"""Glue between ingestion -> rule engine -> scheduler -> persistence.

Kept separate from the router so it can be unit/integration tested without
spinning up FastAPI.
"""
from __future__ import annotations

import datetime as dt
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.google_oauth import get_valid_access_token
from app.ingestion.ga4_sync import fetch_mobile_share, fetch_page_metrics, fetch_site_totals_by_date as fetch_ga4_site_totals
from app.ingestion.gsc_sync import (
    fetch_page_analytics,
    fetch_page_query_analytics,
    fetch_site_totals_by_date as fetch_gsc_site_totals,
)
from app.ingestion.screaming_frog import import_crawl_folder
from app.models import (
    Benchmark,
    Campaign,
    Connection,
    CrawlImport,
    CrawlIssue,
    MetricSnapshot,
    Site,
    SiteMetricDaily,
    Task,
    VolumeBenchmark,
)
from app.rules.content_rules import generate_benchmarking_task, generate_content_plan
from app.rules.crawl_rules import consolidate_technical_tasks, generate_crawl_tasks, generate_indexation_blocking_tasks
from app.rules.ga4_rules import generate_ga4_tasks
from app.rules.gsc_rules import generate_gsc_tasks
from app.rules.optimization_levels import STANDING_TASK_CATEGORIES, default_optimization_level
from app.rules.reporting_rules import generate_reporting_tasks
from app.rules.task_hours import estimated_hours_for
from app.rules.volume_rules import evaluate_volume_benchmarks
from app.scheduling.month_utils import add_months
from app.scheduling.timeline import assign_schedule

# How far back each sync fetches site-wide daily totals (see _upsert_site_metric_daily
# below) -- needs to comfortably cover a full closed calendar month (up to 31 days)
# even in the worst case where the latest synced day is the 1st of a month, meaning
# the whole PREVIOUS month (up to 31 days further back) is what VolumeBenchmark's
# monthly check needs. 40 gives real slack over the 31+1 minimum.
VOLUME_TREND_LOOKBACK_DAYS = 40


def _upsert_site_metric_daily(db: Session, site_id: int, source: str, rows: list[dict], metric_keys: list[str]) -> None:
    """Delete-then-insert for the fetched date range -- a re-synced day overwrites
    its existing row instead of accumulating duplicates, unlike MetricSnapshot's
    intentional historical-log behavior (see SiteMetricDaily's own docstring)."""
    if not rows:
        return
    dates = [row["date"] for row in rows]
    db.query(SiteMetricDaily).filter(
        SiteMetricDaily.site_id == site_id,
        SiteMetricDaily.source == source,
        SiteMetricDaily.metric_key.in_(metric_keys),
        SiteMetricDaily.date.in_(dates),
    ).delete(synchronize_session="fetch")
    db.flush()
    for row in rows:
        for key in metric_keys:
            if key in row:
                db.add(SiteMetricDaily(site_id=site_id, source=source, metric_key=key, date=row["date"], value=row[key]))


def evaluate_site_volume_benchmarks(db: Session, site_id: int) -> list[dict]:
    """Every VolumeBenchmark configured for this site, evaluated against its
    latest closed period (see rules/volume_rules.py) -- flagged ones first.
    Purely a read; nothing here is cached/stored, so it's always current as of
    whatever's actually been synced. Used by both the Benchmarks page (the full
    list) and the Overview page (just the flagged ones, as a warning banner).
    """
    benchmarks = db.scalars(select(VolumeBenchmark).where(VolumeBenchmark.site_id == site_id)).all()
    if not benchmarks:
        return []
    daily_rows = db.scalars(select(SiteMetricDaily).where(SiteMetricDaily.site_id == site_id)).all()
    daily_rows_by_key: dict[tuple[str, str], list[SiteMetricDaily]] = {}
    for row in daily_rows:
        daily_rows_by_key.setdefault((row.source, row.metric_key), []).append(row)
    return evaluate_volume_benchmarks(benchmarks, daily_rows_by_key)


def _dates_occupied_by_other_sources(db: Session, site_id: int, source: str) -> frozenset[dt.date]:
    """Every target_date already on this site's calendar from a source OTHER than the
    one currently being (re)generated. Crawl/GSC/GA4 tasks are each generated and
    scheduled independently -- without this, each source's own day-1 slot lands on the
    same calendar date (whichever is the next available business day from wherever that
    source starts counting), stacking multiple sources' tasks onto one day even though
    each source's own internal spacing looks correct in isolation. Passed into
    assign_schedule's excluded_dates so a newly (re)scheduled source's tasks land on
    genuinely free days instead of colliding with what's already there.

    Flushes first -- this app's session is autoflush=False (see app/db.py), so a task
    just added earlier in the same call (e.g. run_crawl_import's own Technical Audit
    task, staged but not yet flushed when ensure_benchmarking_task runs right after)
    would otherwise be invisible to this SELECT and collide with it anyway."""
    db.flush()
    dates = db.scalars(
        select(Task.target_date).where(
            Task.site_id == site_id, Task.source != source, Task.target_date.is_not(None)
        )
    ).all()
    return frozenset(dates)


