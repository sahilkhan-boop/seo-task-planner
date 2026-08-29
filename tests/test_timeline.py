import datetime as dt

from app.rules.crawl_rules import GeneratedTask
from app.scheduling.timeline import assign_schedule


def _task(category, severity, n_urls=1):
    return GeneratedTask(
        source="crawl",
        category=category,
        title=f"{category}-{severity}",
        description="",
        affected_urls=[f"u{i}" for i in range(n_urls)],
        severity=severity,
    )


def test_technical_fixes_scheduled_before_content_optimization():
    tasks = [
        _task("ctr_optimization", "medium"),
        _task("404_fix", "high"),
    ]
    start = dt.date(2026, 9, 1)  # a Tuesday
    scheduled = assign_schedule(tasks, start, capacity_per_week=5)
    assert scheduled[0].category == "404_fix"
    assert scheduled[1].category == "ctr_optimization"


def test_tasks_land_on_weekdays_only():
    tasks = [_task("404_fix", "high") for _ in range(10)]
    start = dt.date(2026, 9, 1)
    scheduled = assign_schedule(tasks, start, capacity_per_week=5)
    for t in scheduled:
        assert t.target_date.weekday() < 5


def test_month_index_is_zero_based_from_campaign_start():
    tasks = [_task("404_fix", "high")]
    start = dt.date(2026, 8, 15)
    [scheduled] = assign_schedule(tasks, start, capacity_per_week=5)
    assert scheduled.month_index == 0
    assert scheduled.target_date >= start


def test_capacity_per_week_controls_how_many_land_per_day():
    tasks = [_task("404_fix", "high") for _ in range(10)]
    start = dt.date(2026, 9, 1)
    scheduled = assign_schedule(tasks, start, capacity_per_week=10)  # 2/day
    dates = [t.target_date for t in scheduled]
    assert dates[0] == dates[1]
    assert dates[2] == dates[3]
    assert dates[0] != dates[2]


def test_workflow_priority_order_matches_approved_pdf():
    """indexation_blocking > server_error > high-404 > redirect cleanup > low-404 > ctr."""
    tasks = [
        _task("ctr_optimization", "medium"),
        _task("404_fix", "medium"),          # low-impact 404 -> tier 4
        _task("redirect_inlink_update", "high"),  # tier 3
        _task("404_fix", "high"),            # high-impact 404 -> tier 2
        _task("server_error", "high"),       # tier 1
        _task("indexation_blocking", "high"),  # tier 0
    ]
    scheduled = assign_schedule(tasks, dt.date(2026, 9, 1), capacity_per_week=5)
    ordered_categories = [(t.category, t.severity) for t in scheduled]
    assert ordered_categories == [
        ("indexation_blocking", "high"),
        ("server_error", "high"),
        ("404_fix", "high"),
        ("redirect_inlink_update", "high"),
        ("404_fix", "medium"),
        ("ctr_optimization", "medium"),
    ]


def test_large_site_gates_redirect_cleanup_to_month_two():
    tasks = [_task("server_error", "high"), _task("redirect_inlink_update", "high")]
    start = dt.date(2026, 9, 1)  # Tuesday
    scheduled = assign_schedule(tasks, start, capacity_per_week=5, site_scale="large")
    by_category = {t.category: t for t in scheduled}
    assert by_category["server_error"].month_index == 0
    assert by_category["redirect_inlink_update"].month_index >= 1
    assert by_category["redirect_inlink_update"].target_date >= dt.date(2026, 10, 1)


def test_small_site_does_not_gate_redirect_cleanup():
    tasks = [_task("server_error", "high"), _task("redirect_inlink_update", "high")]
    start = dt.date(2026, 9, 1)
    scheduled = assign_schedule(tasks, start, capacity_per_week=5, site_scale="small")
    by_category = {t.category: t for t in scheduled}
    # with no gate, redirect cleanup lands the very next business day -- still month 0
    assert by_category["redirect_inlink_update"].month_index == 0


def test_large_site_gate_does_not_affect_tasks_already_in_month_two():
    """If tier-0/1 work alone spills past month 1, gated work just continues normally --
    no artificial jump forward once the date is already past the gate."""
    tasks = [_task("server_error", "high") for _ in range(25)] + [_task("redirect_inlink_update", "high")]
    start = dt.date(2026, 9, 1)
    scheduled = assign_schedule(tasks, start, capacity_per_week=5, site_scale="large")
    redirect_task = next(t for t in scheduled if t.category == "redirect_inlink_update")
    last_server_error_date = max(t.target_date for t in scheduled if t.category == "server_error")
    # should be the very next business day after the last server_error task, not artificially delayed further
    assert redirect_task.target_date > last_server_error_date


# ---------- content/growth tiers (5+) spread across the whole campaign ----------


