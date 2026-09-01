"""evaluate_site_volume_benchmarks (app/services.py) -- the DB-level glue that
gathers a site's configured VolumeBenchmark rows and its synced SiteMetricDaily
totals, then hands them to rules/volume_rules.py (see test_volume_rules.py for
the pure evaluation logic itself).
"""
import datetime as dt

from app.models import Site, SiteMetricDaily, VolumeBenchmark
from app.services import evaluate_site_volume_benchmarks


def _site(db_session, domain="example.com"):
    site = Site(domain=domain)
    db_session.add(site)
    db_session.commit()
    return site


def _daily(db_session, site_id, source, metric_key, date, value):
    db_session.add(SiteMetricDaily(site_id=site_id, source=source, metric_key=metric_key, date=date, value=value))
    db_session.commit()


def test_returns_empty_list_when_no_volume_benchmarks_configured(db_session):
    site = _site(db_session)
    assert evaluate_site_volume_benchmarks(db_session, site.id) == []


def test_evaluates_a_configured_benchmark_against_its_synced_daily_totals(db_session):
    site = _site(db_session)
    db_session.add(
        VolumeBenchmark(site_id=site.id, source="gsc", metric_key="clicks", period="daily", comparator="lt", target_value=200)
    )
    db_session.commit()
    _daily(db_session, site.id, "gsc", "clicks", dt.date(2026, 8, 30), 150)

    results = evaluate_site_volume_benchmarks(db_session, site.id)

    assert len(results) == 1
    assert results[0]["flagged"] is True
    assert results[0]["actual"] == 150


def test_only_matches_daily_rows_for_the_same_site(db_session):
    """Another site's daily totals must never leak into this site's evaluation."""
    site_a = _site(db_session, "a.com")
    site_b = _site(db_session, "b.com")
    db_session.add(
        VolumeBenchmark(site_id=site_a.id, source="gsc", metric_key="clicks", period="daily", comparator="lt", target_value=1)
    )
    db_session.commit()
    _daily(db_session, site_b.id, "gsc", "clicks", dt.date(2026, 8, 30), 999999)  # site B's own data, irrelevant to A

    assert evaluate_site_volume_benchmarks(db_session, site_a.id) == []  # no data yet FOR SITE A specifically


def test_matches_rows_by_source_and_metric_key_not_just_site(db_session):
    site = _site(db_session)
    db_session.add(
        VolumeBenchmark(site_id=site.id, source="ga4", metric_key="sessions", period="daily", comparator="lt", target_value=100)
    )
    db_session.commit()
    _daily(db_session, site.id, "gsc", "clicks", dt.date(2026, 8, 30), 5)  # same site, wrong source/metric

    assert evaluate_site_volume_benchmarks(db_session, site.id) == []