# Phase order the analyst asked for: benchmarking (week 1) -> Key Fix, technical work
# first -> Quick Wins -> Ongoing Content's recurring research/brief/article cycle.
# One task, one week, straight through -- no more per-source independent scheduling
# (the old assign_schedule-per-source design), which was how crawl/gsc/ga4/content_plan
# tasks ended up landing on the same day and Key Fix items scattering months apart:
# each source had no idea what the others had already placed on the calendar.
# The analyst's own hierarchy (confirmed explicitly): Research & Benchmarking ->
# Technical Audit -> (the rest of) Key Fixes -> Quick Wins -> Ongoing Content.
# technical_audit carries optimization_level "key_fix" for filtering/badge purposes
# elsewhere in the UI, but SCHEDULING it needs finer granularity than that one level
# gives -- it needs to run before the rest of key_fix (it's the crawl pass
# url_structure_optimization/indexation_blocking/etc. all follow from), not just
# somewhere within that phase (see _schedule_phase_for).
_SCHEDULE_PHASE_ORDER = ["benchmarking", "technical_audit", "key_fix", "quick_win", "ongoing_content"]
_KEY_FIX_ORDER = [
    "url_structure_optimization", "indexation_blocking", "server_error", "404_fix",
    "redirect_inlink_update", "ui_ux_review", "low_mobile_share", "low_key_events",
]
_QUICK_WIN_ORDER = ["meta_tag_reoptimization", "high_exit_rate", "ctr_optimization", "anchor_optimization"]
WEDNESDAY = 2
# An 8-hour workday's total capacity -- see reschedule_all_tasks. Real, analyst-
# supplied (Sahil, 2026-08-27), not a guess.
DAILY_CAPACITY_HOURS = 8.0


def _schedule_phase_for(task: Task) -> str:
    """Scheduling phase, distinct from optimization_level -- see _SCHEDULE_PHASE_ORDER's
    comment for why technical_audit needs its own slot ahead of the rest of key_fix."""
    if task.category == "technical_audit":
        return "technical_audit"
    return task.optimization_level or default_optimization_level(task.category) or "ongoing_content"


def _phase_sort_key(task: Task) -> tuple:
    phase = _schedule_phase_for(task)
    phase_rank = _SCHEDULE_PHASE_ORDER.index(phase) if phase in _SCHEDULE_PHASE_ORDER else len(_SCHEDULE_PHASE_ORDER)
    if phase in ("key_fix", "quick_win"):
        # These come from crawl/gsc/ga4 syncs that can run in any order relative to
        # each other, so insertion order alone doesn't reflect the desired priority --
        # an explicit within-phase order is needed (technical work before GA4 checks,
        # etc).
        order = _KEY_FIX_ORDER if phase == "key_fix" else _QUICK_WIN_ORDER
        within_rank = order.index(task.category) if task.category in order else len(order)
        return (phase_rank, within_rank, task.id or 0)
    # benchmarking/technical_audit/setup_reporting (each effectively a single task)
    # and ongoing_content: insertion order already IS the right sequence --
    # regenerate_content_plan generates month 0's research/brief/article/llm as one
    # contiguous block before month 1's, so sorting by id preserves that cycle
    # correctly. Deliberately NOT using month_index here: this function overwrites
    # month_index on every call, so sorting by it would make a second call (e.g.
    # after a later GA4 sync) scramble the cycle order the first call produced.
    return (phase_rank, task.id or 0)


def _month_index_for(anchor: dt.date, target_date: dt.date) -> int:
    return (target_date.year - anchor.year) * 12 + (target_date.month - anchor.month)


def _assign_next_available_slot(
    task: Task, days: list[dt.date], day_hours: dict[dt.date, float], anchor: dt.date, start_idx: int
) -> int:
    """Places `task` on the earliest day at/after days[start_idx] with room left in its
    DAILY_CAPACITY_HOURS budget, tracking the running total per day in `day_hours` as it
    goes. A completely empty day (0 hours used yet) is always used regardless of how
    big the task is -- a single task heavier than a full day's capacity would otherwise
    get permanently stuck advancing forever, never actually landing anywhere. Returns
    the index it landed on (or len(days) if the campaign window ran out first, in which
    case target_date/month_index are left None -- next cycle's backlog, same as before
    this was hour-based), so a caller walking several tasks in a fixed order can resume
    searching forward from there instead of re-scanning from day 0 for every task."""
    idx = start_idx
    while idx < len(days) and day_hours.get(days[idx], 0.0) > 0.0 and day_hours[days[idx]] + task.estimated_hours > DAILY_CAPACITY_HOURS:
        idx += 1
    if idx >= len(days):
        task.target_date = None
        task.month_index = None
        return idx
    d = days[idx]
    task.target_date = d
    task.month_index = _month_index_for(anchor, d)
    day_hours[d] = day_hours.get(d, 0.0) + task.estimated_hours
    return idx


