"""Parser for Screaming Frog CLI export CSVs.

Expects a per-crawl folder (dropped by a scheduled headless run), produced by,
e.g. on macOS with the app installed at the default location:

  "/Applications/Screaming Frog SEO Spider.app/Contents/MacOS/ScreamingFrogSEOSpiderLauncher" \
    --crawl <url> --headless --output-folder imports/<site>/<date> \
    --export-tabs "Response Codes:All" \
    --bulk-export "Links:All Inlinks" \
    --save-report "Redirects:Redirect Chains"

Verified against a real run of the CLI (not just documentation) -- the three
flags above are genuinely three different CLI mechanisms (--export-tabs,
--bulk-export, --save-report respectively), not all --bulk-export as an
earlier version of this docstring assumed.

Produces some subset of:
  - a "response codes" export (Address, Status Code, Redirect URL) -- the
    Redirect URL column gives the *immediate* target of a 3xx row
  - an "all inlinks" export (Source, Destination) -- used to find which pages
    link to a broken/redirected URL
  - a "redirect chains" export (Address, ... Final Address) -- ONLY lists
    multi-hop chains (2+ redirects); a normal single-hop 301/302 never
    appears here, so this is a supplementary source for the final
    destination after multiple hops, not the primary source of redirects_to

File naming varies across Screaming Frog versions, so files are matched by a
case-insensitive substring in the filename rather than an exact name. Column
names are matched the same way, so minor header differences across versions
don't break parsing.
"""
from __future__ import annotations

import csv
import os
from collections import defaultdict
from dataclasses import dataclass, field
from urllib.parse import urlparse

REDIRECT_STATUS_CODES = {301, 302, 303, 307, 308}

# Site-scale thresholds (total crawled URLs) -- drives how the scheduler phases technical work.
# See workflow.pdf: small sites can fit ALL technical work in month 1; large sites can't, and need
# indexation-blocking issues prioritized while lower-impact work (redirect cleanup) waits for month 2+.
SITE_SCALE_SMALL_MAX = 1_000
SITE_SCALE_MEDIUM_MAX = 50_000

# Indexability Status values (from the standard Response Codes export) that mean a page is being
# kept OUT of the index systemically -- as opposed to a redirect/404/5xx, which already have their
# own issue types. Only meaningful in bulk: one noindex page is a one-off; thousands means a
# robots.txt rule or template bug, not thousands of individual mistakes.
BLOCKING_INDEXABILITY_STATUSES = {"noindex", "blocked by robots.txt", "blocked by x-robots-tag", "canonicalised"}


@dataclass
class CrawlIssueRow:
    issue_type: str  # "404" | "301" | "302" | ... | "5xx"
    url: str
    status_code: int
    redirects_to: str | None = None
    inlinking_urls: list[str] = field(default_factory=list)


@dataclass
class CrawlResult:
    issues: list[CrawlIssueRow]
    total_urls: int  # every row in the Response Codes export, not just issues
    site_scale: str  # "small" | "medium" | "large"
    indexation_blocking: dict[str, list[str]]  # {reason: [urls]}, e.g. {"noindex": [...]}


def classify_site_scale(total_urls: int) -> str:
    if total_urls < SITE_SCALE_SMALL_MAX:
        return "small"
    if total_urls < SITE_SCALE_MEDIUM_MAX:
        return "medium"
    return "large"


