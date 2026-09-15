import datetime as dt
from dataclasses import dataclass

from app.scheduling.calendar_grid import build_campaign_calendar, calendar_span_months, task_calendar_span


@dataclass
class FakeTask:
    target_date: dt.date
    severity: str = "medium"
    title: str = "Do something"
    assignee: str | None = None
    month_index: int | None = None
    estimated_hours: float = 1.0  # real Task rows always have this; well under a day's capacity by default


def test_spans_the_full_campaign_duration_even_with_no_tasks():
    months = build_campaign_calendar(dt.date(2026, 8, 15), 6, [])
    labels = [m.label for m in months]
    assert labels == [
        "August 2026", "September 2026", "October 2026",
        "November 2026", "December 2026", "January 2027",
    ]


def test_every_week_has_seven_days_and_is_sunday_first():
    [month] = build_campaign_calendar(dt.date(2026, 8, 15), 1, [])
    for week in month.weeks:
        assert len(week) == 7
        assert week[0].date.weekday() == 6  # Sunday


def test_task_lands_on_its_target_date_in_the_right_month():
    task = FakeTask(target_date=dt.date(2026, 9, 3))
    months = build_campaign_calendar(dt.date(2026, 8, 15), 6, [task])
    sept = next(m for m in months if m.label == "September 2026")
    matching_cells = [c for week in sept.weeks for c in week if c.date == dt.date(2026, 9, 3)]
    assert len(matching_cells) == 1
    assert matching_cells[0].tasks == [task]
    # shouldn't leak into other months
    aug = next(m for m in months if m.label == "August 2026")
    assert all(not c.tasks for week in aug.weeks for c in week if c.in_month)


def test_out_of_month_days_are_flagged():
    [month] = build_campaign_calendar(dt.date(2026, 8, 15), 1, [])
    first_week = month.weeks[0]
    # August 1 2026 is a Saturday, so Sun-Fri of the first week belong to July
    assert not first_week[0].in_month
    assert first_week[-1].in_month  # Saturday Aug 1 itself


# ---------- calendar_span_months ----------


def test_span_is_at_least_the_configured_duration_even_with_no_overflow():
    tasks = [FakeTask(target_date=dt.date(2026, 8, 20), month_index=0)]
    assert calendar_span_months(6, tasks) == 6


def test_span_is_at_least_the_configured_duration_with_no_tasks_at_all():
    assert calendar_span_months(6, []) == 6


def test_span_extends_past_configured_duration_when_backlog_overflows():
    tasks = [FakeTask(target_date=dt.date(2027, 1, 1), month_index=12)]
    assert calendar_span_months(6, tasks) == 13  # month_index 12 -> 13 months (0-indexed)


def test_span_ignores_tasks_with_no_month_index():
    tasks = [FakeTask(target_date=dt.date(2026, 8, 20), month_index=None)]
    assert calendar_span_months(6, tasks) == 6


def test_span_extension_makes_overflow_tasks_actually_appear_in_the_grid():
    task = FakeTask(target_date=dt.date(2027, 1, 5), month_index=12)
    span = calendar_span_months(6, [task])
    months = build_campaign_calendar(dt.date(2026, 8, 15), span, [task])
    jan_2027 = next(m for m in months if m.label == "January 2027")
    matching = [c for week in jan_2027.weeks for c in week if c.date == dt.date(2027, 1, 5)]
    assert matching and matching[0].tasks == [task]


# ---------- task_calendar_span ----------


def test_span_is_just_the_target_date_for_a_task_at_or_under_a_days_capacity():
    assert task_calendar_span(dt.date(2026, 9, 15), 4.0) == [dt.date(2026, 9, 15)]
    assert task_calendar_span(dt.date(2026, 9, 15), 8.0) == [dt.date(2026, 9, 15)]


def test_span_is_empty_with_no_target_date():
    assert task_calendar_span(None, 45.0) == []


def test_span_treats_missing_hours_as_a_single_day():
    assert task_calendar_span(dt.date(2026, 9, 15), None) == [dt.date(2026, 9, 15)]


def test_span_covers_multiple_consecutive_business_days_for_an_oversized_task():
    # 2026-09-15 is a Tuesday -- 20h needs 3 days (8+8+4), landing on Tue/Wed/Thu...
    # except Wednesday is skipped (reserved for reporting tasks), so it's Tue/Thu/Fri.
    span = task_calendar_span(dt.date(2026, 9, 15), 20.0)
    assert span == [dt.date(2026, 9, 15), dt.date(2026, 9, 17), dt.date(2026, 9, 18)]


def test_span_skips_weekends():
    # Friday 2026-09-18 + 16h needs 2 more days after the first -- Sat/Sun skipped,
    # landing on Monday and Tuesday.
    span = task_calendar_span(dt.date(2026, 9, 18), 24.0)
    assert span == [dt.date(2026, 9, 18), dt.date(2026, 9, 21), dt.date(2026, 9, 22)]


# ---------- multi-day tasks in build_campaign_calendar / calendar_span_months ----------


def test_an_oversized_task_appears_in_every_day_cell_it_spans():
    task = FakeTask(target_date=dt.date(2026, 9, 15), estimated_hours=20.0, month_index=0)
    months = build_campaign_calendar(dt.date(2026, 9, 1), 1, [task])
    sept = months[0]
    cells_with_task = [c for week in sept.weeks for c in week if task in c.tasks]
    assert {c.date for c in cells_with_task} == {dt.date(2026, 9, 15), dt.date(2026, 9, 17), dt.date(2026, 9, 18)}


def test_calendar_span_months_extends_for_a_multi_day_task_crossing_into_a_later_month():
    # Starts the last business day of August; its real span runs into September,
    # which month_index alone (computed only for the start day) wouldn't capture.
    task = FakeTask(target_date=dt.date(2026, 8, 31), estimated_hours=16.0, month_index=0)
    span = calendar_span_months(1, [task], start_date=dt.date(2026, 8, 1))
    assert span == 2  # August + September, not just the configured 1 month


def test_calendar_span_months_without_start_date_keeps_old_behavior():
    """Backward-compatible: omitting start_date (existing callers/tests) skips the
    multi-day check entirely, same as before this feature existed."""
    task = FakeTask(target_date=dt.date(2026, 8, 31), estimated_hours=16.0, month_index=0)
    assert calendar_span_months(1, [task]) == 1
