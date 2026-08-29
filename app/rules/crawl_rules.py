"""Crawl issue -> Task rule engine.

Pure functions: given crawl issues (and, in later phases, metric snapshots +
benchmarks), return GeneratedTask objects. No DB/HTTP access here so these
are trivially unit-testable -- see tests/test_crawl_rules.py.

A smart analyst doesn't open 579 separate tickets for the same kind of
redirect cleanup -- and they don't want the same-looking ticket to reappear
a dozen times either. CAMPAIGN_TASK_BUDGET encodes that: each data-driven
category has a threshold for the *whole* campaign (not per month), and once
the real issue count crosses it, every issue in that category collapses
into ONE consolidated task instead of one task per issue -- so the plan
stays something one analyst can actually work through in 6 months, without
a wall of individual tickets or a string of near-identical batched ones. See
app/scheduling/timeline.py for how each threshold was sized per category
(effort + priority together, not just tier order).

Batched task descriptions deliberately explain the WORK (what/why/how) rather
than listing every affected URL inline -- that data already lives in full in
affected_urls and the CSV export (one row per URL, in the source tool's own
native column shape), and repeating it as text in the description was both
redundant and, at real volume, a genuine rendering-cost problem (one task's
description reached 28,000+ characters before this).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.ingestion.screaming_frog import CrawlIssueRow

# A redirect/404 with this many or more internal links pointing at it is high
# severity -- it's actively wasting crawl budget / link equity across the site,
# not just a one-off broken link.
HIGH_INLINK_THRESHOLD = 5

# Per-category threshold, for the whole campaign (not per month) -- see timeline.py's
# docstring for the full reasoning (effort + priority together, not just tier order).
# Below this count, one task per issue, same as always. At or beyond it, every issue
# in that category collapses into ONE consolidated task (see batch_items below) --
# not several similarly-sized tasks, which would land on different days across the
# campaign and read as the same task repeating over and over (a real analyst doesn't
# open "Rewrite title/meta for 7 pages" a dozen times, they do one pass covering all
# of them). Centralized here (rather than split across crawl_rules/gsc_rules/ga4_rules)
# so there's one place that states each category's collapse-to-one-task threshold.
# indexation_blocking, server_error, content_creation, and page_optimization are
# deliberately absent -- see timeline.py's docstring for why each is left uncapped.
# wasted_impressions moved here too (see gsc_rules.py's module docstring) -- it's
# now a monthly content-plan task ranked/sliced like page_optimization, not a
# live-sync check with a whole-campaign collapse threshold.
CAMPAIGN_TASK_BUDGET = {
    "404_fix": 15,
    "redirect_inlink_update": 15,
    "meta_tag_reoptimization": 25,
    "content_expansion": 20,
    "ctr_optimization": 15,
    "ui_ux_review": 10,
    "high_exit_rate": 8,
    "low_mobile_share": 8,
    "low_key_events": 8,
}


@dataclass
class GeneratedTask:
    source: str
    category: str
    title: str
    description: str
    affected_urls: list[str] = field(default_factory=list)
    severity: str = "medium"
    metric_actual: float | None = None
    metric_benchmark: float | None = None
    effort_tier: str = "medium"
    # Per-URL extras that a flat affected_urls list can't carry -- see Task.url_details
    # for the two shapes this takes (consolidated technical_audit vs. batched GSC/GA4).
    # None (not {}) is the "nothing to add" default, same as the other optional fields.
    url_details: dict[str, dict] | None = None


def batch_items(items: list, budget: int) -> list[list]:
    """Below `budget` items, nothing is grouped at all -- one task per item, same as
    always. At or beyond it, everything collapses into a SINGLE consolidated batch
    covering every item, rather than splitting into several similarly-sized batches
    that would each get their own task, land on different days across the campaign,
    and read as the same task repeating (e.g. "Rewrite title/meta for 7 page-1
    pages" showing up half a dozen times) -- same "one consolidated task, not N
    near-identical ones" reasoning already used for indexation_blocking and the GA4
    systemic-housekeeping collapse. The task description built from this batch
    explains the work in aggregate, not item-by-item (see the per-category
    _task_for_*_batch builders below), but the task's affected_urls always keeps the
    full list, so no item is ever silently dropped -- it's exportable even though it's
    not printed in the description."""
    if not items:
        return []
    if len(items) <= max(1, budget):
        return [[item] for item in items]
    return [items]


def _task_for_404(issue: CrawlIssueRow) -> GeneratedTask:
    inlinks = issue.inlinking_urls
    if inlinks:
        description = (
            f"{issue.url} returns a 404 with {len(inlinks)} internal link(s) still pointing at it. "
            "Update or remove each one -- export this task for the exact list."
        )
        severity = "high" if len(inlinks) >= HIGH_INLINK_THRESHOLD else "medium"
    else:
        description = (
            f"{issue.url} returns a 404 with no internal links pointing at it. Check the XML sitemap "
            "and backlinks before deciding whether to redirect it or leave it 404."
        )
        severity = "medium"
    title_count = f"{len(inlinks)} inlinking page(s)" if inlinks else "no inlinks -- check sitemap/backlinks"
    return GeneratedTask(
        source="crawl",
        category="404_fix",
        title=f"Fix 404: {issue.url} ({title_count})",
        description=description,
        affected_urls=[issue.url, *inlinks],
        severity=severity,
        metric_actual=issue.status_code,
        metric_benchmark=200,
        effort_tier="low" if not inlinks else "medium",
    )


def _task_for_404_batch(batch: list[CrawlIssueRow]) -> GeneratedTask:
    """One task covering several 404s at once -- used only once the real count
    exceeds CAMPAIGN_TASK_BUDGET; a single 404 never goes through this path.

    Deliberately doesn't list individual URLs in the description -- at batch volume
    that's a wall of text that's expensive to render and adds nothing a spreadsheet
    doesn't do better (see export.csv, which already has the full affected_urls list,
    one row per URL, in the source tool's own native column shape). The description's
    job is explaining the WORK, not duplicating the data."""
    total_inlinks = sum(len(i.inlinking_urls) for i in batch)
    high_impact = sum(1 for i in batch if len(i.inlinking_urls) >= HIGH_INLINK_THRESHOLD)
    description = (
        f"{len(batch)} 404s batched into one pass ({high_impact} high-impact). Update or remove the "
        "internal links pointing at each -- export this task for the full list."
    )
    return GeneratedTask(
        source="crawl",
        category="404_fix",
        title=f"Fix {len(batch)} 404s ({total_inlinks} inlinking pages total)",
        description=description,
        affected_urls=[i.url for i in batch],
        severity="high" if high_impact else "medium",
        effort_tier="medium",
    )


def _task_for_redirect(issue: CrawlIssueRow) -> GeneratedTask:
    inlinks = issue.inlinking_urls
    target = issue.redirects_to or "(final destination not detected -- check the Redirect Chains export)"
    if inlinks:
        description = (
            f"{issue.url} is a {issue.status_code} redirect to {target}, with {len(inlinks)} internal "
            f"link(s) still pointing at the old URL. Update each to link directly to {target}."
        )
        severity = "high" if len(inlinks) >= HIGH_INLINK_THRESHOLD else "medium"
    else:
        description = (
            f"{issue.url} is a {issue.status_code} redirect to {target} with no internal links pointing "
            "at it. Low priority -- just confirm no external backlinks still reference it."
        )
        severity = "low"
    title_count = f"{len(inlinks)} page(s)" if inlinks else "no inlinking pages"
    return GeneratedTask(
        source="crawl",
        category="redirect_inlink_update",
        title=f"Update internal links on {title_count} through {issue.status_code} redirect: {issue.url}",
        description=description,
        affected_urls=[issue.url, *inlinks],
        severity=severity,
        metric_actual=issue.status_code,
        metric_benchmark=200,
        effort_tier="low",
    )


def _task_for_redirect_batch(batch: list[CrawlIssueRow]) -> GeneratedTask:
    """One task covering several redirects at once -- same batching reasoning as
    _task_for_404_batch (including not listing individual URLs -- see its docstring),
    only used once the real count exceeds the budget."""
    total_inlinks = sum(len(i.inlinking_urls) for i in batch)
    high_impact = sum(1 for i in batch if len(i.inlinking_urls) >= HIGH_INLINK_THRESHOLD)
    description = (
        f"{len(batch)} redirects batched into one pass ({high_impact} high-impact). Update the internal "
        "links to point directly at each final destination -- export this task for the full list."
    )
    return GeneratedTask(
        source="crawl",
        category="redirect_inlink_update",
        title=f"Update internal links for {len(batch)} redirects ({total_inlinks} inlinking pages total)",
        description=description,
        affected_urls=[i.url for i in batch],
        severity="high" if high_impact else "medium",
        effort_tier="medium",
    )


def _task_for_server_error(issue: CrawlIssueRow) -> GeneratedTask:
    inlinks = issue.inlinking_urls
    return GeneratedTask(
        source="crawl",
        category="server_error",
        title=f"Investigate server error ({issue.status_code}): {issue.url}",
        description=(
            f"{issue.url} returned a {issue.status_code} server error during the crawl. "
            f"{len(inlinks)} internal page(s) link to it. Server errors can silently drop a page "
            "out of the index -- investigate immediately."
        ),
        affected_urls=[issue.url, *inlinks],
        severity="high",
        metric_actual=issue.status_code,
        metric_benchmark=200,
        effort_tier="high",
    )


def generate_crawl_tasks(issues: list[CrawlIssueRow]) -> list[GeneratedTask]:
    """server_error is never batched -- it's rare on a healthy site and too urgent
    to summarize away; every one gets investigated on its own. 404_fix and
    redirect_inlink_update batch once their real count exceeds CAMPAIGN_TASK_BUDGET."""
    fours = [i for i in issues if i.issue_type == "404"]
    redirects = [i for i in issues if i.issue_type in {"301", "302", "303", "307", "308"}]
    errors = [i for i in issues if i.issue_type == "5xx"]

    tasks: list[GeneratedTask] = [_task_for_server_error(i) for i in errors]

    for batch in batch_items(fours, CAMPAIGN_TASK_BUDGET["404_fix"]):
        tasks.append(_task_for_404(batch[0]) if len(batch) == 1 else _task_for_404_batch(batch))
    for batch in batch_items(redirects, CAMPAIGN_TASK_BUDGET["redirect_inlink_update"]):
        tasks.append(_task_for_redirect(batch[0]) if len(batch) == 1 else _task_for_redirect_batch(batch))

    return tasks


def _task_for_indexation_blocking(reason: str, urls: list[str]) -> GeneratedTask:
    description = (
        f"{len(urls)} page(s) are non-indexable due to '{reason}'. At this volume this is almost "
        "certainly a systemic cause (a robots.txt rule, a template-level noindex tag, a CMS "
        "misconfiguration) rather than individual mistakes -- investigate the root cause first, don't "
        "fix these one by one. Export this task for the full list of affected URLs."
    )
    return GeneratedTask(
        source="crawl",
        category="indexation_blocking",
        title=f"Investigate {len(urls)} pages blocked from indexing ({reason})",
        description=description,
        # Full list, not just a sample -- now that the description doesn't repeat any
        # of it inline, there's no reason to cap what's actually stored/exportable.
        affected_urls=urls,
        severity="high",
        effort_tier="high",
    )


def generate_indexation_blocking_tasks(indexation_blocking: dict[str, list[str]]) -> list[GeneratedTask]:
    """One aggregate task per blocking reason (never one task per URL, and never
    batched further -- there are only ever a handful of distinct reasons, so this
    never explodes with site size the way per-URL categories do)."""
    return [_task_for_indexation_blocking(reason, urls) for reason, urls in indexation_blocking.items() if urls]


# Fixed display order for consolidate_technical_tasks -- same priority reasoning as
# timeline.py's CATEGORY_TIER (indexation-blocking > server errors > redirect cleanup
# > 404s), just rendered as section order in one task's description instead of
# separate calendar slots.
_TECHNICAL_SECTION_ORDER = ["indexation_blocking", "server_error", "redirect_inlink_update", "404_fix"]
_TECHNICAL_LABELS = {
    "indexation_blocking": "indexation-blocking reasons",
    "server_error": "server errors",
    "redirect_inlink_update": "redirects",
    "404_fix": "404s",
}


def consolidate_technical_tasks(tasks: list[GeneratedTask]) -> list[GeneratedTask]:
    """Merge every crawl-side technical task (404s, redirects, indexation-blocking,
    server errors) into ONE "Technical Audit" task, for analysts who'd rather work the
    whole crawl backlog in one focused pass than juggle several separate crawl tickets.
    This is opt-in per campaign (Campaign.consolidate_technical_tasks, default True) --
    package/workflow shape is the analyst's call, not a fixed platform rule -- so this
    is applied by the caller (app/services.py), not baked into generate_crawl_tasks/
    generate_indexation_blocking_tasks themselves.

    Unlike category-level batching, this doesn't preserve server_error's "never
    batched, always individually urgent" property -- that's a deliberate trade the
    analyst is opting into by choosing "one go," not an oversight.
    """
    if len(tasks) <= 1:
        return tasks  # nothing to consolidate

    by_category: dict[str, list[GeneratedTask]] = {}
    for t in tasks:
        by_category.setdefault(t.category, []).append(t)

    all_urls: list[str] = []
    seen_urls: set[str] = set()
    counted_categories: list[str] = []
    # Each url's ORIGINAL sub-issue (404_fix/redirect_inlink_update/server_error/
    # indexation_blocking) and severity -- otherwise every one of these rows would
    # export as category="technical_audit"/severity="high" with no way to tell which
    # of the 4 checks actually flagged it, or to cross-reference back to the crawl.
    url_details: dict[str, dict] = {}
    for category in _TECHNICAL_SECTION_ORDER + [c for c in by_category if c not in _TECHNICAL_SECTION_ORDER]:
        group = by_category.get(category, [])
        if group:
            counted_categories.append(f"{len(group)} {_TECHNICAL_LABELS.get(category, category)}")
        for t in group:
            for url in t.affected_urls:
                if url not in seen_urls:
                    seen_urls.add(url)
                    all_urls.append(url)
                    url_details[url] = {"category": t.category, "severity": t.severity}

    description = (
        f"Every technical issue this crawl found -- {', '.join(counted_categories)} -- fixed here in "
        "one consolidated pass instead of separate tickets. Export this task for the full list of "
        "affected URLs."
    )

    return [
        GeneratedTask(
            source="crawl",
            category="technical_audit",
            title="Technical Audit",
            description=description,
            affected_urls=all_urls,
            severity="high" if any(t.severity == "high" for t in tasks) else "medium",
            effort_tier="high",
            url_details=url_details,
        )
    ]
