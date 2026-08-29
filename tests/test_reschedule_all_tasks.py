"""Direct coverage of the unified capacity-based phase scheduler
(services.reschedule_all_tasks) -- the mechanism that replaced four independent
per-source schedulers after they proved unable to coordinate with each other
(same-day collisions across sources, Key Fix items scattering months apart
instead of clustering right after technical work). Tests build Task rows
directly rather than going through full crawl/gsc/ga4 generation, so they
isolate the scheduler's ordering logic from the rule engines that produce these
tasks.

Since 2026-08-27 this packs tasks into 8-hour business days by real per-category
estimated_hours (see app/rules/task_hours.py) rather than one task per week --
tests below that check ORDER use a heavy `estimated_hours` override (5h) so two
tasks can never share a day, keeping strict "A happened before B" assertions
meaningful; tests checking the packing/capacity behavior itself use the default
1h weight deliberately, since same-day sharing between cheap tasks is the actual
point of this model, not a bug to route around.
"""
import datetime as dt

from app.models import Campaign, Site, Task
from app.services import reschedule_all_tasks


def _site_with_campaign(db_session, start_date=dt.date(2026, 9, 1), **overrides):
    site = Site(domain="example.com")
    db_session.add(site)
    db_session.flush()
    overrides.setdefault("duration_months", 6)
    db_session.add(Campaign(site_id=site.id, start_date=start_date, **overrides))
    db_session.commit()
    return site


def _task(site_id, category, optimization_level, **overrides):
    defaults = dict(
        source="crawl", title=category, description="", severity="medium", status="todo",
    )
    defaults.update(overrides)
    return Task(site_id=site_id, category=category, optimization_level=optimization_level, **defaults)


def test_phases_run_in_order_benchmarking_key_fix_quick_win_ongoing_content(db_session):
    site = _site_with_campaign(db_session)
    db_session.add_all([
        _task(site.id, "content_creation", "ongoing_content", estimated_hours=5),
        _task(site.id, "meta_tag_reoptimization", "quick_win", estimated_hours=5),
        _task(site.id, "technical_audit", "key_fix", estimated_hours=5),
        _task(site.id, "prompt_keyword_benchmarking", "benchmarking", estimated_hours=5),
    ])
    db_session.commit()

    reschedule_all_tasks(db_session, site.id)

    by_category = {t.category: t.target_date for t in db_session.query(Task).filter(Task.site_id == site.id)}
    assert (
        by_category["prompt_keyword_benchmarking"]
        < by_category["technical_audit"]
        < by_category["meta_tag_reoptimization"]
        < by_category["content_creation"]
    )


def test_technical_audit_leads_key_fix_then_ga4_checks_follow(db_session):
    site = _site_with_campaign(db_session)
    db_session.add_all([
        _task(site.id, "low_key_events", "key_fix", source="ga4", estimated_hours=5),
        _task(site.id, "low_mobile_share", "key_fix", source="ga4", estimated_hours=5),
        _task(site.id, "ui_ux_review", "key_fix", source="ga4", estimated_hours=5),
        _task(site.id, "technical_audit", "key_fix", source="crawl", estimated_hours=5),
    ])
    db_session.commit()

    reschedule_all_tasks(db_session, site.id)

    by_category = {t.category: t.target_date for t in db_session.query(Task).filter(Task.site_id == site.id)}
    assert by_category["technical_audit"] < by_category["ui_ux_review"]
    assert by_category["ui_ux_review"] < by_category["low_mobile_share"]
    assert by_category["ui_ux_review"] < by_category["low_key_events"]


def test_url_structure_optimization_follows_technical_audit_directly(db_session):
    site = _site_with_campaign(db_session)
    db_session.add_all([
        _task(site.id, "ui_ux_review", "key_fix", source="ga4", estimated_hours=5),
        _task(site.id, "url_structure_optimization", "key_fix", source="content_plan", estimated_hours=5),
        _task(site.id, "technical_audit", "key_fix", source="crawl", estimated_hours=5),
    ])
    db_session.commit()

    reschedule_all_tasks(db_session, site.id)

    by_category = {t.category: t.target_date for t in db_session.query(Task).filter(Task.site_id == site.id)}
    assert by_category["technical_audit"] < by_category["url_structure_optimization"] < by_category["ui_ux_review"]


