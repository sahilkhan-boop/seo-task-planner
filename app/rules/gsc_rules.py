"""GSC existing-page-optimization rule engine.

Three position-based tiers, checked in that order for each page (a page only
ever falls into one -- see test_a_page_only_ever_falls_into_one_tier),
ordered by effort vs. impact so the analyst always works the highest-leverage/
lowest-effort opportunity first (see app/scheduling/timeline.py's CATEGORY_TIER
for how this maps onto calendar priority):

  (wasted_impressions -- real visibility converting to essentially no clicks,
  regardless of position -- used to be a live-sync Tier 0 check here, checked
  before the position tiers below. It's since moved to the same monthly,
  ranked, non-repeating content-plan cycle page_optimization uses -- see
  services.py's _ranked_wasted_impression_pages and content_rules.py's
  generate_content_plan -- since "pace it out gradually across several
  months" is a content-plan-calendar concept a live per-sync check has no
  way to express. Running both side by side would double-flag the same
  pages under two mechanisms, so this file no longer generates it at all.)
  Tier 1 - meta_tag_reoptimization (easiest, do first): page already ranks
      position 1-5 -- the hard part (reaching page 1) is done -- but CTR is
      below the position-band benchmark. Fix is copy-only: rewrite the title
      tag/meta description. No new content, fastest lift, so it goes first.
  Tier 2 - content_expansion: page ranks position 5-15 AND has real
      non-branded query demand (impressions) it isn't already capturing.
      Fix is adding on-page content/sections that address those specific
      queries -- more effort than tier 1 (real content work), but still
      targeted at an existing, already-ranking page rather than a new one.
  Tier 3 - ctr_optimization (gradual catch-all): pages beyond position 15
      with meaningful impressions. Real search demand exists but ranking is
      too far back for CTR fixes to matter yet -- broader/opportunistic
      on-page SEO work, worked gradually once tiers 1-2 are handled, ranked
      by impressions (opportunity size) via the severity scale below.

Branded vs. non-branded is a simple case-insensitive substring match against
the site's configured brand terms (Site.brand_terms), or a regex
(Site.brand_regex) when one's configured -- no NLP, deliberately, same
"simple explicit rule over guessing" philosophy as the rest of this rule
engine.

Each tier has a total-campaign threshold (CAMPAIGN_TASK_BUDGET in
crawl_rules.py) -- a smart analyst doesn't open 245 separate meta-tag
tickets, and they don't want the same-looking "rewrite title/meta for N
pages" ticket reappearing a dozen times either. Below the threshold, nothing
changes: one task per page. At or beyond it, every page in that tier
collapses into ONE consolidated task covering all of them (see
crawl_rules.batch_items).
"""
from __future__ import annotations

import re

from app.rules.content_rules import prompt_analysis_cross_reference
from app.rules.crawl_rules import CAMPAIGN_TASK_BUDGET, GeneratedTask, batch_items

MIN_IMPRESSIONS = 20  # ignore low-volume noise -- a 0/1 click page with 3 impressions isn't a signal
NON_BRANDED_OPPORTUNITY_IMPRESSIONS = 100  # tier 2: minimum non-branded-query impressions to act on
CATCH_ALL_MIN_IMPRESSIONS = 300  # tier 3: minimum impressions for a beyond-position-15 page to be worth touching
MAX_QUERIES_LISTED = 5  # cap how many queries a single tier-2 (unbatched) task names in its description


def position_bucket(position: float) -> str | None:
    if position <= 5:
        return "pos_1_5"
    if position <= 15:
        return "pos_5_15"
    return None  # beyond position 15 -- handled by the tier-3 catch-all instead of a position bucket


def _severity_for_impressions(impressions: int) -> str:
    if impressions >= 500:
        return "high"
    if impressions >= 100:
        return "medium"
    return "low"


def _compile_brand_regex(pattern: str | None) -> re.Pattern | None:
    """Invalid regex is ignored (falls back to the plain brand_terms substring match)
    rather than erroring the whole sync -- same "a typo in an optional customization
    field shouldn't block real data" reasoning as services.py's GSC/GA4 filter regexes."""
    if not pattern or not pattern.strip():
        return None
    try:
        return re.compile(pattern)
    except re.error:
        return None


