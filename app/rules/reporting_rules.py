"""Recurring reporting cadence: weekly status reports, a Monthly Report/MBR, and the
one-time performance dashboard/artifact that bridges into it.

Reports don't start until the campaign's SECOND month -- month 1 is spent on
benchmarking/key-fix/quick-win work, and the first real report should reflect that
work having actually happened, not an empty first week. Month 1's last week instead
gets a one-time "build the performance dashboard/artifact" task, so there's something
concrete in place before weekly reporting kicks in next month.

From month 2 onward: every Wednesday gets a Weekly Report, EXCEPT the last Wednesday
of each month, which gets the Monthly Report/MBR instead -- never both in the same
week, since the MBR effectively IS that week's report, just at a higher level.

This is a genuinely separate scheduling mechanism from the rest of the platform's
tasks: every other category flows through app/services.py's phase-sequential
reschedule_all_tasks (one task per week, in priority order). Reporting is calendar-
anchored instead -- it always lands on a real Wednesday regardless of how many other
tasks exist that week -- so these tasks carry their target_date/month_index already
set at generation time, and reschedule_all_tasks explicitly leaves optimization_level
"reporting" tasks alone (see its docstring).
"""
from __future__ import annotations

import calendar
import datetime as dt
from dataclasses import dataclass, field

from app.scheduling.month_utils import add_months

WEDNESDAY = 2


@dataclass
class ReportingTask:
    category: str  # "performance_dashboard" | "weekly_report" | "monthly_report_mbr"
    title: str
    description: str
    month_index: int
    target_date: dt.date
    severity: str = "low"
    effort_tier: str = "medium"
    source: str = "content_plan"
    affected_urls: list = field(default_factory=list)
    url_details: dict = field(default_factory=dict)


def _wednesdays_in_month(year: int, month: int) -> list[dt.date]:
    _, last_day = calendar.monthrange(year, month)
    return [
        dt.date(year, month, day)
        for day in range(1, last_day + 1)
        if dt.date(year, month, day).weekday() == WEDNESDAY
    ]


def generate_reporting_tasks(campaign_start_date: dt.date, duration_months: int) -> list[ReportingTask]:
    tasks: list[ReportingTask] = []
    first_of_start_month = campaign_start_date.replace(day=1)

    # Month 1 (index 0): no reports yet -- last Wednesday gets the one-time dashboard/
    # artifact-creation task instead, bridging into real reporting next month.
    month0 = first_of_start_month
    wednesdays0 = [d for d in _wednesdays_in_month(month0.year, month0.month) if d >= campaign_start_date]
    if not wednesdays0:  # campaign started after the month's last Wednesday -- fall back to any Wednesday
        wednesdays0 = _wednesdays_in_month(month0.year, month0.month)
    if wednesdays0:
        tasks.append(
            ReportingTask(
                category="performance_dashboard",
                title=f"Build Performance Dashboard & Artifact — {month0.strftime('%B %Y')}",
                description=(
                    "Set up the recurring performance dashboard/artifact (rankings, traffic, task "
                    "progress) that weekly and monthly reports build on starting next month."
                ),
                month_index=0,
                target_date=wednesdays0[-1],
            )
        )

    for i in range(1, duration_months):
        month_start = add_months(first_of_start_month, i)
        wednesdays = _wednesdays_in_month(month_start.year, month_start.month)
        if not wednesdays:
            continue
        month_label = month_start.strftime("%B %Y")
        last_wed = wednesdays[-1]
        for wed in wednesdays:
            if wed == last_wed:
                tasks.append(
                    ReportingTask(
                        category="monthly_report_mbr",
                        title=f"Monthly Report / MBR — {month_label}",
                        description=(
                            "Monthly business review: summarize the month's completed work, benchmark "
                            "progress against the initial research, and set next month's focus. "
                            "Replaces this week's Weekly Report."
                        ),
                        month_index=i,
                        target_date=wed,
                    )
                )
            else:
                tasks.append(
                    ReportingTask(
                        category="weekly_report",
                        title=f"Weekly Report — {wed.isoformat()}",
                        description="Weekly status report: what shipped this week, what's next.",
                        month_index=i,
                        target_date=wed,
                    )
                )
    return tasks