def test_quick_win_order_meta_then_exits_then_ctr(db_session):
    site = _site_with_campaign(db_session)
    db_session.add_all([
        _task(site.id, "ctr_optimization", "quick_win", source="gsc", estimated_hours=5),
        _task(site.id, "high_exit_rate", "quick_win", source="ga4", estimated_hours=5),
        _task(site.id, "meta_tag_reoptimization", "quick_win", source="gsc", estimated_hours=5),
    ])
    db_session.commit()

    reschedule_all_tasks(db_session, site.id)

    by_category = {t.category: t.target_date for t in db_session.query(Task).filter(Task.site_id == site.id)}
    assert by_category["meta_tag_reoptimization"] < by_category["high_exit_rate"] < by_category["ctr_optimization"]


def test_tasks_pack_into_shared_days_without_exceeding_daily_capacity(db_session):
    """The actual point of the capacity model, replacing the old "one task per week,
    guaranteed no collisions" guarantee: 10 tasks at the default 1-hour weight pack
    8 onto day one (the daily cap) and spill the remaining 2 onto day two, rather
    than each claiming its own week."""
    site = _site_with_campaign(db_session)
    db_session.add_all([_task(site.id, f"custom_{i}", "quick_win", estimated_hours=1.0) for i in range(10)])
    db_session.commit()

    reschedule_all_tasks(db_session, site.id)

    tasks = db_session.query(Task).filter(Task.site_id == site.id).all()
    assert all(t.target_date is not None for t in tasks)
    assert all(t.target_date.weekday() < 5 for t in tasks)

    hours_by_day: dict = {}
    for t in tasks:
        hours_by_day[t.target_date] = hours_by_day.get(t.target_date, 0.0) + t.estimated_hours
    assert all(total <= 8.0 for total in hours_by_day.values())
    assert len(hours_by_day) == 2  # 10 x 1h: 8 fit day one, 2 spill to day two
    assert sorted(hours_by_day.values()) == [2.0, 8.0]


def test_anchors_to_today_not_an_ancient_campaign_start_date(db_session):
    # a campaign that started years ago shouldn't retroactively schedule fresh findings
    # into the past
    site = _site_with_campaign(db_session, start_date=dt.date(2020, 1, 1))
    db_session.add(_task(site.id, "meta_tag_reoptimization", "quick_win"))
    db_session.commit()

    reschedule_all_tasks(db_session, site.id)

    task = db_session.query(Task).filter(Task.site_id == site.id).one()
    assert task.target_date >= dt.date.today()


def test_anchors_to_future_campaign_start_when_not_yet_begun(db_session):
    future_start = dt.date.today() + dt.timedelta(days=60)
    site = _site_with_campaign(db_session, start_date=future_start)
    db_session.add(_task(site.id, "technical_audit", "key_fix"))
    db_session.commit()

    reschedule_all_tasks(db_session, site.id)

    task = db_session.query(Task).filter(Task.site_id == site.id).one()
    assert task.target_date >= future_start


def test_missing_optimization_level_falls_back_to_category_default(db_session):
    site = _site_with_campaign(db_session)
    db_session.add_all([
        # content_expansion (GSC-sourced) rather than a content_plan category here --
        # content_plan's own ongoing_content tasks are excluded from this queue
        # entirely (see test_content_plan_tasks_are_left_untouched_not_rescheduled).
        _task(site.id, "content_expansion", None, source="gsc", estimated_hours=5),
        _task(site.id, "technical_audit", None, source="crawl", estimated_hours=5),
    ])
    db_session.commit()

    reschedule_all_tasks(db_session, site.id)

    by_category = {t.category: t.target_date for t in db_session.query(Task).filter(Task.site_id == site.id)}
    # technical_audit defaults to key_fix, content_expansion to ongoing_content --
    # key_fix still runs first even with no optimization_level explicitly set
    assert by_category["technical_audit"] < by_category["content_expansion"]


def test_no_campaign_is_a_safe_noop(db_session):
    site = Site(domain="no-campaign.com")
    db_session.add(site)
    db_session.commit()
    db_session.add(_task(site.id, "technical_audit", "key_fix"))
    db_session.commit()

    reschedule_all_tasks(db_session, site.id)  # must not raise

    task = db_session.query(Task).filter(Task.site_id == site.id).one()
    assert task.target_date is None  # untouched, since there's no campaign to anchor to


