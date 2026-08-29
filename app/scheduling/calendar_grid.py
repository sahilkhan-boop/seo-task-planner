"""Builds a month-by-month calendar grid (like a desktop Calendar app's month
view) from a flat list of Tasks -- shared by the HTML calendar view, the PDF
export, and the Excel export so all three render identically.
"""
from __future__ import annotations

import calendar
import datetime as dt
from dataclasses import dataclass, field

from app.scheduling.month_utils import add_months

WEEKDAY_LABELS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]


@dataclass
class DayCell:
    date: dt.date
    in_month: bool
    tasks: list = field(default_factory=list)


@dataclass
class MonthGrid:
    label: str  # "August 2026"
    weeks: list  # list of list[DayCell], each inner list has 7 entries (Sun-Sat)


def calendar_span_months(configured_duration_months: int, tasks: list) -> int:
    """How many months the calendar needs to show every scheduled task.

    Never fewer than the campaign's configured duration -- a light-workload
    campaign should still show its full planned length, not shrink to however
    many months currently have work. But when the real backlog runs past that
    (more scheduled work than capacity_per_week x duration_months can hold --
    see app/scheduling/timeline.py), extend the grid rather than silently
    dropping the overflow tasks from the calendar/PDF/Excel exports, which all
    build off this same span so they stay in agreement.
    """
    max_month_index = max((t.month_index for t in tasks if t.month_index is not None), default=-1)
    return max(configured_duration_months, max_month_index + 1)


def build_campaign_calendar(start_date: dt.date, duration_months: int, tasks: list) -> list[MonthGrid]:
    """One MonthGrid per calendar month the campaign spans, in order.

    `tasks` is any iterable of objects with a `target_date` attribute (ORM
    Task rows work directly). Tasks land in the grid of whichever real
    calendar month their target_date falls in.
    """
    by_date: dict[dt.date, list] = {}
    for t in tasks:
        if t.target_date is not None:
            by_date.setdefault(t.target_date, []).append(t)

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
