"""evaluate_volume_benchmark(s) (app/rules/volume_rules.py) -- the site-wide
daily/weekly/monthly volume-trend check (e.g. "clicks below 200 for the day"),
evaluated against SiteMetricDaily's stored daily totals. Plain dataclasses stand
in for the ORM rows -- this module only ever reads .date/.value/.period/etc, so
no real DB is needed to test it.
"""
import datetime as dt
from dataclasses import dataclass

from app.rules.volume_rules import evaluate_volume_benchmark, evaluate_volume_benchmarks


@dataclass
class FakeDailyRow:
    date: dt.date
    value: float


@dataclass
class FakeBenchmark:
    source: str = "gsc"
    metric_key: str = "clicks"
    period: str = "daily"
    comparator: str = "lt"
    target_value: float = 200.0


def _rows(*pairs):
    return [FakeDailyRow(date=d, value=v) for d, v in pairs]


def test_no_data_yet_returns_none():
    assert evaluate_volume_benchmark(FakeBenchmark(), []) is None


def test_daily_flags_when_latest_day_is_below_target():
    rows = _rows((dt.date(2026, 8, 30), 150.0))
    result = evaluate_volume_benchmark(FakeBenchmark(period="daily", target_value=200.0), rows)
    assert result["flagged"] is True
    assert result["actual"] == 150.0
    assert result["period_label"] == "2026-08-30"


def test_daily_does_not_flag_when_latest_day_meets_target():
    rows = _rows((dt.date(2026, 8, 30), 250.0))
    result = evaluate_volume_benchmark(FakeBenchmark(period="daily", target_value=200.0), rows)
    assert result["flagged"] is False


def test_daily_uses_the_most_recent_date_even_if_rows_are_unordered():
    rows = _rows((dt.date(2026, 8, 28), 999.0), (dt.date(2026, 8, 30), 100.0), (dt.date(2026, 8, 29), 999.0))
    result = evaluate_volume_benchmark(FakeBenchmark(period="daily", target_value=200.0), rows)
    assert result["actual"] == 100.0


def test_gt_comparator_flags_above_target_instead_of_below():
    rows = _rows((dt.date(2026, 8, 30), 500.0))
    result = evaluate_volume_benchmark(FakeBenchmark(period="daily", comparator="gt", target_value=300.0), rows)
    assert result["flagged"] is True


def test_weekly_sums_the_most_recently_closed_mon_sun_week():
    # Latest date is Wed 2026-09-02 -- the most recently CLOSED week is
    # Mon 2026-08-24 through Sun 2026-08-30 (the current partial week is excluded).
    closed_week = [(dt.date(2026, 8, 24) + dt.timedelta(days=i), 30.0) for i in range(7)]  # 7*30 = 210
    partial_week = [(dt.date(2026, 8, 31), 999.0), (dt.date(2026, 9, 1), 999.0), (dt.date(2026, 9, 2), 999.0)]
    rows = _rows(*closed_week, *partial_week)

    result = evaluate_volume_benchmark(FakeBenchmark(period="weekly", target_value=250.0), rows)

    assert result["actual"] == 210.0
    assert result["flagged"] is True
    assert result["period_label"] == "2026-08-24 to 2026-08-30"


def test_weekly_when_latest_date_is_itself_a_sunday_that_week_is_the_closed_one():
    week = [(dt.date(2026, 8, 24) + dt.timedelta(days=i), 50.0) for i in range(7)]  # ends Sun 2026-08-30
    rows = _rows(*week)

    result = evaluate_volume_benchmark(FakeBenchmark(period="weekly", target_value=300.0), rows)

    assert result["actual"] == 350.0
    assert result["period_label"] == "2026-08-24 to 2026-08-30"


def test_weekly_returns_none_when_the_closed_week_has_missing_days():
    """A gap (failed sync day, or benchmark added before a full week synced)
    must not understate the total and falsely read as a drop."""
    week = [(dt.date(2026, 8, 24) + dt.timedelta(days=i), 100.0) for i in range(7) if i != 3]  # missing one day
    rows = _rows(*week)

    assert evaluate_volume_benchmark(FakeBenchmark(period="weekly", target_value=1.0), rows) is None


def test_monthly_sums_the_most_recently_closed_calendar_month():
    # Latest date is 2026-09-15 (mid-September) -- the closed month is all of August.
    august = [(dt.date(2026, 8, i), 10.0) for i in range(1, 32)]  # 31 days * 10 = 310
    september_so_far = [(dt.date(2026, 9, i), 999.0) for i in range(1, 16)]
    rows = _rows(*august, *september_so_far)

    result = evaluate_volume_benchmark(FakeBenchmark(period="monthly", target_value=400.0), rows)

    assert result["actual"] == 310.0
    assert result["flagged"] is True
    assert result["period_label"] == "August 2026"


def test_monthly_when_latest_date_is_the_last_day_of_its_month_that_month_is_closed():
    august = [(dt.date(2026, 8, i), 10.0) for i in range(1, 32)]  # latest date = Aug 31, month-end
    rows = _rows(*august)

    result = evaluate_volume_benchmark(FakeBenchmark(period="monthly", target_value=100.0), rows)

    assert result["actual"] == 310.0
    assert result["period_label"] == "August 2026"


def test_monthly_returns_none_when_the_closed_month_has_missing_days():
    august = [(dt.date(2026, 8, i), 10.0) for i in range(1, 31)]  # missing Aug 31
    september_so_far = [(dt.date(2026, 9, 1), 10.0)]
    rows = _rows(*august, *september_so_far)

    assert evaluate_volume_benchmark(FakeBenchmark(period="monthly", target_value=1.0), rows) is None


def test_evaluate_many_puts_flagged_ones_first():
    ok = FakeBenchmark(metric_key="clicks", period="daily", target_value=10.0)
    bad = FakeBenchmark(metric_key="impressions", period="daily", target_value=10000.0)
    daily_rows_by_key = {
        ("gsc", "clicks"): _rows((dt.date(2026, 8, 30), 500.0)),  # well above target -- fine
        ("gsc", "impressions"): _rows((dt.date(2026, 8, 30), 100.0)),  # way below target -- flagged
    }

    results = evaluate_volume_benchmarks([ok, bad], daily_rows_by_key)

    assert [r["benchmark"] is bad for r in results] == [True, False]


def test_evaluate_many_skips_benchmarks_with_no_data_yet():
    has_data = FakeBenchmark(metric_key="clicks", period="daily", target_value=10.0)
    no_data = FakeBenchmark(metric_key="sessions", period="daily", target_value=10.0)
    daily_rows_by_key = {("gsc", "clicks"): _rows((dt.date(2026, 8, 30), 5.0))}

    results = evaluate_volume_benchmarks([has_data, no_data], daily_rows_by_key)

    assert [r["benchmark"] for r in results] == [has_data]