def reschedule_all_tasks(db: Session, site_id: int) -> None:
    """Single unified scheduler across every source (crawl/gsc/ga4/content_plan) for
    this site: every business day is an 8-hour bucket (DAILY_CAPACITY_HOURS), filled in
    order through every phase of the analyst's own hierarchy -- Research & Benchmarking
    -> Technical Audit -> (the rest of) Key Fixes -> Quick Wins -> Ongoing Content --
    each task consuming its own Task.estimated_hours (see app/rules/task_hours.py's
    real, analyst-supplied numbers) until the day's full, then spilling to the next.
    Bounded to the campaign's configured duration_months; whatever doesn't fit within
    that window is left unscheduled (target_date=None) -- it becomes next cycle's
    backlog once the campaign is renewed, rather than overloading the analyst now or
    silently running the plan past its configured length.

    This replaced a strict "one task per week" model (2026-08-27) -- multiple tasks can
    now share a day, and a heavy day can push work to the next business day rather than
    always exactly 7 days after the last one. Deliberately simple greedy packing, not
    optimal bin-packing: tasks are placed in a fixed priority order and a day, once
    passed over because the next task in line didn't fit, is never revisited even if a
    later, smaller task would have -- same "simple explicit rule over guessing"
    philosophy as the rest of this codebase's rule engines, and with real hours mostly
    in the 0.75-4h range, the capacity gaps this leaves on the table are modest.

    A task a human has explicitly moved (Task.manually_scheduled, set by the due-date
    route/chat tool) is left exactly where it was put -- never re-derived or silently
    overwritten by a later call -- but its hours still count against that day's
    capacity, so autoscheduled work doesn't get packed on top of it past 8 hours.

    Idempotent and safe to call after any import/sync/regenerate: since real data
    arrives asynchronously (crawl imported today, GSC connected next week), this always
    re-derives one coherent calendar from whatever's currently in the DB (excluding
    manually-scheduled tasks), rather than each source guessing around what the others
    might have already placed.

    Two categories of task sit outside this queue entirely, both calendar-anchored by
    generate_reporting_tasks instead of taking a sequential slot:
      - weekly_report/monthly_report_mbr: start the campaign's SECOND month, one per
        Wednesday -- the one day of the week this queue deliberately never uses (see
        WEDNESDAY below), so the two never compete for the same day.
      - performance_dashboard: the one-time "set up reporting" task, fixed to the LAST
        Wednesday of month 1 (bridging into the above).
    schema_recommendations is a third special case: not calendar-anchored, but not part
    of the main capacity-packing pass either -- it's placed separately afterward, same
    day as technical_audit if there's room left in that day's budget (there almost
    always is: 3h + 0.75h is well under 8h), otherwise the next day with room, found the
    exact same way as everything else (see _assign_next_available_slot).
    """
    campaign = db.query(Campaign).filter(Campaign.site_id == site_id).order_by(Campaign.start_date.desc()).first()
    if not campaign:
        return
    # Anchor to whichever is later: the campaign's own start date, or today. A brand
    # new campaign with a future start date should still anchor to that future date
    # (work hasn't begun yet) -- but an old, long-running campaign's start date could
    # be years in the past, and freshly-synced GSC/GA4 findings should never get
    # scheduled retroactively into a date that's already happened.
    anchor = max(campaign.start_date, dt.date.today())
    campaign_last_day = add_months(campaign.start_date.replace(day=1), campaign.duration_months) - dt.timedelta(days=1)

    days: list[dt.date] = []
    d = anchor
    while d <= campaign_last_day:
        if d.weekday() < 5 and d.weekday() != WEDNESDAY:
            days.append(d)
        d += dt.timedelta(days=1)
    if not days:
        # The campaign's own configured end has already passed (e.g. "today" landed
        # after it) -- fall back to a single day so a freshly-synced finding still
        # has somewhere to go, rather than going dateless purely because of this
        # edge case rather than a real capacity limit.
        days = [anchor]

    all_tasks = db.scalars(select(Task).where(Task.site_id == site_id)).all()
    # weekly_report/monthly_report_mbr/performance_dashboard all stay calendar-
    # anchored (generate_reporting_tasks) regardless of how this queue paces.
    reporting_tasks = [
        t for t in all_tasks if (t.optimization_level or default_optimization_level(t.category)) == "reporting"
    ]
    schema_tasks = [t for t in all_tasks if t.category == "schema_recommendations"]
    excluded_ids = {t.id for t in reporting_tasks + schema_tasks}
    sequential_tasks = [t for t in all_tasks if t.id not in excluded_ids]

    manual_tasks = [t for t in sequential_tasks if t.manually_scheduled]
    auto_tasks = [t for t in sequential_tasks if not t.manually_scheduled]

    # Seed each already-manually-placed task's hours against its OWN existing date --
    # autoscheduling below must not stack more work on top of a day a human already
    # committed to past its 8-hour budget. A manual date outside `days` (a weekend, a
    # Wednesday, past the campaign end -- the due-date route doesn't restrict this)
    # simply never matches a key here, which is fine: it's not a day this scheduler
    # allocates against anyway.
    day_hours: dict[dt.date, float] = {}
    for t in manual_tasks:
        if t.target_date:
            day_hours[t.target_date] = day_hours.get(t.target_date, 0.0) + (t.estimated_hours or 0.0)

    # In phase order (benchmarking -> technical_audit -> key_fix -> quick_win ->
    # ongoing_content -- see _phase_sort_key), each task consuming its own
    # estimated_hours against the day it lands on. Ongoing content's own recurring
    # tasks (research/brief/article/page-optimization/llm -- source="content_plan")
    # are included here rather than kept on generate_content_plan's own per-month
    # batching: that per-month batching is what caused the very cramming this
    # replaces, letting a whole month's content quota land within that same month
    # regardless of how much capacity was actually available for it.
    ordered = sorted(auto_tasks, key=_phase_sort_key)
    day_idx = 0
    for task in ordered:
        day_idx = _assign_next_available_slot(task, days, day_hours, anchor, day_idx)

    technical_audit = next((t for t in sequential_tasks if t.category == "technical_audit"), None)
    if schema_tasks and technical_audit and technical_audit.target_date in days:
        start_idx = days.index(technical_audit.target_date)
        for schema_task in schema_tasks:
            _assign_next_available_slot(schema_task, days, day_hours, anchor, start_idx)
    elif schema_tasks and not (technical_audit and technical_audit.target_date):
        for schema_task in schema_tasks:
            schema_task.target_date = None
            schema_task.month_index = None

    db.commit()


def ensure_benchmarking_task(db: Session, site_id: int, start_date: dt.date) -> None:
    """Seeds the one-off "Prompt Analysis & Keyword Research (Benchmarking)" task in
    week 1 if this site doesn't already have one. Deliberately create-once, not
    regenerated/replaced on every re-import like crawl/gsc/ga4 tasks are -- an
    analyst's progress on this (status, notes, reassignment) shouldn't reset just
    because the crawl got re-imported."""
    exists = db.scalars(
        select(Task.id).where(Task.site_id == site_id, Task.category == "prompt_keyword_benchmarking")
    ).first()
    if exists:
        return
    # Route around whatever's already on the calendar (e.g. the crawl's own Technical
    # Audit task, landing on this same start_date) -- same "don't stack two sources'
    # tasks on one day" reasoning as assign_schedule's excluded_dates.
    occupied = _dates_occupied_by_other_sources(db, site_id, "__none__")
    target_date = start_date
    while target_date.weekday() >= 5 or target_date in occupied:
        target_date += dt.timedelta(days=1)
    item = generate_benchmarking_task(target_date)
    db.add(
        Task(
            site_id=site_id,
            source=item.source,
            category=item.category,
            title=item.title,
            description=item.description,
            affected_urls=item.affected_urls,
            severity=item.severity,
            month_index=item.month_index,
            target_date=item.target_date,
            effort_tier=item.effort_tier,
            optimization_level=default_optimization_level(item.category),
            estimated_hours=estimated_hours_for(item.category),
            status="todo",
        )
    )


