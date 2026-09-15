"""Builds a month-by-month calendar grid (like a desktop Calendar app's month
view) from a flat list of Tasks -- shared by the HTML calendar view, the PDF
export, and the Excel export so all three render identically.
"""
from __future__ import annotations

import calendar
import datetime as dt
from dataclasses import dataclass, field

from app.scheduling.capacity import DAILY_CAPACITY_HOURS, WEDNESDAY
from app.scheduling.month_utils import add_months

WEEKDAY_LABELS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]


def task_calendar_span(target_date: dt.date | None, estimated_hours: float | None) -> list[dt.date]:
    """Every business day (Mon-Fri, skipping Wednesday -- reserved for
    weekly_report/monthly_report_mbr, see WEDNESDAY) a task's real hours occupy,
    starting from target_date -- mirrors services._assign_next_available_slot's
    own day-by-day capacity consumption, purely for display: the calendar/PDF/
    Excel views show a multi-day task (a worksheet-driven site-wide audit, 30-60h
    -- see app/rules/worksheet_task_hours.py) on every day it actually spans,
    not just its start day, which would otherwise misrepresent a week-long audit
    as a single impossible day.

    A task at or under DAILY_CAPACITY_HOURS always returns exactly
    [target_date] -- the normal case for every pre-worksheet task category, none
    of which was ever bigger than a single day.
    """
    if target_date is None:
        return []
    if not estimated_hours or estimated_hours <= DAILY_CAPACITY_HOURS:
        return [target_date]
    days = [target_date]
    remaining = estimated_hours - DAILY_CAPACITY_HOURS
    d = target_date
    while remaining > 0:
        d += dt.timedelta(days=1)
        if d.weekday() < 5 and d.weekday() != WEDNESDAY:
            days.append(d)
            remaining -= DAILY_CAPACITY_HOURS
    return days


@dataclass
class DayCell:
    date: dt.date
    in_month: bool
    tasks: list = field(default_factory=list)


@dataclass
class MonthGrid:
    label: str  # "August 2026"
    weeks: list  # list of list[DayCell], each inner list has 7 entries (Sun-Sat)


def calendar_span_months(configured_duration_months: int, tasks: list, start_date: dt.date | None = None) -> int:
    """How many months the calendar needs to show every scheduled task.

    Never fewer than the campaign's configured duration -- a light-workload
    campaign should still show its full planned length, not shrink to however
    many months currently have work. But when the real backlog runs past that
    (more scheduled work than capacity_per_week x duration_months can hold --
    see app/scheduling/timeline.py), extend the grid rather than silently
    dropping the overflow tasks from the calendar/PDF/Excel exports, which all
    build off this same span so they stay in agreement.

    start_date, when given, also accounts for a multi-day task (task_calendar_span
    -- one bigger than DAILY_CAPACITY_HOURS) whose real span crosses into a later
    calendar month than its own month_index reflects: month_index is only ever
    computed for a task's START day, so without this a large task starting near a
    month's end could have its later days silently fall outside the grid.
    """
    max_month_index = max((t.month_index for t in tasks if t.month_index is not None), default=-1)
    if start_date is not None:
        first_of_start_month = start_date.replace(day=1)
        for t in tasks:
            span = task_calendar_span(t.target_date, t.estimated_hours)
            if len(span) > 1:
                last_day = span[-1]
                end_month_index = (last_day.year - first_of_start_month.year) * 12 + (last_day.month - first_of_start_month.month)
                max_month_index = max(max_month_index, end_month_index)
    return max(configured_duration_months, max_month_index + 1)


def build_campaign_calendar(start_date: dt.date, duration_months: int, tasks: list) -> list[MonthGrid]:
    """One MonthGrid per calendar month the campaign spans, in order.

    `tasks` is any iterable of objects with `target_date`/`estimated_hours`
    attributes (ORM Task rows work directly). A task lands in the day cell of
    every real calendar day its span actually covers (task_calendar_span) --
    just its own target_date for every task at or under a single day's
    capacity (still the overwhelming majority), multiple consecutive cells for
    a multi-day worksheet-driven audit.
    """
    by_date: dict[dt.date, list] = {}
    for t in tasks:
        for day in task_calendar_span(t.target_date, t.estimated_hours):
            by_date.setdefault(day, []).append(t)

    cal = calendar.Calendar(firstweekday=6)  # Sunday-first, matching common calendar-app layout
    months: list[MonthGrid] = []
    first_of_start_month = start_date.replace(day=1)

    for i in range(duration_months):
        month_start = add_months(first_of_start_month, i)
        label = month_start.strftime("%B %Y")
        weeks: list[list[DayCell]] = []
        for week in cal.monthdatescalendar(month_start.year, month_start.month):
            week_cells = []
            for day in week:
                week_cells.append(
                    DayCell(
                        date=day,
                        in_month=(day.month == month_start.month),
                        tasks=sorted(by_date.get(day, []), key=lambda t: t.severity != "high"),
                    )
                )
            weeks.append(week_cells)
        months.append(MonthGrid(label=label, weeks=weeks))

    return months
