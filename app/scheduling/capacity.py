"""The two real, analyst-supplied constants the day-by-day capacity scheduler
(services.reschedule_all_tasks) is built on -- pulled out into their own module
so app/scheduling/calendar_grid.py can mirror the same day-spanning logic for
display (a task bigger than one day's capacity, e.g. a worksheet-driven
site-wide audit, spans multiple calendar cells) without calendar_grid.py (a
low-level rendering utility) depending on services.py (the app's top-level
orchestration layer) just for two numbers.
"""
from __future__ import annotations

# Wednesday is reserved for weekly_report/monthly_report_mbr (see
# reporting_rules.py) -- the sequential capacity queue never places anything on
# it, so a task's real span (task_calendar_span in calendar_grid.py) must skip
# it too, the same way reschedule_all_tasks's own `days` list does.
WEDNESDAY = 2

# An 8-hour workday's total capacity -- real, analyst-supplied (Sahil,
# 2026-08-27), not a guess.
DAILY_CAPACITY_HOURS = 8.0