def ensure_anchor_optimization_task(db: Session, site_id: int) -> None:
    """Seeds the one-off "Anchor Optimization of Service/Product/Target Pages" Quick
    Win task if this site doesn't already have one. Create-once like
    ensure_benchmarking_task -- reschedule_all_tasks slots it into its place in the
    Quick Win sequence (see _QUICK_WIN_ORDER) every time it runs, but the task itself
    is never deleted/recreated."""
    exists = db.scalars(
        select(Task.id).where(Task.site_id == site_id, Task.category == "anchor_optimization")
    ).first()
    if exists:
        return
    db.add(
        Task(
            site_id=site_id,
            source="content_plan",
            category="anchor_optimization",
            title="Anchor Optimization of Service/Product/Target Pages",
            description=(
                "Review and optimize internal-link anchor text pointing at the site's core service/"
                "product/target pages -- align anchor text with the actual target keywords for each "
                "page rather than generic \"click here\"/\"learn more\" text."
            ),
            affected_urls=[],
            severity="medium",
            effort_tier="medium",
            optimization_level=default_optimization_level("anchor_optimization"),
            estimated_hours=estimated_hours_for("anchor_optimization"),
            status="todo",
        )
    )


def ensure_url_structure_optimization_task(db: Session, site_id: int) -> None:
    """Seeds the one-off "URL Structure Optimization" Key Fix task if this site
    doesn't already have one. Same hierarchy/prioritization as the other standing
    tasks -- create-once, slotted into its normal place in the Key Fix sequence (see
    _KEY_FIX_ORDER, right after technical_audit) every time reschedule_all_tasks runs,
    no special pinned-day logic like schema_recommendations."""
    exists = db.scalars(
        select(Task.id).where(Task.site_id == site_id, Task.category == "url_structure_optimization")
    ).first()
    if exists:
        return
    db.add(
        Task(
            site_id=site_id,
            source="content_plan",
            category="url_structure_optimization",
            title="URL Structure Optimization",
            description=(
                "Review the site's URL structure (depth, folder logic, keyword presence, parameter/"
                "duplicate-path cleanup) and recommend/implement fixes -- a clear, consistent structure "
                "supports both crawlability and topical organization."
            ),
            affected_urls=[],
            severity="medium",
            effort_tier="medium",
            optimization_level=default_optimization_level("url_structure_optimization"),
            estimated_hours=estimated_hours_for("url_structure_optimization"),
            status="todo",
        )
    )


def ensure_schema_recommendations_task(db: Session, site_id: int) -> None:
    """Seeds the one-off "Schema Recommendations" Key Fix task if this site doesn't
    already have one. Its target_date is intentionally left unset here --
    reschedule_all_tasks computes it separately (same day as technical_audit, if
    there's capacity left that day, else the next day with room) once
    technical_audit's own date is known, rather than assigning it a normal
    sequential slot."""
    exists = db.scalars(
        select(Task.id).where(Task.site_id == site_id, Task.category == "schema_recommendations")
    ).first()
    if exists:
        return
    db.add(
        Task(
            site_id=site_id,
            # NOT source="crawl" -- run_crawl_import deletes+recreates every "crawl"
            # task on every re-import, which would wipe this create-once task's status/
            # assignee just like ensure_benchmarking_task's earlier bug did. See
            # STANDING_TASK_CATEGORIES for the corresponding exclusion from
            # regenerate_content_plan's cleanup.
            source="content_plan",
            category="schema_recommendations",
            title="Schema Recommendations",
            description=(
                "Review the site's structured data (or lack of it) alongside the technical audit and "
                "recommend/implement the schema types that fit its content (Article, Product, FAQ, "
                "Organization, etc.) -- same day as the technical audit, since both come from the same "
                "crawl pass."
            ),
            affected_urls=[],
            severity="medium",
            effort_tier="medium",
            optimization_level=default_optimization_level("schema_recommendations"),
            estimated_hours=estimated_hours_for("schema_recommendations"),
            status="todo",
        )
    )