def test_content_tasks_spread_across_the_full_campaign_not_crammed_into_month_one():
    # a small, already-batched total (see crawl_rules.CAMPAIGN_TASK_BUDGET) that would
    # previously have all finished in the first couple weeks at generous capacity
    tasks = [_task("meta_tag_reoptimization", "high") for _ in range(20)]
    start = dt.date(2026, 9, 1)
    scheduled = assign_schedule(tasks, start, capacity_per_week=25, duration_months=6)
    months_used = {t.month_index for t in scheduled}
    # spread across a real span of the campaign, not all landing in month 0
    assert len(months_used) > 1
    assert max(months_used) >= 4


def test_content_tasks_never_land_after_the_campaign_ends():
    tasks = [_task("ctr_optimization", "medium") for _ in range(15)]
    start = dt.date(2026, 9, 1)
    duration = 3
    scheduled = assign_schedule(tasks, start, capacity_per_week=5, duration_months=duration)
    campaign_end = dt.date(2026, 12, 1) - dt.timedelta(days=1)  # last day of month 3 (Nov 2026)
    assert all(t.target_date <= campaign_end for t in scheduled)


def test_technical_work_still_lands_as_fast_as_possible_unaffected_by_content_spread():
    tasks = [_task("404_fix", "high") for _ in range(2)] + [_task("meta_tag_reoptimization", "high") for _ in range(5)]
    start = dt.date(2026, 9, 1)  # Tuesday
    scheduled = assign_schedule(tasks, start, capacity_per_week=5)
    technical = [t for t in scheduled if t.category == "404_fix"]
    # unchanged behavior: one per business day starting immediately, regardless of content tasks present
    assert technical[0].target_date == start
    assert technical[1].target_date == dt.date(2026, 9, 2)


def test_content_spread_starts_after_technical_work_finishes():
    tasks = [_task("404_fix", "high") for _ in range(10)] + [_task("meta_tag_reoptimization", "high") for _ in range(5)]
    start = dt.date(2026, 9, 1)
    scheduled = assign_schedule(tasks, start, capacity_per_week=5, duration_months=6)
    last_technical_date = max(t.target_date for t in scheduled if t.category == "404_fix")
    first_content_date = min(t.target_date for t in scheduled if t.category == "meta_tag_reoptimization")
    assert first_content_date > last_technical_date


def test_content_tier_priority_order_preserved_within_the_spread():
    """meta_tag_reoptimization (tier 5) still gets earlier spread slots than
    ctr_optimization (tier 8), even though both are spread rather than greedily filled."""
    tasks = [_task("ctr_optimization", "medium") for _ in range(3)] + [_task("meta_tag_reoptimization", "high") for _ in range(3)]
    start = dt.date(2026, 9, 1)
    scheduled = assign_schedule(tasks, start, capacity_per_week=5, duration_months=6)
    meta_dates = [t.target_date for t in scheduled if t.category == "meta_tag_reoptimization"]
    ctr_dates = [t.target_date for t in scheduled if t.category == "ctr_optimization"]
    assert max(meta_dates) <= min(ctr_dates)


def test_no_content_tasks_means_no_change_to_technical_only_schedule():
    tasks = [_task("404_fix", "high") for _ in range(5)]
    start = dt.date(2026, 9, 1)
    scheduled = assign_schedule(tasks, start, capacity_per_week=5, duration_months=6)
    assert len(scheduled) == 5
    assert all(t.category == "404_fix" for t in scheduled)


# ---------- excluded_dates (keeps independently-scheduled sources off each other's days) ----------


def test_excluded_dates_pushes_a_technical_task_off_an_already_used_day():
    start = dt.date(2026, 9, 1)  # Tuesday
    scheduled = assign_schedule(
        [_task("404_fix", "high")], start, capacity_per_week=5, excluded_dates=frozenset({start})
    )
    assert scheduled[0].target_date != start
    assert scheduled[0].target_date == dt.date(2026, 9, 2)


def test_excluded_dates_pushes_a_content_task_off_an_already_used_day():
    start = dt.date(2026, 9, 1)
    scheduled = assign_schedule(
        [_task("meta_tag_reoptimization", "high")], start, capacity_per_week=5, duration_months=6,
        excluded_dates=frozenset({start}),
    )
    assert scheduled[0].target_date != start


def test_two_independently_scheduled_sources_never_land_on_the_same_day():
    """Simulates crawl + GSC each calling assign_schedule separately for the same site
    (as app/services.py's three sync/import functions actually do) -- the second call
    must exclude the first call's dates, or both sources' day-1 slot collides on the
    same calendar date even though each looks correctly spaced in isolation."""
    start = dt.date(2026, 9, 1)
    crawl_scheduled = assign_schedule([_task("404_fix", "high")], start, capacity_per_week=5)
    crawl_dates = frozenset(t.target_date for t in crawl_scheduled)

    gsc_scheduled = assign_schedule(
        [_task("meta_tag_reoptimization", "high")], start, capacity_per_week=5, duration_months=6,
        excluded_dates=crawl_dates,
    )
    assert not (crawl_dates & {t.target_date for t in gsc_scheduled})


def test_excluded_dates_defaults_to_no_exclusions():
    start = dt.date(2026, 9, 1)
    scheduled = assign_schedule([_task("404_fix", "high")], start, capacity_per_week=5)
    assert scheduled[0].target_date == start
