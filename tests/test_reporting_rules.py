import datetime as dt

from app.rules.reporting_rules import generate_reporting_tasks


def test_month_one_gets_only_the_performance_dashboard_task():
    tasks = generate_reporting_tasks(dt.date(2026, 9, 1), 3)
    month0_tasks = [t for t in tasks if t.month_index == 0]
    assert [t.category for t in month0_tasks] == ["performance_dashboard"]


def test_performance_dashboard_lands_on_the_last_wednesday_of_month_one():
    tasks = generate_reporting_tasks(dt.date(2026, 9, 1), 3)
    [dashboard] = [t for t in tasks if t.category == "performance_dashboard"]
    assert dashboard.target_date.weekday() == 2  # Wednesday
    assert dashboard.target_date == dt.date(2026, 9, 30)  # last Wednesday of September 2026


def test_month_two_onward_gets_weekly_reports_every_wednesday():
    tasks = generate_reporting_tasks(dt.date(2026, 9, 1), 3)
    month1_tasks = [t for t in tasks if t.month_index == 1]  # October 2026
    assert all(t.target_date.weekday() == 2 for t in month1_tasks)
    # October 2026 has 4 Wednesdays: 7, 14, 21, 28
    assert len(month1_tasks) == 4


def test_last_wednesday_of_each_month_is_the_mbr_not_a_weekly_report():
    tasks = generate_reporting_tasks(dt.date(2026, 9, 1), 3)
    month1_tasks = sorted([t for t in tasks if t.month_index == 1], key=lambda t: t.target_date)
    assert [t.category for t in month1_tasks] == ["weekly_report", "weekly_report", "weekly_report", "monthly_report_mbr"]
    assert month1_tasks[-1].target_date == dt.date(2026, 10, 28)


def test_no_weekly_report_and_mbr_ever_share_a_week():
    tasks = generate_reporting_tasks(dt.date(2026, 9, 1), 4)
    weeks_seen = set()
    for t in tasks:
        if t.category in ("weekly_report", "monthly_report_mbr"):
            week_key = t.target_date.isocalendar()[:2]  # (iso_year, iso_week)
            assert week_key not in weeks_seen, f"two report tasks landed in the same week: {week_key}"
            weeks_seen.add(week_key)


def test_no_task_ever_lands_on_a_non_wednesday():
    tasks = generate_reporting_tasks(dt.date(2026, 9, 1), 6)
    assert all(t.target_date.weekday() == 2 for t in tasks)


def test_duration_of_one_month_produces_only_the_dashboard_task():
    tasks = generate_reporting_tasks(dt.date(2026, 9, 1), 1)
    assert len(tasks) == 1
    assert tasks[0].category == "performance_dashboard"