def run_crawl_import(db: Session, site_id: int, folder: str, crawl_date: dt.date) -> CrawlImport:
    """Parse a Screaming Frog export folder, generate + schedule tasks, persist everything.

    Idempotent on tasks, same as sync_gsc_and_generate_tasks/sync_ga4_and_generate_tasks:
    old "crawl" tasks are cleared first, so re-importing after a fresh crawl (fixing a
    corrupted export, a re-crawl next month, etc.) replaces the backlog instead of piling
    duplicate tasks on top of it. CrawlImport/CrawlIssue rows are untouched and just
    accumulate as a historical log, same as MetricSnapshot does for GSC/GA4."""
    site = db.get(Site, site_id)
    crawl_result = import_crawl_folder(folder, site_domain=site.domain if site else None)
    issue_rows = crawl_result.issues

    db.query(Task).filter(Task.site_id == site_id, Task.source == "crawl").delete(synchronize_session="fetch")
    db.flush()

    crawl_import = CrawlImport(
        site_id=site_id,
        crawl_date=crawl_date,
        source_folder=folder,
        issue_count=len(issue_rows),
        total_urls=crawl_result.total_urls,
        site_scale=crawl_result.site_scale,
    )
    db.add(crawl_import)
    db.flush()  # get crawl_import.id without committing yet

    for row in issue_rows:
        db.add(
            CrawlIssue(
                site_id=site_id,
                crawl_import_id=crawl_import.id,
                issue_type=row.issue_type,
                url=row.url,
                status_code=row.status_code,
                redirects_to=row.redirects_to,
                inlinking_urls=row.inlinking_urls,
            )
        )

    generated = generate_crawl_tasks(issue_rows) + generate_indexation_blocking_tasks(crawl_result.indexation_blocking)

    campaign = db.query(Campaign).filter(Campaign.site_id == site_id).order_by(Campaign.start_date.desc()).first()
    # Default True (see Campaign.consolidate_technical_tasks) even with no campaign yet --
    # a fresh site with no package details configured should still get the analyst-friendly
    # default, not silently fall back to the old per-category behavior.
    if campaign is None or campaign.consolidate_technical_tasks:
        generated = consolidate_technical_tasks(generated)
    start_date = campaign.start_date if campaign else dt.date.today()
    capacity = campaign.capacity_per_week if campaign else 5
    duration_months = campaign.duration_months if campaign else 6
    default_assignee = campaign.default_assignee if campaign else None

    scheduled = assign_schedule(
        generated, start_date, capacity, site_scale=crawl_result.site_scale, duration_months=duration_months,
        excluded_dates=_dates_occupied_by_other_sources(db, site_id, "crawl"),
    )

    for t in scheduled:
        db.add(
            Task(
                site_id=site_id,
                source=t.source,
                category=t.category,
                title=t.title,
                description=t.description,
                affected_urls=t.affected_urls,
                url_details=t.url_details or {},
                severity=t.severity,
                metric_actual=t.metric_actual,
                metric_benchmark=t.metric_benchmark,
                month_index=t.month_index,
                target_date=t.target_date,
                effort_tier=t.effort_tier,
                optimization_level=default_optimization_level(t.category),
                estimated_hours=estimated_hours_for(t.category),
                status="todo",
                assignee=default_assignee,
            )
        )

    ensure_benchmarking_task(db, site_id, start_date)
    ensure_schema_recommendations_task(db, site_id)
    ensure_anchor_optimization_task(db, site_id)
    ensure_url_structure_optimization_task(db, site_id)

    db.commit()
    reschedule_all_tasks(db, site_id)
    db.refresh(crawl_import)
    return crawl_import


class NoCampaignError(Exception):
    """Raised when a content plan is requested but the site has no campaign set up yet."""


# Real, evidence-grounded (see the "wasted impressions" analysis this was sized
# from) -- below this floor a low CTR is unremarkable noise, not a real signal.
# Used by _ranked_wasted_impression_pages; the CTR side of the check comes from
# the site's own configured Benchmark row instead (kept in sync with the
# Benchmarks page rather than a second hardcoded copy of it).
WASTED_IMPRESSION_MIN_IMPRESSIONS = 1000


def _latest_month_deduped_gsc_pages(db: Session, site_id: int) -> list[dict]:
    """The site's own most-recently-synced GSC pages (one row per url, latest sync
    wins), unsorted -- shared by both _ranked_gsc_pages_for_optimization and
    _ranked_wasted_impression_pages, which each just sort/filter this same base
    list differently. Uses whatever's already been synced (MetricSnapshot), not a
    fresh live API call -- a real GSC sync already produced these numbers. Empty
    list (not an error) if the site has never synced GSC data yet.
    """
    latest_month = db.scalars(
        select(MetricSnapshot.month)
        .where(MetricSnapshot.site_id == site_id, MetricSnapshot.source == "gsc", MetricSnapshot.metric_key == "ctr")
        .order_by(MetricSnapshot.month.desc())
    ).first()
    if not latest_month:
        return []
    # Ordered by id ascending -- MetricSnapshot rows accumulate as a historical log
    # (a re-sync doesn't overwrite the prior one, it adds new rows), so the same
    # month can hold several snapshots per url from different sync runs. Iterating
    # oldest-first and letting each url's LAST row win keeps only the most recent
    # sync's numbers per page, rather than a same-url duplicate appearing several
    # times in a row near the top of the ranking.
    rows = db.scalars(
        select(MetricSnapshot).where(
            MetricSnapshot.site_id == site_id, MetricSnapshot.source == "gsc",
            MetricSnapshot.metric_key == "ctr", MetricSnapshot.month == latest_month,
        ).order_by(MetricSnapshot.id)
    ).all()
    by_url: dict[str, dict] = {}
    for r in rows:
        if not r.url:
            continue
        by_url[r.url] = {
            "page": r.url,
            "impressions": (r.extra or {}).get("impressions", 0),
            "clicks": (r.extra or {}).get("clicks", 0),
            "ctr": r.value,
            "position": (r.extra or {}).get("position", 0),
        }
    return list(by_url.values())


def _ranked_gsc_pages_for_optimization(db: Session, site_id: int) -> list[dict]:
    """Every synced page, ranked by impressions descending -- feeds page_optimization's
    month-by-month "highest opportunity first, gradually working down the ranking"
    cadence (see generate_content_plan). Empty list (not an error) if the site has
    never synced GSC data -- generate_content_plan falls back to its existing
    generic placeholder in that case."""
    ranked = _latest_month_deduped_gsc_pages(db, site_id)
    ranked.sort(key=lambda p: -p["impressions"])
    return ranked


