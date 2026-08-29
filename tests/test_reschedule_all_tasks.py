"""Direct coverage of the unified weekly phase scheduler (services.reschedule_all_tasks)
-- the mechanism that replaced four independent per-source schedulers after they proved
unable to coordinate with each other (same-day collisions across sources, Key Fix items
scattering months apart instead of clustering right after technical work). Tests build
Task rows directly rather than going through full crawl/gsc/ga4 generation, so they
isolate the scheduler's ordering logic from the rule engines that produce these tasks.
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
        _task(site.id, "content_creation", "ongoing_content"),
        _task(site.id, "meta_tag_reoptimization", "quick_win"),
        _task(site.id, "technical_audit", "key_fix"),
        _task(site.id, "prompt_keyword_benchmarking", "benchmarking"),
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
        _task(site.id, "low_key_events", "key_fix", source="ga4"),
        _task(site.id, "low_mobile_share", "key_fix", source="ga4"),
        _task(site.id, "ui_ux_review", "key_fix", source="ga4"),
        _task(site.id, "technical_audit", "key_fix", source="crawl"),
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
        _task(site.id, "ui_ux_review", "key_fix", source="ga4"),
        _task(site.id, "url_structure_optimization", "key_fix", source="content_plan"),
        _task(site.id, "technical_audit", "key_fix", source="crawl"),
    ])
    db_session.commit()

    reschedule_all_tasks(db_session, site.id)

    by_category = {t.category: t.target_date for t in db_session.query(Task).filter(Task.site_id == site.id)}
    assert by_category["technical_audit"] < by_category["url_structure_optimization"] < by_category["ui_ux_review"]


def test_quick_win_order_meta_then_exits_then_ctr(db_session):
    site = _site_with_campaign(db_session)
    db_session.add_all([
        _task(site.id, "ctr_optimization", "quick_win", source="gsc"),
        _task(site.id, "high_exit_rate", "quick_win", source="ga4"),
        _task(site.id, "meta_tag_reoptimization", "quick_win", source="gsc"),
    ])
    db_session.commit()

    reschedule_all_tasks(db_session, site.id)

    by_category = {t.category: t.target_date for t in db_session.query(Task).filter(Task.site_id == site.id)}
    assert by_category["meta_tag_reoptimization"] < by_category["high_exit_rate"] < by_category["ctr_optimization"]


def test_every_task_lands_on_its_own_week_no_collisions(db_session):
    site = _site_with_campaign(db_session)
    db_session.add_all([_task(site.id, f"custom_{i}", "quick_win") for i in range(10)])
    db_session.commit()

    reschedule_all_tasks(db_session, site.id)

    dates = [t.target_date for t in db_session.query(Task).filter(Task.site_id == site.id)]
    assert len(dates) == len(set(dates))
    assert all(d.weekday() < 5 for d in dates)


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
        _task(site.id, "content_expansion", None, source="gsc"),
        _task(site.id, "technical_audit", None, source="crawl"),
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
              target_date=dt.date(2026, 9, 3), month_index=0),
        _task(site.id, "content_brief_finalization", "ongoing_content", source="content_plan",
              target_date=dt.date(2026, 9, 10), month_index=0),
        _task(site.id, "content_creation", "ongoing_content", source="content_plan",
              target_date=dt.date(2026, 9, 17), month_index=0),
    ])
    db_session.add(_task(site.id, "technical_audit", "key_fix", source="crawl"))
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
    """One task per week, phase-ordered, bounded to the campaign's configured
    duration -- a heavy one-time backlog (technical/GSC/GA4 findings) that eats
    every available week within a short campaign must not push content-plan work
    into parallel with (or before) it. Whatever content-plan work doesn't fit in the
    remaining weeks is simply left unscheduled (target_date/month_index None)
    rather than overlapping the backlog or getting compressed to squeeze in."""
    site = _site_with_campaign(db_session, duration_months=1)  # tiny window, heavy backlog
    db_session.add_all([_task(site.id, "page_optimization", "ongoing_content", source="content_plan") for _ in range(3)])
    db_session.add_all([_task(site.id, f"custom_{i}", "quick_win") for i in range(20)])  # eats every available week
    db_session.commit()

    reschedule_all_tasks(db_session, site.id)

    backlog_dates = [
        t.target_date
        for t in db_session.query(Task).filter(Task.site_id == site.id, Task.source != "content_plan")
        if t.target_date
    ]
    page_opt = db_session.query(Task).filter(Task.site_id == site.id, Task.category == "page_optimization").all()
    for t in page_opt:
        # either it got no slot at all (deferred to next renewal), or it landed
        # strictly after every backlog date that did get scheduled
        assert t.target_date is None or all(t.target_date > d for d in backlog_dates)


def test_full_hierarchy_research_audit_keyfix_quickwin_ongoing(db_session):
    """The analyst's explicitly confirmed hierarchy, end to end: Research &
    Benchmarking < Technical Audit < (the rest of) Key Fixes < Quick Wins <
    Ongoing Content -- as strict, non-overlapping phases, exactly one task per week
    straight through, no gaps and no compression. (Reporting --
    performance_dashboard/weekly_report/monthly_report_mbr -- is calendar-anchored
    separately by generate_reporting_tasks and untouched here; see
    test_reporting_tasks_are_not_touched_by_the_sequential_queue.)"""
    site = _site_with_campaign(db_session)
    db_session.add_all([
        _task(site.id, "prompt_keyword_benchmarking", "benchmarking", source="content_plan"),
        _task(site.id, "technical_audit", "key_fix", source="crawl"),
        _task(site.id, "indexation_blocking", "key_fix", source="crawl"),
        _task(site.id, "ui_ux_review", "key_fix", source="ga4"),
        _task(site.id, "meta_tag_reoptimization", "quick_win", source="gsc"),
        _task(site.id, "content_expansion", "ongoing_content", source="gsc"),
    ])
    db_session.add_all([
        _task(site.id, "content_topic_research", "ongoing_content", source="content_plan"),
        _task(site.id, "page_optimization", "ongoing_content", source="content_plan"),
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
    # exactly one week apart, straight through -- no compression, no gaps
    all_dates = sorted(by_category.values())
    for earlier, later in zip(all_dates, all_dates[1:]):
        assert (later - earlier).days == 7


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


# ---------- schema_recommendations piggybacks on technical_audit's week ----------


def test_schema_recommendations_lands_on_thursday_of_technical_audits_week(db_session):
    # A Monday start (rather than the fixture default, a Tuesday) so technical_audit
    # -- the 2nd item packed at capacity, right after benchmarking -- lands on a
    # Tuesday, not a Thursday: this test covers the NORMAL case, distinct from
    # test_schema_recommendations_falls_back_to_friday_if_technical_audit_lands_on_thursday
    # below, which deliberately forces the Thursday-collision edge case instead.
    site = _site_with_campaign(db_session, start_date=dt.date(2026, 9, 7))
    db_session.add_all([
        _task(site.id, "prompt_keyword_benchmarking", "benchmarking", source="content_plan"),
        _task(site.id, "technical_audit", "key_fix", source="crawl"),
        _task(site.id, "schema_recommendations", "key_fix", source="content_plan"),
    ])
    db_session.commit()

    reschedule_all_tasks(db_session, site.id)

    by_category = {t.category: t.target_date for t in db_session.query(Task).filter(Task.site_id == site.id)}
    assert by_category["schema_recommendations"].weekday() == 3  # Thursday
    ta_monday = by_category["technical_audit"] - dt.timedelta(days=by_category["technical_audit"].weekday())
    schema_monday = by_category["schema_recommendations"] - dt.timedelta(days=3)
    assert ta_monday == schema_monday  # same calendar week


def test_schema_recommendations_falls_back_to_friday_if_technical_audit_lands_on_thursday(db_session):
    # force technical_audit onto a Thursday by starting the campaign on one
    thursday_start = dt.date(2026, 9, 3)
    assert thursday_start.weekday() == 3
    site = _site_with_campaign(db_session, start_date=thursday_start)
    db_session.add_all([
        _task(site.id, "technical_audit", "key_fix", source="crawl"),
        _task(site.id, "schema_recommendations", "key_fix", source="content_plan"),
    ])
    db_session.commit()

    reschedule_all_tasks(db_session, site.id)

    by_category = {t.category: t.target_date for t in db_session.query(Task).filter(Task.site_id == site.id)}
    assert by_category["technical_audit"].weekday() == 3  # confirms the forced setup
    assert by_category["schema_recommendations"].weekday() == 4  # Friday fallback
    assert by_category["schema_recommendations"] != by_category["technical_audit"]


def test_schema_recommendations_never_collides_even_under_a_heavy_backlog(db_session):
    """Every week_slot shares the exact same day-of-week (nth_week_business_day
    always spaces them exactly 7 days apart -- see _week_slot_date), so
    schema_recommendations' derived date (Thursday, or Friday if technical_audit
    itself landed on Thursday) can only ever coincide with technical_audit's OWN
    slot -- handled by that fallback -- never with a different week's task. True
    regardless of how heavy the rest of the backlog is, so no reserve/bump
    mechanism is needed at all."""
    site = _site_with_campaign(db_session, duration_months=1)
    tasks = [_task(site.id, "technical_audit", "key_fix", source="crawl")]
    tasks += [_task(site.id, "schema_recommendations", "key_fix", source="content_plan")]
    tasks += [_task(site.id, f"custom_{i}", "quick_win") for i in range(20)]
    db_session.add_all(tasks)
    db_session.commit()

    reschedule_all_tasks(db_session, site.id)

    all_tasks = db_session.query(Task).filter(Task.site_id == site.id).all()
    dates = [t.target_date for t in all_tasks if t.target_date is not None]
    assert len(dates) == len(set(dates))  # zero collisions, however heavy the backlog


def test_schema_recommendations_has_no_date_if_technical_audit_is_absent(db_session):
    site = _site_with_campaign(db_session)
    db_session.add(_task(site.id, "schema_recommendations", "key_fix", source="content_plan"))
    db_session.commit()

    reschedule_all_tasks(db_session, site.id)  # must not raise

    task = db_session.query(Task).filter(Task.site_id == site.id).one()
    assert task.target_date is None