def is_branded_query(query: str, brand_terms: list[str], brand_regex: re.Pattern | None = None) -> bool:
    """brand_regex (Site.brand_regex, pre-compiled) takes priority when set -- a plain
    comma-separated term list can't cleanly express spelling variants, apostrophe/
    hyphen variants, or sub-brand names the way a regex can. Falls back to the
    original case-insensitive substring match against brand_terms otherwise. No
    brand terms AND no regex configured -> nothing is branded, so tier 2 just sees
    every query as non-branded (a reasonable default: without either, the app can't
    tell branded from non-branded, so it doesn't pretend to)."""
    if brand_regex is not None:
        return bool(brand_regex.search(query))
    q = query.lower()
    return any(term in q for term in brand_terms)


def _native_gsc_row(c: dict) -> dict:
    """A candidate's full native-GSC-shaped row (Clicks/Impressions/CTR/Position) --
    every task builder below sets this as that page's url_details entry, single-page
    or batched, so the export can populate every native column for every row instead
    of leaving Clicks/Position blank and only ever filling in whichever ONE metric
    this particular check happens to key off of. This is what makes the export
    actually look like a real Search Console export instead of a partial one."""
    return {
        "clicks": c["clicks"], "impressions": c["impressions"], "ctr": c["ctr"], "position": c["position"],
    }


def _task_for_meta_tag(c: dict) -> GeneratedTask:
    return GeneratedTask(
        source="gsc",
        category="meta_tag_reoptimization",
        title=f"Rewrite title/meta: {c['page']}",
        description=(
            f"Ranks position {c['position']:.1f} but CTR is {c['ctr'] * 100:.1f}% vs a "
            f"{c['target'] * 100:.1f}% benchmark. Rewrite the title tag and meta description to be more "
            "compelling -- copy-only fix, no content work needed."
        ),
        affected_urls=[c["page"]],
        severity=c["severity"],
        metric_actual=c["ctr"],
        metric_benchmark=c["target"],
        effort_tier="low",
        url_details={c["page"]: _native_gsc_row(c)},
    )


def _task_for_meta_tag_batch(batch: list[dict]) -> GeneratedTask:
    """Explains the work in aggregate, not page-by-page -- the per-page CTR/benchmark
    numbers already live in affected_urls' export (one row per page), so repeating
    them as text here would just be a long, expensive-to-render duplicate."""
    high_count = sum(1 for c in batch if c["severity"] == "high")
    description = (
        f"{len(batch)} page-1 pages batched into one pass, all ranking well but under-performing on "
        f"CTR ({high_count} high-impression) against a {batch[0]['target'] * 100:.1f}% benchmark. "
        "Rewrite title/meta for each -- export this task for the full list with each page's own actual CTR."
    )
    return GeneratedTask(
        source="gsc",
        category="meta_tag_reoptimization",
        title=f"Rewrite title/meta for {len(batch)} page-1 pages",
        description=description,
        affected_urls=[c["page"] for c in batch],
        severity="high" if any(c["severity"] == "high" for c in batch) else "medium",
        # Same target for the whole batch (one CTR benchmark per position bucket) --
        # metric_benchmark can just carry it, but each page's own actual row still
        # needs per-url tracking since that's what varies. See _write_gsc_ga4_task_row.
        metric_benchmark=batch[0]["target"],
        effort_tier="low",
        url_details={c["page"]: _native_gsc_row(c) for c in batch},
    )


def _task_for_content_expansion(c: dict) -> GeneratedTask:
    return GeneratedTask(
        source="gsc",
        category="content_expansion",
        title=f"Expand content for query gap: {c['page']}",
        description=(
            f"{prompt_analysis_cross_reference()}\n\n"
            f"Ranks position {c['position']:.1f} but misses {c['non_branded_impressions']:.0f} "
            f"non-branded impressions from queries it doesn't address yet: {c['query_list']}. Add "
            "sections covering these queries rather than creating a new page."
        ),
        affected_urls=[c["page"]],
        severity=c["severity"],
        metric_actual=c["non_branded_impressions"],
        metric_benchmark=NON_BRANDED_OPPORTUNITY_IMPRESSIONS,
        effort_tier="medium",
        url_details={c["page"]: _native_gsc_row(c)},
    )