def test_content_plan_pacing_starts_only_after_the_backlog_clears(db_session):
    """Regression, reported directly against real data: content-plan tasks (topic
    research/brief/article/llm/page-optimization) used to keep generate_content_plan's
    own campaign-start-anchored dates completely untouched -- which meant they ran in
    parallel with, or even before, the one-time backlog, violating the analyst's own
    hierarchy (benchmarking -> technical_audit -> setup_reporting -> key_fix ->
    quick_win -> ongoing_content) it was supposed to respect. They must now be
    reflowed (not left as-is) to start strictly after the backlog's last date --
    while still preserving their own internal order (research < brief < creation) and
    staying stable across a second, later call (e.g. a subsequent GA4 sync
    re-triggering this for unrelated reasons)."""
    site = _site_with_campaign(db_session)
    db_session.add_all([
        _task(site.id, "content_topic_research", "ongoing_content", source="content_plan",
              target_date=dt.date(2026, 9, 3), month_index=0, estimated_hours=5),
        _task(site.id, "content_brief_finalization", "ongoing_content", source="content_plan",
              target_date=dt.date(2026, 9, 10), month_index=0, estimated_hours=5),
        _task(site.id, "content_creation", "ongoing_content", source="content_plan",
              target_date=dt.date(2026, 9, 17), month_index=0, estimated_hours=5),
    ])
    db_session.add(_task(site.id, "technical_audit", "key_fix", source="crawl", estimated_hours=5))
    db_session.commit()

    reschedule_all_tasks(db_session, site.id)
    first_pass = {t.category: t.target_date for t in db_session.query(Task).filter(Task.site_id == site.id)}

    backlog_end = first_pass["technical_audit"]
    assert first_pass["content_topic_research"] > backlog_end  # strictly after, never same-day or before
    assert (
        first_pass["content_topic_research"]
        < first_pass["content_brief_finalization"]
        < first_pass["content_creation"]
    )

    reschedule_all_tasks(db_session, site.id)  # simulate a later, unrelated sync re-triggering this
    second_pass = {t.category: t.target_date for t in db_session.query(Task).filter(Task.site_id == site.id)}
    assert first_pass == second_pass  # stable, doesn't drift further on repeated calls


def test_heavy_one_time_backlog_leaves_content_plan_unscheduled_rather_than_overlapping(db_session):
    """Phase-ordered, bounded to the campaign's configured duration -- a heavy
    one-time backlog (technical/GSC/GA4 findings) that eats most of a short
    campaign's capacity must not push content-plan work BEFORE it. Whatever
    content-plan work doesn't fit in what's left is simply left unscheduled
    (target_date/month_index None) rather than getting compressed to squeeze in.
    Landing on the SAME day as the tail end of the backlog is fine now (real
    capacity left that day) -- see test_tasks_pack_into_shared_days_without_
    exceeding_daily_capacity for why that's the point, not a bug; landing BEFORE
    any backlog date would still be a real ordering violation."""
    site = _site_with_campaign(db_session, duration_months=1)  # tiny window, heavy backlog
    db_session.add_all([_task(site.id, "page_optimization", "ongoing_content", source="content_plan") for _ in range(3)])
    db_session.add_all([_task(site.id, f"custom_{i}", "quick_win") for i in range(20)])  # eats most of the capacity
    db_session.commit()

    reschedule_all_tasks(db_session, site.id)

    backlog_dates = [
        t.target_date
        for t in db_session.query(Task).filter(Task.site_id == site.id, Task.source != "content_plan")
        if t.target_date
    ]
    page_opt = db_session.query(Task).filter(Task.site_id == site.id, Task.category == "page_optimization").all()
    for t in page_opt:
        # either it got no slot at all (deferred to next renewal), or it landed on
        # or after every backlog date that did get scheduled -- never before
        assert t.target_date is None or all(t.target_date >= d for d in backlog_dates)