def _ranked_wasted_impression_pages(db: Session, site_id: int) -> list[dict]:
    """Same real, already-synced GSC pages, filtered down to the "wasted impressions"
    signal (real, meaningful visibility converting to essentially no clicks) and
    ranked the same way -- feeds wasted_impressions' own month-by-month cadence
    alongside page_optimization's (see generate_content_plan). This used to be a
    live-per-sync check in gsc_rules.py (Tier 0); moved here so it can be paced out
    gradually across months instead of dumping every qualifying page into one task
    the moment a sync runs -- see gsc_rules.py's module docstring.

    Reads the site's own configured CTR floor (Benchmark(metric_key="ctr",
    segment="high_impression_wasted")) rather than a hardcoded value, so it stays
    in sync with whatever's set on the Benchmarks page. Empty list if that
    benchmark isn't configured for this site -- same "don't fabricate a threshold"
    rule the old live check followed."""
    benchmark = db.scalars(
        select(Benchmark).where(
            Benchmark.site_id == site_id, Benchmark.metric_key == "ctr",
            Benchmark.segment == "high_impression_wasted",
        )
    ).first()
    if benchmark is None:
        return []
    pages = _latest_month_deduped_gsc_pages(db, site_id)
    wasted = [
        p for p in pages
        if p["impressions"] >= WASTED_IMPRESSION_MIN_IMPRESSIONS and p["ctr"] < benchmark.target_value
    ]
    wasted.sort(key=lambda p: -p["impressions"])
    return wasted


def regenerate_content_plan(db: Session, site_id: int) -> int:
    """(Re)generate the recurring content-creation/page-optimization tasks for every
    month of the campaign. Idempotent: existing content-plan tasks are cleared first,
    so calling this again after editing the package size doesn't leave stale duplicates.
    Returns the number of tasks created.
    """
    campaign = db.query(Campaign).filter(Campaign.site_id == site_id).order_by(Campaign.start_date.desc()).first()
    if not campaign:
        raise NoCampaignError(f"Site {site_id} has no campaign configured yet")

    # synchronize_session="fetch": selects the matching rows first and expunges exactly those
    # objects from the session's identity map, so a later insert that reuses one of their
    # (now-freed) SQLite rowids doesn't collide with a stale identity-mapped instance.
    #
    # Excludes STANDING_TASK_CATEGORIES (prompt_keyword_benchmarking, anchor_optimization,
    # schema_recommendations): they also use source="content_plan" but are deliberately
    # create-once, never regenerated -- without this exclusion, regenerating the content
    # plan would silently delete them and never recreate them, since this function only
    # builds the monthly content pipeline + reporting cadence.
    db.query(Task).filter(
        Task.site_id == site_id, Task.source == "content_plan", Task.category.not_in(STANDING_TASK_CATEGORIES)
    ).delete(synchronize_session="fetch")
    db.flush()  # commit the delete before inserting -- avoids SQLite reusing a just-deleted row's PK mid-flush

    # Only meaningful in "optimize_existing" mode -- "create_new" has no existing
    # page backlog to rank at all (see content_rules.py's generate_content_plan).
    optimize_existing = campaign.page_work_mode == "optimize_existing"
    ranked_pages = _ranked_gsc_pages_for_optimization(db, site_id) if optimize_existing else None
    ranked_wasted_pages = _ranked_wasted_impression_pages(db, site_id) if optimize_existing else None
    items = generate_content_plan(
        campaign.start_date,
        campaign.duration_months,
        campaign.content_pieces_per_month,
        campaign.pages_to_optimize_per_month,
        campaign.page_work_mode,
        ranked_pages,
        ranked_wasted_pages,
    ) + generate_reporting_tasks(campaign.start_date, campaign.duration_months)

    for item in items:
        db.add(
            Task(
                site_id=site_id,
                source=item.source,
                category=item.category,
                title=item.title,
                description=item.description,
                affected_urls=item.affected_urls,
                url_details=item.url_details,
                severity=item.severity,
                month_index=item.month_index,
                target_date=item.target_date,
                effort_tier=item.effort_tier,
                optimization_level=default_optimization_level(item.category),
                estimated_hours=estimated_hours_for(item.category),
                status="todo",
                assignee=campaign.default_assignee,
            )
        )

    db.commit()
    reschedule_all_tasks(db, site_id)
    return len(items)


class NoConnectionError(Exception):
    """Raised when a GSC/GA4 sync is requested but that provider isn't connected yet."""


def find_connection(db: Session, site_id: int, provider: str) -> Connection | None:
    """The Google OAuth connection to actually use for this site/provider -- a real
    site-specific one if this site has its own (the rare client whose data needs a
    different Google account than the rest), falling back to the one shared,
    desktop-wide connection (Connection.site_id is None) every other site uses.
    Connecting via Google from ANY one site's Connect page establishes that shared
    connection for every other site too (see google_auth.py's oauth_callback), so
    adding a 5th/6th/... client project never means going through Google's consent
    screen -- and whatever internal verification/approval it needs -- again."""
    return db.scalars(
        select(Connection).where(Connection.site_id == site_id, Connection.provider == provider)
    ).first() or db.scalars(
        select(Connection).where(Connection.site_id.is_(None), Connection.provider == provider)
    ).first()


def _compile_filter(pattern: str | None) -> re.Pattern | None:
    """Invalid regex is ignored (falls back to "no filter") rather than erroring the
    whole sync -- a typo in an optional customization field shouldn't block real data
    from syncing."""
    if not pattern or not pattern.strip():
        return None
    try:
        return re.compile(pattern)
    except re.error:
        return None