def _task_for_content_expansion_batch(batch: list[dict]) -> GeneratedTask:
    """Explains the work in aggregate, not page-by-page -- see _task_for_meta_tag_batch."""
    total_impressions = sum(c["non_branded_impressions"] for c in batch)
    description = (
        f"{prompt_analysis_cross_reference()}\n\n"
        f"{len(batch)} position 5-15 pages batched into one pass, each missing real non-branded query "
        f"demand ({total_impressions:.0f} impressions total, each page clearing the "
        f"{NON_BRANDED_OPPORTUNITY_IMPRESSIONS}-impression opportunity floor). Export this task for each "
        "page's specific query gaps."
    )
    return GeneratedTask(
        source="gsc",
        category="content_expansion",
        title=f"Expand content for {len(batch)} pages with query gaps",
        description=description,
        affected_urls=[c["page"] for c in batch],
        severity="high" if any(c["severity"] == "high" for c in batch) else "medium",
        metric_benchmark=NON_BRANDED_OPPORTUNITY_IMPRESSIONS,
        effort_tier="medium",
        url_details={c["page"]: _native_gsc_row(c) for c in batch},
    )


def _task_for_ctr_optimization(c: dict) -> GeneratedTask:
    return GeneratedTask(
        source="gsc",
        category="ctr_optimization",
        title=f"Improve ranking & CTR: {c['page']}",
        description=(
            f"{c['impressions']} impressions at position {c['position']:.1f} -- real demand, but too far "
            "back for a CTR fix to matter yet. Broader on-page SEO/content-depth work, once higher-"
            "leverage pages are handled."
        ),
        affected_urls=[c["page"]],
        severity=c["severity"],
        metric_actual=c["impressions"],
        metric_benchmark=CATCH_ALL_MIN_IMPRESSIONS,
        effort_tier="medium",
        url_details={c["page"]: _native_gsc_row(c)},
    )


def _task_for_ctr_optimization_batch(batch: list[dict]) -> GeneratedTask:
    """Explains the work in aggregate, not page-by-page -- see _task_for_meta_tag_batch."""
    total_impressions = sum(c["impressions"] for c in batch)
    description = (
        f"{len(batch)} beyond-position-15 pages batched into one gradual pass ({total_impressions:,} "
        f"impressions total, each clearing the {CATCH_ALL_MIN_IMPRESSIONS}-impression floor) -- real "
        "demand, but too far back for a quick CTR fix. Export this task for the full list."
    )
    return GeneratedTask(
        source="gsc",
        category="ctr_optimization",
        title=f"Improve ranking & CTR for {len(batch)} pages",
        description=description,
        affected_urls=[c["page"] for c in batch],
        severity="high" if any(c["severity"] == "high" for c in batch) else "medium",
        metric_benchmark=CATCH_ALL_MIN_IMPRESSIONS,
        effort_tier="medium",
        url_details={c["page"]: _native_gsc_row(c) for c in batch},
    )