def test_full_hierarchy_research_audit_keyfix_quickwin_ongoing(db_session):
    """The analyst's explicitly confirmed hierarchy, end to end: Research &
    Benchmarking < Technical Audit < (the rest of) Key Fixes < Quick Wins <
    Ongoing Content -- as strict, non-overlapping phases (forced onto separate days
    here via a heavy estimated_hours override -- see the module docstring). Under the
    capacity model this no longer means exactly one week apart -- see
    test_tasks_pack_into_shared_days_without_exceeding_daily_capacity for that
    behavior -- just that phase order is never violated and no day exceeds capacity.
    (Reporting -- performance_dashboard/weekly_report/monthly_report_mbr -- is
    calendar-anchored separately by generate_reporting_tasks and untouched here; see
    test_reporting_tasks_are_not_touched_by_the_sequential_queue.)"""
    site = _site_with_campaign(db_session)
    db_session.add_all([
        _task(site.id, "prompt_keyword_benchmarking", "benchmarking", source="content_plan", estimated_hours=5),
        _task(site.id, "technical_audit", "key_fix", source="crawl", estimated_hours=5),
        _task(site.id, "indexation_blocking", "key_fix", source="crawl", estimated_hours=5),
        _task(site.id, "ui_ux_review", "key_fix", source="ga4", estimated_hours=5),
        _task(site.id, "meta_tag_reoptimization", "quick_win", source="gsc", estimated_hours=5),
        _task(site.id, "content_expansion", "ongoing_content", source="gsc", estimated_hours=5),
    ])
    db_session.add_all([
        _task(site.id, "content_topic_research", "ongoing_content", source="content_plan", estimated_hours=5),
        _task(site.id, "page_optimization", "ongoing_content", source="content_plan", estimated_hours=5),
    ])
    db_session.commit()

    reschedule_all_tasks(db_session, site.id)

    by_category = {t.category: t.target_date for t in db_session.query(Task).filter(Task.site_id == site.id)}
    assert (
        by_category["prompt_keyword_benchmarking"]
        < by_category["technical_audit"]
        < by_category["indexation_blocking"]
        < by_category["ui_ux_review"]
        < by_category["meta_tag_reoptimization"]
        < by_category["content_expansion"]
    )
    # content-plan's own recurring work starts only once ALL of the above (the
    # one-time backlog) has cleared, not campaign-start-anchored independently of it
    assert by_category["content_topic_research"] > by_category["meta_tag_reoptimization"]
    assert by_category["page_optimization"] >= by_category["content_topic_research"]
    # strictly increasing throughout -- no compression, no gaps, no two tasks sharing
    # a day (guaranteed here by the heavy per-task hours, not by the scheduler itself)
    all_dates = sorted(by_category.values())
    assert all_dates == sorted(set(all_dates))
    assert all(d.weekday() < 5 for d in all_dates)


# ---------- reporting tasks are calendar-anchored, left alone by the sequential queue ----------


def test_reporting_tasks_are_not_touched_by_the_sequential_queue(db_session):
    site = _site_with_campaign(db_session)
    fixed_date = dt.date(2026, 10, 28)
    db_session.add_all([
        _task(site.id, "technical_audit", "key_fix", source="crawl"),
        _task(
            site.id, "weekly_report", "reporting", source="content_plan",
            target_date=fixed_date, month_index=1,
        ),
    ])
    db_session.commit()

    reschedule_all_tasks(db_session, site.id)

    report = db_session.query(Task).filter(Task.site_id == site.id, Task.category == "weekly_report").one()
    assert report.target_date == fixed_date  # untouched


def test_sequential_queue_never_lands_on_a_wednesday_even_if_campaign_starts_on_one(db_session):
    wednesday_start = dt.date(2026, 9, 2)  # a Wednesday
    assert wednesday_start.weekday() == 2
    site = _site_with_campaign(db_session, start_date=wednesday_start)
    db_session.add_all([_task(site.id, f"custom_{i}", "quick_win") for i in range(5)])
    db_session.commit()

    reschedule_all_tasks(db_session, site.id)

    dates = [t.target_date for t in db_session.query(Task).filter(Task.site_id == site.id)]
    assert all(d.weekday() != 2 for d in dates)


# ---------- schema_recommendations piggybacks on technical_audit's day ----------


def test_schema_recommendations_shares_technical_audits_day_when_capacity_allows(db_session):
    """Real hours (technical_audit=3h, schema_recommendations=0.75h -- see
    app/rules/task_hours.py) comfortably fit on the same day (3.75h of 8h), so this is
    the normal case now -- no Thursday/Friday dance needed at all."""
    site = _site_with_campaign(db_session, start_date=dt.date(2026, 9, 7))
    db_session.add_all([
        _task(site.id, "prompt_keyword_benchmarking", "benchmarking", source="content_plan", estimated_hours=3.0),
        _task(site.id, "technical_audit", "key_fix", source="crawl", estimated_hours=3.0),
        _task(site.id, "schema_recommendations", "key_fix", source="content_plan", estimated_hours=0.75),
    ])
    db_session.commit()

    reschedule_all_tasks(db_session, site.id)

    by_category = {t.category: t.target_date for t in db_session.query(Task).filter(Task.site_id == site.id)}
    assert by_category["schema_recommendations"] == by_category["technical_audit"]