def apply_gsc_filters(
    site: Site, page_rows: list[dict], query_rows: list[dict]
) -> tuple[list[dict], list[dict]]:
    """Same idea as Search Console's own Performance-report page regex filter
    (Site.gsc_page_filter_regex, set in the Connect step) -- lets an analyst scope
    task generation to (mode="include") or away from (mode="exclude") a specific
    URL path, entirely optionally. Applied before the GSC rule engine ever sees the
    data, so it affects which pages can generate tasks at all, not just which ones
    get displayed.

    Also drops non-matching rows from query_rows (by page), since a page tier-2's
    query list wouldn't make sense for a page the page-filter excluded.

    No query-level equivalent -- there used to be one (gsc_query_filter_regex),
    merged into Site.brand_regex instead (see its own comment on why): an
    "exclude" filter here would have run BEFORE gsc_rules.py's branded/non-branded
    classification ever saw the data, silently deleting the exact queries that
    classification needs to see.
    """
    page_pattern = _compile_filter(site.gsc_page_filter_regex)
    if page_pattern:
        keep = (lambda p: bool(page_pattern.search(p))) if site.gsc_page_filter_mode == "include" \
            else (lambda p: not page_pattern.search(p))
        page_rows = [r for r in page_rows if keep(r["page"])]
        kept_pages = {r["page"] for r in page_rows}
        query_rows = [r for r in query_rows if r["page"] in kept_pages]

    return page_rows, query_rows


def apply_ga4_filters(
    site: Site, page_rows: list[dict], mobile_share_by_page: dict[str, float]
) -> tuple[list[dict], dict[str, float]]:
    """Same idea as apply_gsc_filters, but page-only -- GA4 has no query dimension.
    Useful when a campaign only covers one folder/section of the site."""
    page_pattern = _compile_filter(site.ga4_page_filter_regex)
    if not page_pattern:
        return page_rows, mobile_share_by_page
    keep = (lambda p: bool(page_pattern.search(p))) if site.ga4_page_filter_mode == "include" \
        else (lambda p: not page_pattern.search(p))
    page_rows = [r for r in page_rows if keep(r["page"])]
    kept_pages = {r["page"] for r in page_rows}
    mobile_share_by_page = {p: v for p, v in mobile_share_by_page.items() if p in kept_pages}
    return page_rows, mobile_share_by_page


def sync_gsc_and_generate_tasks(db: Session, site_id: int) -> dict:
    """Pull the trailing 28 days of page-level AND page+query Search Analytics data,
    persist the page-level rows as MetricSnapshots, and (re)generate the three-tier
    existing-page-optimization tasks (see app/rules/gsc_rules.py: meta_tag_reoptimization,
    content_expansion, ctr_optimization) against the site's configured "ctr" Benchmark
    and brand terms. Idempotent on tasks (old "gsc" tasks are cleared first);
    MetricSnapshot rows just accumulate as a data log.

    GSC tasks are scheduled from *today*, not the campaign start date -- unlike
    crawl-issue tasks (which anchor to campaign start because they're a one-time
    backlog), CTR data reflects the site's current state and should be worked
    starting now.

    Also fetches/upserts site-wide daily clicks+impressions totals (SiteMetricDaily)
    over a wider window, feeding any configured VolumeBenchmark rows -- unrelated to
    the per-page task generation above.
    """
    site = db.get(Site, site_id)
    connection = find_connection(db, site_id, "gsc")
    if not connection:
        raise NoConnectionError(f"Site {site_id} has no GSC connection yet")
    if not site.gsc_site_url:
        raise NoConnectionError(f"Site {site_id} has no GSC property URL configured")

    access_token = get_valid_access_token(connection)  # may mutate + need saving if refreshed
    db.commit()

    end_date = dt.date.today() - dt.timedelta(days=3)  # GSC data usually lags ~2-3 days
    start_date = end_date - dt.timedelta(days=27)
    page_rows = fetch_page_analytics(access_token, site.gsc_site_url, start_date, end_date)
    query_rows = fetch_page_query_analytics(access_token, site.gsc_site_url, start_date, end_date)

    # Separate, wider-window fetch feeding VolumeBenchmark's site-wide daily/weekly/
    # monthly trend checks -- unrelated to the page-level rows/tasks above, which stay
    # on their existing 28-day window.
    site_totals = fetch_gsc_site_totals(
        access_token, site.gsc_site_url, end_date - dt.timedelta(days=VOLUME_TREND_LOOKBACK_DAYS), end_date
    )
    _upsert_site_metric_daily(db, site_id, "gsc", site_totals, ["clicks", "impressions"])

    month = start_date.replace(day=1)
    for row in page_rows:
        db.add(
            MetricSnapshot(
                site_id=site_id,
                source="gsc",
                url=row["page"],
                month=month,
                metric_key="ctr",
                value=row["ctr"],
                extra={"position": row["position"], "clicks": row["clicks"], "impressions": row["impressions"]},
            )
        )

    benchmarks = db.scalars(
        select(Benchmark).where(Benchmark.site_id == site_id, Benchmark.metric_key == "ctr")
    ).all()
    benchmarks_by_segment = {b.segment: b.target_value for b in benchmarks if b.segment}
    brand_terms = [t for t in (site.brand_terms or "").split(",")]

    filtered_page_rows, filtered_query_rows = apply_gsc_filters(site, page_rows, query_rows)
    generated = generate_gsc_tasks(
        filtered_page_rows, filtered_query_rows, benchmarks_by_segment, brand_terms, site.brand_regex
    )

    db.query(Task).filter(Task.site_id == site_id, Task.source == "gsc").delete(synchronize_session="fetch")
    db.flush()

    campaign = db.query(Campaign).filter(Campaign.site_id == site_id).order_by(Campaign.start_date.desc()).first()
    latest_import = (
        db.query(CrawlImport).filter(CrawlImport.site_id == site_id).order_by(CrawlImport.imported_at.desc()).first()
    )
    site_scale = latest_import.site_scale if latest_import else "small"
    capacity = campaign.capacity_per_week if campaign else 5
    duration_months = campaign.duration_months if campaign else 6
    default_assignee = campaign.default_assignee if campaign else None

    scheduled = assign_schedule(
        generated, dt.date.today(), capacity, site_scale=site_scale, duration_months=duration_months,
        excluded_dates=_dates_occupied_by_other_sources(db, site_id, "gsc"),
    )

    for t in scheduled:
        db.add(
            Task(
                site_id=site_id,
                source=t.source,
                category=t.category,
                title=t.title,
                description=t.description,
                affected_urls=t.affected_urls,
                url_details=t.url_details or {},
                severity=t.severity,
                metric_actual=t.metric_actual,
                metric_benchmark=t.metric_benchmark,
                month_index=t.month_index,
                target_date=t.target_date,
                effort_tier=t.effort_tier,
                optimization_level=default_optimization_level(t.category),
                estimated_hours=estimated_hours_for(t.category),
                status="todo",
                assignee=default_assignee,
            )
        )

    db.commit()
    reschedule_all_tasks(db, site_id)
    return {"pages_synced": len(page_rows), "tasks_generated": len(scheduled)}