def generate_gsc_tasks(
    page_rows: list[dict],
    query_rows: list[dict],
    benchmarks_by_segment: dict[str, float],
    brand_terms: list[str] | None = None,
    brand_regex: str | None = None,
) -> list[GeneratedTask]:
    """page_rows: [{"page":..., "clicks":..., "impressions":..., "ctr":..., "position":...}, ...]
    query_rows: same shape but dimensioned by (page, query) -- see
        ingestion/gsc_sync.py's fetch_page_query_analytics. Used for tier 2's
        branded/non-branded split, and now also to classify tier 1 pages the same way.
    benchmarks_by_segment: {"pos_1_5": 0.18} (site's configured CTR Benchmark for the
        tier-1 position band; tiers 2-3 use fixed opportunity thresholds above instead
        of a configurable benchmark, same as MIN_IMPRESSIONS not being one). May also
        carry "high_impression_wasted" -- unused here since that check moved to
        services.py's _ranked_wasted_impression_pages, but still read from the same
        Benchmark row so the Benchmarks-page value stays the single source of truth
        for both the (retired) live check's old shape and the new one. Optionally
        also {"branded_pos_1_5": ..., "non_branded_pos_1_5": ...} -- branded and
        non-branded searches have very different realistic CTR expectations even at
        the same position (a branded query is close to navigational, a non-branded
        one is competing on the merits of the title/meta alone), so a page whose
        pos_1_5 impressions are dominated by one or the other gets checked against
        that specific segment instead, when configured. Falls back to the flat
        "pos_1_5" segment for any page a segmented benchmark isn't configured for
        (or that has no query-level data to classify by), so existing sites that
        haven't opted into the split keep working exactly as before.
    brand_terms: the site's configured brand terms (Site.brand_terms.split(",")), for
        tier 2's (and now tier 1's) branded/non-branded split.
    brand_regex: the site's configured brand regex (Site.brand_regex), takes priority
        over brand_terms when set -- see is_branded_query.
    """
    brand_terms = [t.strip().lower() for t in (brand_terms or []) if t.strip()]
    compiled_brand_regex = _compile_brand_regex(brand_regex)

    queries_by_page: dict[str, list[dict]] = {}
    for row in query_rows:
        queries_by_page.setdefault(row["page"], []).append(row)

    meta_tag_candidates: list[dict] = []
    content_expansion_candidates: list[dict] = []
    ctr_optimization_candidates: list[dict] = []

    for row in page_rows:
        if row["impressions"] < MIN_IMPRESSIONS:
            continue
        page = row["page"]
        bucket = position_bucket(row["position"])
        severity = _severity_for_impressions(row["impressions"])

        if bucket == "pos_1_5":
            segment = "pos_1_5"
            page_queries = queries_by_page.get(page, [])
            total_page_impressions = sum(q["impressions"] for q in page_queries)
            if total_page_impressions:
                branded_impressions = sum(
                    q["impressions"] for q in page_queries
                    if is_branded_query(q["query"], brand_terms, compiled_brand_regex)
                )
                is_branded_page = branded_impressions / total_page_impressions > 0.5
                candidate_segment = f"{'branded' if is_branded_page else 'non_branded'}_pos_1_5"
                if candidate_segment in benchmarks_by_segment:
                    segment = candidate_segment
            target = benchmarks_by_segment.get(segment)
            if target is not None and row["ctr"] < target:
                meta_tag_candidates.append(
                    {"page": page, "position": row["position"], "impressions": row["impressions"],
                     "clicks": row["clicks"], "ctr": row["ctr"], "target": target, "severity": severity}
                )
            continue

        if bucket == "pos_5_15":
            candidates = [
                q for q in queries_by_page.get(page, [])
                if not is_branded_query(q["query"], brand_terms, compiled_brand_regex)
            ]
            total_non_branded_impressions = sum(q["impressions"] for q in candidates)
            if total_non_branded_impressions >= NON_BRANDED_OPPORTUNITY_IMPRESSIONS:
                top_queries = sorted(candidates, key=lambda q: -q["impressions"])[:MAX_QUERIES_LISTED]
                query_list = ", ".join(f'"{q["query"]}" ({q["impressions"]} impr.)' for q in top_queries)
                content_expansion_candidates.append(
                    {"page": page, "position": row["position"], "impressions": row["impressions"],
                     "clicks": row["clicks"], "ctr": row["ctr"],
                     "non_branded_impressions": total_non_branded_impressions, "query_list": query_list,
                     "severity": severity}
                )
            continue

        # bucket is None -- beyond position 15: gradual catch-all, ranked by impressions via severity.
        if row["impressions"] >= CATCH_ALL_MIN_IMPRESSIONS:
            ctr_optimization_candidates.append(
                {"page": page, "position": row["position"], "impressions": row["impressions"],
                 "clicks": row["clicks"], "ctr": row["ctr"], "severity": severity}
            )

    tasks: list[GeneratedTask] = []
    for batch in batch_items(meta_tag_candidates, CAMPAIGN_TASK_BUDGET["meta_tag_reoptimization"]):
        tasks.append(_task_for_meta_tag(batch[0]) if len(batch) == 1 else _task_for_meta_tag_batch(batch))
    for batch in batch_items(content_expansion_candidates, CAMPAIGN_TASK_BUDGET["content_expansion"]):
        tasks.append(_task_for_content_expansion(batch[0]) if len(batch) == 1 else _task_for_content_expansion_batch(batch))
    for batch in batch_items(ctr_optimization_candidates, CAMPAIGN_TASK_BUDGET["ctr_optimization"]):
        tasks.append(_task_for_ctr_optimization(batch[0]) if len(batch) == 1 else _task_for_ctr_optimization_batch(batch))

    return tasks
