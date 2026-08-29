"""GA4 engagement/conversion rule engine.

Four checks per page, each against its own configurable Benchmark (see
app/routers/benchmarks.py's DEFAULT_BENCHMARKS). A page can trigger more than
one -- a low-engagement, high-bounce, mobile-weak page with no key events is a
real (if unfortunate) combination, not a rule-engine bug.

Same noise floor as GSC's rule engine (app/rules/gsc_rules.py): below
MIN_SESSIONS a page's numbers are too volatile to act on, so it's skipped
rather than generating a task off statistical noise.

`ui_ux_review` (the engagement check) is gated tighter than the other three:
it only fires for pages that are already *working* traffic-wise -- real
sessions AND real users -- but still failing to engage once people land. That
combination is what actually points at a UI/UX or page-speed problem rather
than a content-fit problem.

The other three (exit_rate, mobile_share, key_events) get a second, different
kind of gate first: if one of them fires on most of the site's pages, that's
not N separate page problems, it's one systemic cause -- a GA4 tracking gap,
a site-wide template issue -- exactly the same "collapse to one investigative
task, don't fix these one by one" reasoning app/rules/crawl_rules.py already
applies to indexation-blocking at scale.

Below the systemic threshold, and for ui_ux_review (which has no systemic
concept -- it's already gated to real, individually-meaningful pages), each
category still has a total-campaign threshold (CAMPAIGN_TASK_BUDGET in
crawl_rules.py): a smart analyst doesn't open 57 separate UI/UX tickets, and
they don't want the same-looking "UI/UX review for N pages" ticket
reappearing over and over either. Below the threshold, nothing changes: one
task per page. At or beyond it, every page in that category collapses into
ONE consolidated task covering all of them (see crawl_rules.batch_items).
"""
from __future__ import annotations

from app.rules.crawl_rules import CAMPAIGN_TASK_BUDGET, GeneratedTask, batch_items

MIN_SESSIONS = 20
HIGH_TRAFFIC_SESSIONS = 500  # ui_ux_review's extra gate -- "highly visited", not just above the noise floor
MIN_ACTIVE_USERS_FOR_UX_REVIEW = 100  # "good users", not just a handful of sessions from a few people

# If a housekeeping check (exit rate / mobile share / key events) fires on this fraction
# or more of evaluated pages, treat it as one systemic finding instead of one task per page.
SYSTEMIC_THRESHOLD_FRACTION = 0.3
# ...but only once there's a real sample to judge that fraction from -- a 2-of-3 "majority"
# on a tiny site isn't a systemic signal, it's just a small site.
MIN_EVALUATED_PAGES_FOR_SYSTEMIC_CHECK = 20

_HOUSEKEEPING_CHECKS = {
    "high_exit_rate": {
        "title": "Investigate site-wide high exit rate",
        "cause": "a broken/slow shared template element, a bad global CTA, or a tracking issue rather than N separate page problems",
    },
    "low_mobile_share": {
        "title": "Investigate site-wide low mobile traffic share",
        "cause": "a mobile usability/performance issue affecting the whole site (or its templates) rather than N separate pages",
    },
    "low_key_events": {
        "title": "Investigate site-wide missing key events",
        "cause": "GA4 key events likely aren't configured/tagged correctly site-wide, rather than N separate pages failing to convert",
    },
}

_BATCH_TITLE_PREFIX = {
    "ui_ux_review": "UI/UX review",
    "high_exit_rate": "Reduce exits",
    "low_mobile_share": "Check mobile experience",
    "low_key_events": "Improve conversion path",
}

# Which direction counts as failing, per category -- needed to phrase the batch
# description's benchmark sentence correctly (ui_ux_review/low_mobile_share/
# low_key_events fail when actual is too LOW, high_exit_rate fails when actual is
# too HIGH). See generate_ga4_tasks for where each direction is actually enforced;
# this is just for wording the aggregate description below.
_BATCH_DIRECTION = {
    "ui_ux_review": "below",
    "high_exit_rate": "above",
    "low_mobile_share": "below",
    "low_key_events": "below",
}


def _severity_for_sessions(sessions: int) -> str:
    if sessions >= 500:
        return "high"
    if sessions >= 100:
        return "medium"
    return "low"


def _native_ga4_row(row: dict) -> dict:
    """A page's full native-GA4-shaped row (Sessions/Active users/Engagement rate/
    Bounce rate) -- every task below sets this as that page's url_details entry
    (whether it stays single-page or later gets collapsed/batched), so the export
    can populate every native column for every row instead of leaving Sessions/
    Active users blank and only ever filling in whichever ONE metric this
    particular check happens to key off of. This is what makes the export
    actually look like a real GA4 "Pages and screens" export instead of a
    partial one."""
    return {
        "sessions": row["sessions"], "active_users": row["active_users"],
        "engagement_rate": row["engagement_rate"], "bounce_rate": row["bounce_rate"],
    }


