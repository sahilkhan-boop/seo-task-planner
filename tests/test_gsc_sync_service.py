import datetime as dt

import pytest

from app import services
from app.models import Benchmark, Campaign, Connection, MetricSnapshot, Site, SiteMetricDaily, Task
from app.services import NoConnectionError, sync_gsc_and_generate_tasks

FAKE_PAGE_ROWS = [
    {"page": "https://example.com/a", "clicks": 5, "impressions": 600, "ctr": 0.01, "position": 2.0},
    {"page": "https://example.com/b", "clicks": 50, "impressions": 200, "ctr": 0.25, "position": 2.0},
]


def _make_site(db_session, domain="example.com", gsc_site_url="https://example.com/"):
    site = Site(domain=domain, gsc_site_url=gsc_site_url)
    db_session.add(site)
    db_session.commit()
    return site


def _make_connection(db_session, site_id):
    conn = Connection(
        site_id=site_id,
        provider="gsc",
        access_token="at",
        refresh_token="rt",
        expires_at=dt.datetime.utcnow() + dt.timedelta(hours=1),
    )
    db_session.add(conn)
    db_session.commit()
    return conn


def _patch_fetches(monkeypatch, page_rows=FAKE_PAGE_ROWS, query_rows=None, site_totals=None):
    monkeypatch.setattr(services, "get_valid_access_token", lambda conn: "fake-token")
    monkeypatch.setattr(services, "fetch_page_analytics", lambda *a, **k: page_rows)
    monkeypatch.setattr(services, "fetch_page_query_analytics", lambda *a, **k: query_rows or [])
    monkeypatch.setattr(services, "fetch_gsc_site_totals", lambda *a, **k: site_totals or [])


def test_raises_when_no_connection(db_session):
    site = _make_site(db_session)
    with pytest.raises(NoConnectionError):
        sync_gsc_and_generate_tasks(db_session, site.id)


def test_raises_when_no_gsc_site_url(db_session, monkeypatch):
    site = _make_site(db_session, gsc_site_url=None)
    _make_connection(db_session, site.id)
    with pytest.raises(NoConnectionError):
        sync_gsc_and_generate_tasks(db_session, site.id)


def test_happy_path_persists_snapshots_and_generates_tasks(db_session, monkeypatch):
    site = _make_site(db_session)
    _make_connection(db_session, site.id)
    db_session.add(Benchmark(site_id=site.id, metric_key="ctr", segment="pos_1_5", comparator="lt", target_value=0.18))
    db_session.commit()

    _patch_fetches(monkeypatch)

    result = sync_gsc_and_generate_tasks(db_session, site.id)

    assert result["pages_synced"] == 2
    # only page "a" is below the 0.18 benchmark at high impressions; page "b" (ctr 0.25) is fine
    assert result["tasks_generated"] == 1

    snapshots = db_session.query(MetricSnapshot).filter(MetricSnapshot.site_id == site.id).all()
    assert len(snapshots) == 2
    assert {s.url for s in snapshots} == {"https://example.com/a", "https://example.com/b"}

    tasks = db_session.query(Task).filter(Task.site_id == site.id, Task.source == "gsc").all()
    assert len(tasks) == 1
    assert tasks[0].affected_urls == ["https://example.com/a"]
    assert tasks[0].category == "meta_tag_reoptimization"  # position 2.0 -> tier 1


def test_site_wide_daily_totals_are_upserted(db_session, monkeypatch):
    site = _make_site(db_session)
    _make_connection(db_session, site.id)
    db_session.commit()

    site_totals = [
        {"date": dt.date(2026, 8, 29), "clicks": 150, "impressions": 3000},
        {"date": dt.date(2026, 8, 30), "clicks": 200, "impressions": 4000},
    ]
    _patch_fetches(monkeypatch, site_totals=site_totals)

    sync_gsc_and_generate_tasks(db_session, site.id)

    rows = db_session.query(SiteMetricDaily).filter(SiteMetricDaily.site_id == site.id).all()
    by_key = {(r.date, r.metric_key): r.value for r in rows}
    assert by_key[(dt.date(2026, 8, 29), "clicks")] == 150
    assert by_key[(dt.date(2026, 8, 30), "impressions")] == 4000


