import datetime as dt
from dataclasses import dataclass

from app.scheduling.calendar_grid import build_campaign_calendar, calendar_span_months


@dataclass
class FakeTask:
    target_date: dt.date
    severity: str = "medium"
    title: str = "Do something"
    assignee: str | None = None
    month_index: int | None = None


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