def _collapse_if_systemic(category: str, per_page_tasks: list[GeneratedTask], evaluated_pages: int) -> list[GeneratedTask]:
    if (
        evaluated_pages < MIN_EVALUATED_PAGES_FOR_SYSTEMIC_CHECK
        or len(per_page_tasks) < SYSTEMIC_THRESHOLD_FRACTION * evaluated_pages
    ):
        return per_page_tasks

    meta = _HOUSEKEEPING_CHECKS[category]
    affected = [t.affected_urls[0] for t in per_page_tasks]
    benchmark = per_page_tasks[0].metric_benchmark
    benchmark_phrase = f" (vs a {benchmark * 100:.1f}% benchmark)" if benchmark is not None else ""
    description = (
        f"{len(affected)} of {evaluated_pages} evaluated pages ({len(affected) / evaluated_pages * 100:.0f}%) "
        f"fail this check{benchmark_phrase} -- almost certainly {meta['cause']}. Find the root cause rather "
        "than working through these one by one."
    )

    return [
        GeneratedTask(
            source="ga4",
            category=category,
            title=f"{meta['title']} ({len(affected)} of {evaluated_pages} pages)",
            description=description,
            # Full list, not a sample -- the description no longer repeats any of it
            # inline, so there's no reason to cap what's actually stored/exportable.
            affected_urls=affected,
            severity="high",
            # Same benchmark for every page here (one configured Benchmark per metric_key,
            # not per-page) -- each page's own actual value still needs per-url tracking,
            # see url_details below.
            metric_benchmark=per_page_tasks[0].metric_benchmark,
            effort_tier="high",
            # Each per-page task already carries its own full native row (see
            # _native_ga4_row) -- just merge them together rather than re-deriving
            # anything from metric_actual alone.
            url_details={url: row for t in per_page_tasks for url, row in (t.url_details or {}).items()},
        )
    ]


def _batch_generated_tasks(category: str, per_page_tasks: list[GeneratedTask], budget: int) -> list[GeneratedTask]:
    """Collapse already-built per-page tasks into a single consolidated task once
    they exceed the campaign threshold -- same collapse reasoning as
    crawl_rules.py/gsc_rules.py, applied to tasks already built per page (each
    check's per-page description differs enough that collapsing from the raw
    GeneratedTask, not a re-derived candidate dict, is simpler here). batch_items
    returns exactly one group in this case, so this only ever produces one
    additional task, not several look-alike ones landing on different days."""
    if len(per_page_tasks) <= budget:
        return per_page_tasks
    prefix = _BATCH_TITLE_PREFIX[category]
    result: list[GeneratedTask] = []
    for batch in batch_items(per_page_tasks, budget):
        if len(batch) == 1:
            result.append(batch[0])
            continue
        benchmark = batch[0].metric_benchmark
        benchmark_phrase = (
            f", all {_BATCH_DIRECTION.get(category, 'vs')} the {benchmark * 100:.1f}% benchmark"
            if benchmark is not None else ""
        )
        description = (
            f"{len(batch)} pages batched into one consolidated pass -- {prefix.lower()} on each{benchmark_phrase}. "
            "Export this task for the full list of pages with their actual value vs. benchmark."
        )
        result.append(
            GeneratedTask(
                source="ga4",
                category=category,
                title=f"{prefix} for {len(batch)} pages",
                description=description,
                affected_urls=[t.affected_urls[0] for t in batch],
                severity="high" if any(t.severity == "high" for t in batch) else "medium",
                metric_benchmark=batch[0].metric_benchmark,
                effort_tier=batch[0].effort_tier,
                # Each per-page task already carries its own full native row (see
                # _native_ga4_row) -- just merge them together.
                url_details={url: row for t in batch for url, row in (t.url_details or {}).items()},
            )
        )
    return result