def test_schema_recommendations_spills_to_the_next_day_if_technical_audits_day_is_full(db_session):
    """technical_audit's day already has other work using up its capacity (7.5h) --
    schema_recommendations (1h) doesn't fit alongside it (8.5h > the 8h cap), so it
    spills to the next available day instead, found the exact same way as any other
    task (see _assign_next_available_slot) -- no special-cased fallback day needed."""
    site = _site_with_campaign(db_session)
    db_session.add_all([
        _task(site.id, "technical_audit", "key_fix", source="crawl", estimated_hours=7.5),
        _task(site.id, "schema_recommendations", "key_fix", source="content_plan", estimated_hours=1.0),
    ])
    db_session.commit()

    reschedule_all_tasks(db_session, site.id)

    by_category = {t.category: t.target_date for t in db_session.query(Task).filter(Task.site_id == site.id)}
    assert by_category["schema_recommendations"] != by_category["technical_audit"]
    assert by_category["schema_recommendations"] > by_category["technical_audit"]


def test_schema_recommendations_still_finds_room_under_a_heavy_backlog(db_session):
    """Regardless of how much other quick_win work is competing for space, schema_
    recommendations still lands on technical_audit's own day if there's room left in
    it (real hours mean there almost always is), or the next day with room --
    verified here by just confirming it lands somewhere valid (a weekday, on or after
    technical_audit's day) even with 20 other tasks in the mix."""
    site = _site_with_campaign(db_session, duration_months=1)
    tasks = [_task(site.id, "technical_audit", "key_fix", source="crawl", estimated_hours=3.0)]
    tasks += [_task(site.id, "schema_recommendations", "key_fix", source="content_plan", estimated_hours=0.75)]
    tasks += [_task(site.id, f"custom_{i}", "quick_win", estimated_hours=1.0) for i in range(20)]
    db_session.add_all(tasks)
    db_session.commit()

    reschedule_all_tasks(db_session, site.id)

    by_category = {t.category: t.target_date for t in db_session.query(Task).filter(Task.site_id == site.id)}
    assert by_category["schema_recommendations"] is not None
    assert by_category["schema_recommendations"].weekday() < 5
    assert by_category["schema_recommendations"] >= by_category["technical_audit"]


def test_schema_recommendations_has_no_date_if_technical_audit_is_absent(db_session):
    site = _site_with_campaign(db_session)
    db_session.add(_task(site.id, "schema_recommendations", "key_fix", source="content_plan"))
    db_session.commit()

    reschedule_all_tasks(db_session, site.id)  # must not raise

    task = db_session.query(Task).filter(Task.site_id == site.id).one()
    assert task.target_date is None


# ---------- manually_scheduled tasks are left alone by later reschedule calls
# (see the due-date route / chat tool, both of which set this flag) ----------


def test_manually_scheduled_task_is_left_alone_by_a_later_reschedule(db_session):
    """The whole point of the flag: a human moved this task on purpose (e.g. to work
    around something off-platform), and a later sync/import re-triggering
    reschedule_all_tasks for unrelated reasons must not silently move it back."""
    site = _site_with_campaign(db_session)
    pinned_date = dt.date(2026, 12, 25)  # deliberately far from where auto-scheduling would put it
    db_session.add_all([
        _task(site.id, "technical_audit", "key_fix", source="crawl", estimated_hours=3.0,
              target_date=pinned_date, month_index=3, manually_scheduled=True),
        _task(site.id, "meta_tag_reoptimization", "quick_win", estimated_hours=1.0),
    ])
    db_session.commit()

    reschedule_all_tasks(db_session, site.id)

    task = db_session.query(Task).filter(Task.site_id == site.id, Task.category == "technical_audit").one()
    assert task.target_date == pinned_date  # untouched
    assert task.month_index == 3


def test_manually_scheduled_tasks_hours_still_count_against_that_days_capacity(db_session):
    """A human pinned a heavy task to a specific day -- autoscheduling must not stack
    more work on top of it past the daily cap, even though the pinned task itself is
    never touched."""
    site = _site_with_campaign(db_session)
    pinned_date = dt.date(2026, 9, 1)
    db_session.add_all([
        _task(site.id, "technical_audit", "key_fix", source="crawl", estimated_hours=7.0,
              target_date=pinned_date, month_index=0, manually_scheduled=True),
        # 3h of auto-scheduled quick_win work -- 7h (pinned) + 3h would exceed 8h if
        # packed onto the same day, so it must spill to the next day instead.
        _task(site.id, "meta_tag_reoptimization", "quick_win", estimated_hours=3.0),
    ])
    db_session.commit()

    reschedule_all_tasks(db_session, site.id)

    technical_audit = db_session.query(Task).filter(Task.site_id == site.id, Task.category == "technical_audit").one()
    meta_tag = db_session.query(Task).filter(Task.site_id == site.id, Task.category == "meta_tag_reoptimization").one()
    assert technical_audit.target_date == pinned_date  # untouched
    assert meta_tag.target_date != pinned_date  # didn't get stacked past capacity
    assert meta_tag.target_date > pinned_date