def _header_columns(path: str) -> set[str]:
    """Lowercase, trimmed column names from a CSV's header row."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        header = next(reader, [])
    return {c.strip().lower() for c in header}


# Each entry in a Screaming Frog folder is classified by what its header actually
# contains, not by filename -- exports get renamed/re-saved by analysts constantly
# (see O'Reilly's "response_codes_internal_all.csv"), and the folder can also contain
# exports from OTHER tools entirely (e.g. Search Console's own Index Coverage report,
# manually exported alongside the crawl -- see GSC_COVERAGE_COLUMNS below). Matching by
# actual column signature means any file that genuinely IS one of these formats is
# picked up regardless of what it's named or which tool produced it.
#
# Checked in this order because a real All Inlinks export also has a "Status Code"
# column (so a naive "has address + status code" check would misclassify it as
# Response Codes) -- Source/Destination is what's actually unique to it.
ALL_INLINKS_COLUMNS = {"source", "destination"}
# "final address"/"chain type" are unique to Redirect Chains -- a plain Response Codes
# export never has them, even though both share an "address" column.
REDIRECT_CHAINS_COLUMNS = {"final address", "chain type"}
RESPONSE_CODES_COLUMNS = {"address", "status code"}
# Search Console's own "Page indexing" (Index Coverage) report, exported per status
# bucket from the GSC UI (Index > Pages > click a bucket > Export) -- each file is just
# {URL, Last crawled}, with the bucket's REASON coming from the filename itself (GSC
# doesn't repeat it as a column), unlike Screaming Frog's Indexability Status column.
# GSC caps this export at 1000 rows per bucket -- a bucket landing on exactly that count
# is a floor, not the true total (see _parse_gsc_coverage_export).
GSC_COVERAGE_COLUMNS = {"url", "last crawled"}
GSC_COVERAGE_EXPORT_ROW_CAP = 1000


def _classify_csv(path: str) -> str | None:
    columns = _header_columns(path)
    if ALL_INLINKS_COLUMNS <= columns:
        return "all_inlinks"
    if REDIRECT_CHAINS_COLUMNS & columns:
        return "redirect_chains"
    if RESPONSE_CODES_COLUMNS <= columns:
        return "response_codes"
    if GSC_COVERAGE_COLUMNS <= columns:
        return "gsc_coverage"
    return None


def parse_gsc_coverage_export(path: str) -> tuple[str, list[str]]:
    """One Search Console Index Coverage bucket export -> (reason, urls).

    The bucket's reason is the filename itself (e.g. "Blocked by robots.txt.csv"),
    since GSC's export doesn't repeat it as a column. If the row count hits GSC's
    1000-row export cap, the reason is annotated -- that count is Google's own
    floor for this bucket, not necessarily the true total.
    """
    reason = os.path.splitext(os.path.basename(path))[0].strip()
    urls: list[str] = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            url = _col(row, "url")
            if url:
                urls.append(url.strip())
    if len(urls) >= GSC_COVERAGE_EXPORT_ROW_CAP:
        reason = f"GSC: {reason} (export capped at {GSC_COVERAGE_EXPORT_ROW_CAP} -- actual total may be higher)"
    else:
        reason = f"GSC: {reason}"
    return reason, urls


def _col(row: dict, *candidates: str) -> str | None:
    """Fetch a value from a CSV row dict by fuzzy (case-insensitive, trimmed) column name."""
    lookup = {k.strip().lower(): v for k, v in row.items() if k}
    for candidate in candidates:
        if candidate in lookup:
            return lookup[candidate]
    return None


@dataclass
class ResponseCodeRow:
    status_code: int
    redirect_url: str | None = None  # immediate redirect target, if this row is a 3xx
    indexability_status: str | None = None  # e.g. "Noindex", "Blocked by Robots.txt", "" if indexable


def parse_response_codes(path: str) -> dict[str, ResponseCodeRow]:
    """Returns {url: ResponseCodeRow} for every row with a parseable status code.

    The "Redirect URL" column (present on the standard Response Codes export) gives the
    *immediate* redirect target for a 3xx row -- this is the primary source of redirects_to.
    Screaming Frog's separate "Redirect Chains" report only lists multi-hop chains (2+ hops),
    so a normal single-hop 301/302 -- the common case -- never appears there at all.
    """
    result: dict[str, ResponseCodeRow] = {}
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            url = _col(row, "address", "url", "source")
            code_raw = _col(row, "status code", "statuscode")
            if not url or not code_raw:
                continue
            try:
                # Some exports (observed from the desktop GUI, not the CLI the original fixtures
                # were built against) format this column as a float string ("200.0") rather than
                # a plain int ("200") -- int() rejects the former outright, so go through float()
                # first to accept both without silently dropping every single row.
                status_code = int(float(str(code_raw).strip()))
            except ValueError:
                continue
            redirect_url = _col(row, "redirect url", "redirecturl")
            indexability_status = _col(row, "indexability status", "indexabilitystatus")
            result[url.strip()] = ResponseCodeRow(
                status_code=status_code,
                redirect_url=redirect_url.strip() if redirect_url else None,
                indexability_status=indexability_status.strip() if indexability_status else None,
            )
    return result


def parse_all_inlinks(path: str) -> dict[str, list[str]]:
    """Returns {destination_url: [source_urls that link to it]}."""
    result: dict[str, list[str]] = defaultdict(list)
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            source = _col(row, "source")
            destination = _col(row, "destination")
            if not source or not destination:
                continue
            source, destination = source.strip(), destination.strip()
            if source not in result[destination]:
                result[destination].append(source)
    return dict(result)


def parse_redirect_chains(path: str) -> dict[str, str]:
    """Returns {start_url: final_redirect_target} -- multi-hop chains ONLY.

    Screaming Frog's Redirect Chains report excludes plain single-hop redirects,
    so most URLs will have no entry here; import_crawl_folder() falls back to
    the Response Codes export's own "Redirect URL" column for the single-hop case.
    """
    result: dict[str, str] = {}
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        final_col = next((c for c in fieldnames if "final" in c.strip().lower()), None)
        redirect_cols = [c for c in fieldnames if "redirect url" in c.strip().lower()]
        for row in reader:
            start = _col(row, "address")
            if not start:
                continue
            target = None
            if final_col and row.get(final_col):
                target = row[final_col]
            elif redirect_cols:
                for c in reversed(redirect_cols):
                    if row.get(c):
                        target = row[c]
                        break
            if target:
                result[start.strip()] = target.strip()
    return result


def classify(status_code: int) -> str | None:
    if status_code == 404:
        return "404"
    if status_code in REDIRECT_STATUS_CODES:
        return str(status_code)
    if status_code >= 500:
        return "5xx"
    return None  # 2xx/3xx-not-a-redirect/etc. -- not an issue


def _hostname(value: str) -> str:
    """Extract a bare, comparable hostname from either a full URL or a plain domain,
    treating "example.com" and "www.example.com" as the same site. Used to keep external
    URLs (e.g. a LinkedIn profile a crawled page happens to link to, which Screaming Frog
    reports the status of but the client can't fix) out of issue detection entirely --
    without this, a crawl that checks external links generates "fix this" tasks for pages
    the client doesn't own."""
    parsed = urlparse(value if "://" in value else f"//{value}")
    host = (parsed.netloc or parsed.path).split("/")[0].lower()
    return host[4:] if host.startswith("www.") else host


def import_crawl_folder(folder: str, site_domain: str | None = None) -> CrawlResult:
    """Parse whatever recognized exports are present in `folder` into a CrawlResult.

    Every .csv in the folder is classified by its actual column signature (see
    _classify_csv), not its filename or which tool produced it -- so a Screaming Frog
    Response Codes export renamed to anything still gets picked up, and files from a
    different tool entirely (e.g. Search Console's own Index Coverage report, manually
    exported into the same folder) are recognized and folded in too, as long as their
    columns match a known shape. A file matching none of the known shapes is silently
    skipped -- this always processes whatever it *does* recognize rather than erroring
    on the rest of the folder.

    site_domain: when given, URLs whose host doesn't match are excluded from issue
    detection and the total_urls count entirely -- external links a crawl happened to
    check the status of aren't the client's pages to fix. Optional (rather than required)
    so existing single-domain test fixtures don't need updating just to pass one in.
    """
    response_codes_path: str | None = None
    inlinks_path: str | None = None
    redirects_path: str | None = None
    gsc_coverage_paths: list[str] = []

    for name in sorted(os.listdir(folder)):
        if not name.lower().endswith(".csv"):
            continue
        path = os.path.join(folder, name)
        kind = _classify_csv(path)
        if kind == "response_codes" and response_codes_path is None:
            response_codes_path = path
        elif kind == "all_inlinks" and inlinks_path is None:
            inlinks_path = path
        elif kind == "redirect_chains" and redirects_path is None:
            redirects_path = path
        elif kind == "gsc_coverage":
            gsc_coverage_paths.append(path)

    if not response_codes_path:
        raise FileNotFoundError(
            f"No Response Codes export found in {folder}. Expected a CSV with 'Address' and "
            "'Status Code' columns."
        )

    response_by_url = parse_response_codes(response_codes_path)
    if site_domain:
        target_host = _hostname(site_domain)
        response_by_url = {url: r for url, r in response_by_url.items() if _hostname(url) == target_host}
    inlinks_by_destination = parse_all_inlinks(inlinks_path) if inlinks_path else {}
    # multi-hop final destinations; single-hop redirects fall back to each row's own Redirect URL below
    chain_final_targets = parse_redirect_chains(redirects_path) if redirects_path else {}

    issues: list[CrawlIssueRow] = []
    indexation_blocking: dict[str, list[str]] = defaultdict(list)

    for url, response in response_by_url.items():
        issue_type = classify(response.status_code)
        if issue_type:
            issues.append(
                CrawlIssueRow(
                    issue_type=issue_type,
                    url=url,
                    status_code=response.status_code,
                    redirects_to=chain_final_targets.get(url, response.redirect_url),
                    inlinking_urls=inlinks_by_destination.get(url, []),
                )
            )
        elif response.indexability_status and response.indexability_status.strip().lower() in BLOCKING_INDEXABILITY_STATUSES:
            # Not a 404/redirect/5xx (those already have their own issue types above) -- a 200-status
            # page that's still being kept out of the index systemically (noindex, robots.txt, etc).
            indexation_blocking[response.indexability_status.strip()].append(url)

    # Any Search Console Index Coverage buckets found in the folder are kept as their
    # own distinct reasons (prefixed "GSC: ") rather than merged into the matching SF
    # bucket by name -- the two are different measurements (Google's actual index vs.
    # Screaming Frog's simulated crawl) and can legitimately disagree; collapsing them
    # into one count would hide that discrepancy instead of surfacing it.
    for path in gsc_coverage_paths:
        reason, urls = parse_gsc_coverage_export(path)
        if site_domain:
            urls = [u for u in urls if _hostname(u) == target_host]
        if urls:
            indexation_blocking[reason].extend(urls)

    total_urls = len(response_by_url)
    return CrawlResult(
        issues=issues,
        total_urls=total_urls,
        site_scale=classify_site_scale(total_urls),
        indexation_blocking=dict(indexation_blocking),
    )