def generate_ga4_tasks(
    page_rows: list[dict],
    mobile_share_by_page: dict[str, float],
    benchmarks: dict[str, float],
) -> list[GeneratedTask]:
    """page_rows: [{"page":..., "sessions":..., "active_users":..., "engagement_rate":..., "bounce_rate":..., "key_events":...}, ...]
    mobile_share_by_page: {page: mobile_session_share}, from fetch_mobile_share.
    benchmarks: {"engagement_rate": 0.55, "exit_rate": 0.60, "mobile_share": 0.35, "key_events": 0.01}
    (site's configured Benchmarks, flat metric_key -> target_value) -- entries are optional; a
    metric with no configured benchmark is simply not checked. Direction (lt/gt) is fixed per
    metric below, same as gsc_rules.py hardcoding "ctr < target" rather than dispatching on
    Benchmark.comparator generically.
    """
    ui_ux_tasks: list[GeneratedTask] = []
    housekeeping_tasks: dict[str, list[GeneratedTask]] = {k: [] for k in _HOUSEKEEPING_CHECKS}
    evaluated_pages = 0

    for row in page_rows:
        sessions = row["sessions"]
        if sessions < MIN_SESSIONS:
            continue
        evaluated_pages += 1
        page = row["page"]
        severity = _severity_for_sessions(sessions)

        engagement_target = benchmarks.get("engagement_rate")
        is_high_traffic = sessions >= HIGH_TRAFFIC_SESSIONS and row["active_users"] >= MIN_ACTIVE_USERS_FOR_UX_REVIEW
        if engagement_target is not None and is_high_traffic and row["engagement_rate"] < engagement_target:
            ui_ux_tasks.append(
                GeneratedTask(
                    source="ga4",
                    category="ui_ux_review",
                    title=f"UI/UX review: {page}",
                    description=(
                        f"{sessions} sessions, healthy traffic, but only {row['engagement_rate'] * 100:.1f}% "
                        f"engagement vs a {engagement_target * 100:.1f}% benchmark. Audit UI/UX, page speed, and "
                        "mobile rendering -- traffic isn't the problem, the experience is."
                    ),
                    affected_urls=[page],
                    severity=severity,
                    metric_actual=row["engagement_rate"],
                    metric_benchmark=engagement_target,
                    effort_tier="medium",
                    url_details={page: _native_ga4_row(row)},
                )
            )

        # bounceRate is GA4's own defined complement of engagementRate (bounceRate =
        # 1 - engagementRate, not an independent metric) -- same signal as
        # ui_ux_review above, so it needs the same "is this even worth caring about"
        # significance gate (real traffic AND real users), not just the general
        # MIN_SESSIONS noise floor. Without this, a page could clear ui_ux_review's
        # bar but still get flagged here at a much lower traffic level for what is
        # mathematically the identical underlying number.
        exit_target = benchmarks.get("exit_rate")
        if exit_target is not None and is_high_traffic and row["bounce_rate"] > exit_target:
            housekeeping_tasks["high_exit_rate"].append(
                GeneratedTask(
                    source="ga4",
                    category="high_exit_rate",
                    title=f"Reduce exits: {page}",
                    description=(
                        f"{sessions} sessions with a {row['bounce_rate'] * 100:.1f}% bounce rate vs a "
                        f"{exit_target * 100:.1f}% benchmark. Check for a mismatched CTA, missing next-step link, "
                        "or a slow/broken page element."
                    ),
                    affected_urls=[page],
                    severity=severity,
                    metric_actual=row["bounce_rate"],
                    metric_benchmark=exit_target,
                    effort_tier="medium",
                    url_details={page: _native_ga4_row(row)},
                )
            )

        mobile_target = benchmarks.get("mobile_share")
        mobile_share = mobile_share_by_page.get(page)
        if mobile_target is not None and mobile_share is not None and mobile_share < mobile_target:
            housekeeping_tasks["low_mobile_share"].append(
                GeneratedTask(
                    source="ga4",
                    category="low_mobile_share",
                    title=f"Check mobile experience: {page}",
                    description=(
                        f"Only {mobile_share * 100:.1f}% of {sessions} sessions are mobile vs a "
                        f"{mobile_target * 100:.1f}% benchmark. Audit for slow mobile load, layout, or tap-target "
                        "issues."
                    ),
                    affected_urls=[page],
                    severity=severity,
                    metric_actual=mobile_share,
                    metric_benchmark=mobile_target,
                    effort_tier="medium",
                    url_details={page: _native_ga4_row(row)},
                )
            )

        key_events_target = benchmarks.get("key_events")
        key_event_rate = row["key_events"] / sessions if sessions else 0.0
        if key_events_target is not None and key_event_rate < key_events_target:
            housekeeping_tasks["low_key_events"].append(
                GeneratedTask(
                    source="ga4",
                    category="low_key_events",
                    title=f"Improve conversion path: {page}",
                    description=(
                        f"{sessions} sessions but only {row['key_events']:.0f} key events "
                        f"({key_event_rate * 100:.2f}% vs a {key_events_target * 100:.2f}% benchmark). Review the "
                        "CTA and whether the key event is actually reachable/tracked here."
                    ),
                    affected_urls=[page],
                    severity=severity,
                    metric_actual=key_event_rate,
                    metric_benchmark=key_events_target,
                    effort_tier="medium",
                    url_details={page: _native_ga4_row(row)},
                )
            )

    tasks = _batch_generated_tasks("ui_ux_review", ui_ux_tasks, CAMPAIGN_TASK_BUDGET["ui_ux_review"])
    for category, per_page in housekeeping_tasks.items():
        collapsed = _collapse_if_systemic(category, per_page, evaluated_pages)
        tasks.extend(_batch_generated_tasks(category, collapsed, CAMPAIGN_TASK_BUDGET[category]))
    return tasks
