"""Evaluates VolumeBenchmark rows -- site-wide daily/weekly/monthly volume
thresholds (e.g. "flag when total clicks for the day are below 200") -- against
SiteMetricDaily's stored daily totals.

Purely a read/compute pass: no tasks are created and no state is written here --
it's recomputed live every time it's asked for (see routers/sites.py and
routers/benchmarks.py), so it always reflects whatever's actually been synced so
far, with nothing to go stale.
"""
from __future__ import annotations

import datetime as dt

from app.scheduling.month_utils import add_months


def _latest_complete_week(latest_date: dt.date) -> tuple[dt.date, dt.date]:
    """(start, end) of the most recently-CLOSED Mon-Sun week as of latest_date --
    latest_date itself if it's a Sunday (that week just closed), otherwise the
    Sunday before it."""
    days_since_sunday = (latest_date.weekday() + 1) % 7  # Mon=0..Sun=6 -> Sun=0..Sat=6
    week_end = latest_date - dt.timedelta(days=days_since_sunday)
    week_start = week_end - dt.timedelta(days=6)
    return week_start, week_end


def _latest_complete_month(latest_date: dt.date) -> tuple[dt.date, dt.date]:
    """(start, end) of the most recently-CLOSED calendar month as of latest_date --
    the current month if latest_date IS its last day, otherwise the previous one."""
    this_month_start = latest_date.replace(day=1)
    next_month_start = add_months(this_month_start, 1)
    last_day_this_month = next_month_start - dt.timedelta(days=1)
    if latest_date >= last_day_this_month:
        return this_month_start, last_day_this_month
    prev_month_start = add_months(this_month_start, -1)
    return prev_month_start, this_month_start - dt.timedelta(days=1)


def _all_days_present(by_date: dict, start: dt.date, end: dt.date) -> bool:
    """A weekly/monthly total off a window with missing days (a failed sync day,
    or a benchmark added before a full period has synced) would understate the
    real total and could falsely read as a traffic drop -- only sum a period
    once every one of its days actually has a row."""
    d = start
    while d <= end:
        if d not in by_date:
            return False
        d += dt.timedelta(days=1)
    return True


def evaluate_volume_benchmark(benchmark, daily_rows: list) -> dict | None:
    """benchmark: one VolumeBenchmark row. daily_rows: every SiteMetricDaily row
    for that same site+source+metric_key (any order). Returns None when there's
    not yet enough synced data to check the benchmark's period, otherwise a dict
    with the latest closed period's actual value against target and whether
    it's currently flagged.
    """
    if not daily_rows:
        return None
    by_date = {r.date: r.value for r in daily_rows}
    latest_date = max(by_date)

    if benchmark.period == "daily":
        period_label = latest_date.isoformat()
        actual = by_date[latest_date]
    elif benchmark.period == "weekly":
        start, end = _latest_complete_week(latest_date)
        if not _all_days_present(by_date, start, end):
            return None
        period_label = f"{start.isoformat()} to {end.isoformat()}"
        actual = sum(v for d, v in by_date.items() if start <= d <= end)
    elif benchmark.period == "monthly":
        start, end = _latest_complete_month(latest_date)
        if not _all_days_present(by_date, start, end):
            return None
        period_label = start.strftime("%B %Y")
        actual = sum(v for d, v in by_date.items() if start <= d <= end)
    else:
        return None

    flagged = (actual < benchmark.target_value) if benchmark.comparator == "lt" else (actual > benchmark.target_value)
    return {
        "benchmark": benchmark,
        "period_label": period_label,
        "actual": actual,
        "flagged": flagged,
    }


def evaluate_volume_benchmarks(benchmarks: list, daily_rows_by_key: dict) -> list[dict]:
    """benchmarks: every VolumeBenchmark row for a site. daily_rows_by_key:
    {(source, metric_key): [SiteMetricDaily, ...]}. Returns one evaluation per
    benchmark that has enough data to check (skips ones with none yet),
    currently-flagged ones first so a warning banner never buries the thing
    that actually needs attention under a page of "all clear" rows.
    """
    results = []
    for b in benchmarks:
        evaluated = evaluate_volume_benchmark(b, daily_rows_by_key.get((b.source, b.metric_key), []))
        if evaluated:
            results.append(evaluated)
    results.sort(key=lambda r: not r["flagged"])
    return results