def sync_ga4_and_generate_tasks(db: Session, site_id: int) -> dict:
    """Pull the trailing 28 days of page-level GA4 engagement/conversion data,
    persist it as MetricSnapshots, and (re)generate engagement/exit-rate/mobile-share/
    key-event tasks against the site's configured benchmarks. Idempotent on tasks
    (old "ga4" tasks are cleared first), same pattern as sync_gsc_and_generate_tasks.

    Like GSC tasks, these are scheduled from *today* -- they reflect the site's
    current behavioral state, not a one-time backlog anchored to campaign start.

    Also fetches/upserts site-wide daily sessions+active_users totals
    (SiteMetricDaily) over a wider window, feeding any configured VolumeBenchmark
    rows -- unrelated to the per-page task generation above.
    """
    site = db.get(Site, site_id)
    connection = find_connection(db, site_id, "ga4")
    if not connection:
        raise NoConnectionError(f"Site {site_id} has no GA4 connection yet")
    if not site.ga4_property_id:
        raise NoConnectionError(f"Site {site_id} has no GA4 property ID configured")

    access_token = get_valid_access_token(connection)  # may mutate + need saving if refreshed
    db.commit()

    end_date = dt.date.today() - dt.timedelta(days=1)  # GA4 standard reports lag ~1 day, unlike GSC's ~3
    start_date = end_date - dt.timedelta(days=27)
    page_rows = fetch_page_metrics(access_token, site.ga4_property_id, start_date, end_date)
    mobile_share_by_page = fetch_mobile_share(access_token, site.ga4_property_id, start_date, end_date)

    # Separate, wider-window fetch feeding VolumeBenchmark's site-wide daily/weekly/
    # monthly trend checks -- unrelated to the page-level rows/tasks above, which stay
    # on their existing 28-day window.
    site_totals = fetch_ga4_site_totals(
        access_token, site.ga4_property_id, end_date - dt.timedelta(days=VOLUME_TREND_LOOKBACK_DAYS), end_date
    )
    _upsert_site_metric_daily(db, site_id, "ga4", site_totals, ["sessions", "active_users"])

    month = start_date.replace(day=1)
    for row in page_rows:
        snapshot_metrics = {
            "engagement_rate": row["engagement_rate"],
            "exit_rate": row["bounce_rate"],
            "key_events": (row["key_events"] / row["sessions"]) if row["sessions"] else 0.0,
        }
        if row["page"] in mobile_share_by_page:
            snapshot_metrics["mobile_share"] = mobile_share_by_page[row["page"]]
        for metric_key, value in snapshot_metrics.items():
            db.add(
                MetricSnapshot(
                    site_id=site_id,
                    source="ga4",
                    url=row["page"],
                    month=month,
                    metric_key=metric_key,
                    value=value,
                    extra={"sessions": row["sessions"]},
                )
            )

    benchmark_rows = db.scalars(
        select(Benchmark).where(
            Benchmark.site_id == site_id,
            Benchmark.metric_key.in_(["engagement_rate", "exit_rate", "mobile_share", "key_events"]),
        )
    ).all()
    # site-wide only (segment is null for all four GA4 benchmarks) -- last one wins if duplicated.
    benchmarks_by_metric = {b.metric_key: b.target_value for b in benchmark_rows}

    filtered_page_rows, filtered_mobile_share = apply_ga4_filters(site, page_rows, mobile_share_by_page)
    generated = generate_ga4_tasks(filtered_page_rows, filtered_mobile_share, benchmarks_by_metric)

    db.query(Task).filter(Task.site_id == site_id, Task.source == "ga4").delete(synchronize_session="fetch")
    db.flush()

    campaign = db.query(Campaign).filter(Campaign.site_id == site_id).order_by(Campaign.start_date.desc()).first()
    latest_import = (
        db.query(CrawlImport).filter(CrawlImport.site_id == site_id).order_by(CrawlImport.imported_at.desc()).first()
    )
    site_scale = latest_import.site_scale if latest_import else "small"
    capacity = campaign.capacity_per_week if campaign else 5
    duration_months = campaign.duration_months if campaign else 6
    default_assignee = campaign.default_assignee if campaign else None

    scheduled = assign_schedule(
        generated, dt.date.today(), capacity, site_scale=site_scale, duration_months=duration_months,
        excluded_dates=_dates_occupied_by_other_sources(db, site_id, "ga4"),
    )

    for t in scheduled:
        db.add(
            Task(
                site_id=site_id,
                source=t.source,
                category=t.category,
                title=t.title,
                description=t.description,
                affected_urls=t.affected_urls,
                url_details=t.url_details or {},
                severity=t.severity,
                metric_actual=t.metric_actual,
                metric_benchmark=t.metric_benchmark,
                month_index=t.month_index,
                target_date=t.target_date,
                effort_tier=t.effort_tier,
                optimization_level=default_optimization_level(t.category),
                estimated_hours=estimated_hours_for(t.category),
                status="todo",
                assignee=default_assignee,
            )
        )

    db.commit()
    reschedule_all_tasks(db, site_id)
    return {"pages_synced": len(page_rows), "tasks_generated": len(scheduled)}