def test_rerunning_sync_upserts_site_wide_totals_instead_of_duplicating(db_session, monkeypatch):
    site = _make_site(db_session)
    _make_connection(db_session, site.id)
    db_session.commit()

    _patch_fetches(monkeypatch, site_totals=[{"date": dt.date(2026, 8, 30), "clicks": 100, "impressions": 1000}])
    sync_gsc_and_generate_tasks(db_session, site.id)
    _patch_fetches(monkeypatch, site_totals=[{"date": dt.date(2026, 8, 30), "clicks": 250, "impressions": 1000}])
    sync_gsc_and_generate_tasks(db_session, site.id)

    rows = db_session.query(SiteMetricDaily).filter(
        SiteMetricDaily.site_id == site.id, SiteMetricDaily.metric_key == "clicks"
    ).all()
    assert len(rows) == 1  # corrected, not duplicated
    assert rows[0].value == 250


def test_rerunning_sync_clears_old_gsc_tasks_idempotently(db_session, monkeypatch):
    site = _make_site(db_session)
    _make_connection(db_session, site.id)
    db_session.add(Benchmark(site_id=site.id, metric_key="ctr", segment="pos_1_5", comparator="lt", target_value=0.18))
    db_session.commit()

    _patch_fetches(monkeypatch)

    sync_gsc_and_generate_tasks(db_session, site.id)
    sync_gsc_and_generate_tasks(db_session, site.id)

    tasks = db_session.query(Task).filter(Task.site_id == site.id, Task.source == "gsc").all()
    assert len(tasks) == 1  # not duplicated


def test_does_not_touch_non_gsc_tasks(db_session, monkeypatch):
    site = _make_site(db_session)
    _make_connection(db_session, site.id)
    db_session.add(Benchmark(site_id=site.id, metric_key="ctr", segment="pos_1_5", comparator="lt", target_value=0.18))
    db_session.add(Task(site_id=site.id, source="crawl", category="404_fix", title="Fix", description="D", severity="high"))
    db_session.commit()

    _patch_fetches(monkeypatch)

    sync_gsc_and_generate_tasks(db_session, site.id)

    crawl_tasks = db_session.query(Task).filter(Task.site_id == site.id, Task.source == "crawl").all()
    assert len(crawl_tasks) == 1


def test_schedules_from_today_not_campaign_start(db_session, monkeypatch):
    site = _make_site(db_session)
    _make_connection(db_session, site.id)
    db_session.add(Benchmark(site_id=site.id, metric_key="ctr", segment="pos_1_5", comparator="lt", target_value=0.18))
    # campaign started long ago -- GSC tasks should NOT anchor back to this date
    db_session.add(Campaign(site_id=site.id, start_date=dt.date(2020, 1, 1), duration_months=6))
    db_session.commit()

    _patch_fetches(monkeypatch)

    sync_gsc_and_generate_tasks(db_session, site.id)

    task = db_session.query(Task).filter(Task.site_id == site.id, Task.source == "gsc").one()
    assert task.target_date >= dt.date.today()


def test_brand_terms_passed_through_to_content_expansion_tier(db_session, monkeypatch):
    site = _make_site(db_session)
    site.brand_terms = "acme, acme corp"
    db_session.add(Connection(
        site_id=site.id, provider="gsc", access_token="at", refresh_token="rt",
        expires_at=dt.datetime.utcnow() + dt.timedelta(hours=1),
    ))
    db_session.commit()

    page_rows = [{"page": "https://example.com/c", "clicks": 20, "impressions": 300, "ctr": 0.07, "position": 8.0}]
    query_rows = [
        {"page": "https://example.com/c", "query": "widget guide", "clicks": 10, "impressions": 150, "ctr": 0.07, "position": 8},
        {"page": "https://example.com/c", "query": "acme widgets", "clicks": 10, "impressions": 150, "ctr": 0.07, "position": 8},
    ]
    _patch_fetches(monkeypatch, page_rows=page_rows, query_rows=query_rows)

    result = sync_gsc_and_generate_tasks(db_session, site.id)

    assert result["tasks_generated"] == 1
    task = db_session.query(Task).filter(Task.site_id == site.id, Task.source == "gsc").one()
    assert task.category == "content_expansion"
    assert task.metric_actual == 150  # only the non-branded "widget guide" query counts
