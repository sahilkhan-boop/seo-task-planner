import datetime as dt

import pytest

from app import services
from app.models import Benchmark, Campaign, Connection, MetricSnapshot, Site, Task
from app.services import NoConnectionError, sync_ga4_and_generate_tasks

FAKE_PAGE_ROWS = [
    {"page": "/a", "sessions": 600, "active_users": 400, "engagement_rate": 0.2, "bounce_rate": 0.3, "key_events": 5},
    {"page": "/b", "sessions": 200, "active_users": 150, "engagement_rate": 0.7, "bounce_rate": 0.2, "key_events": 10},
]
FAKE_MOBILE_SHARE = {"/a": 0.4, "/b": 0.5}


def _make_site(db_session, domain="example.com", ga4_property_id="123456789"):
    site = Site(domain=domain, ga4_property_id=ga4_property_id)
    db_session.add(site)
    db_session.commit()
    return site


def _make_connection(db_session, site_id):
    conn = Connection(
        site_id=site_id,
        provider="ga4",
        access_token="at",
        refresh_token="rt",
        expires_at=dt.datetime.utcnow() + dt.timedelta(hours=1),
    )
    db_session.add(conn)
    db_session.commit()
    return conn


def test_raises_when_no_connection(db_session):
    site = _make_site(db_session)
    with pytest.raises(NoConnectionError):
        sync_ga4_and_generate_tasks(db_session, site.id)


def test_raises_when_no_ga4_property_id(db_session):
    site = _make_site(db_session, ga4_property_id=None)
    _make_connection(db_session, site.id)
    with pytest.raises(NoConnectionError):
        sync_ga4_and_generate_tasks(db_session, site.id)


def test_happy_path_persists_snapshots_and_generates_tasks(db_session, monkeypatch):
    site = _make_site(db_session)
    _make_connection(db_session, site.id)
    db_session.add(Benchmark(site_id=site.id, metric_key="engagement_rate", comparator="lt", target_value=0.55))
    db_session.commit()

    monkeypatch.setattr(services, "get_valid_access_token", lambda conn: "fake-token")
    monkeypatch.setattr(services, "fetch_page_metrics", lambda *a, **k: FAKE_PAGE_ROWS)
    monkeypatch.setattr(services, "fetch_mobile_share", lambda *a, **k: FAKE_MOBILE_SHARE)
    monkeypatch.setattr(services, "fetch_ga4_site_totals", lambda *a, **k: [])

    result = sync_ga4_and_generate_tasks(db_session, site.id)

    assert result["pages_synced"] == 2
    # only /a is below the 0.55 engagement benchmark; /b (0.7) is fine
    assert result["tasks_generated"] == 1

    snapshots = db_session.query(MetricSnapshot).filter(MetricSnapshot.site_id == site.id).all()
    # 3 metric rows (engagement_rate, exit_rate, mobile_share) + key_events, per page x 2 pages
    assert {s.url for s in snapshots} == {"/a", "/b"}
    assert {s.metric_key for s in snapshots} == {"engagement_rate", "exit_rate", "mobile_share", "key_events"}

    tasks = db_session.query(Task).filter(Task.site_id == site.id, Task.source == "ga4").all()
    assert len(tasks) == 1
    assert tasks[0].affected_urls == ["/a"]
    assert tasks[0].category == "ui_ux_review"


def test_rerunning_sync_clears_old_ga4_tasks_idempotently(db_session, monkeypatch):
    site = _make_site(db_session)
    _make_connection(db_session, site.id)
    db_session.add(Benchmark(site_id=site.id, metric_key="engagement_rate", comparator="lt", target_value=0.55))
    db_session.commit()

    monkeypatch.setattr(services, "get_valid_access_token", lambda conn: "fake-token")
    monkeypatch.setattr(services, "fetch_page_metrics", lambda *a, **k: FAKE_PAGE_ROWS)
    monkeypatch.setattr(services, "fetch_mobile_share", lambda *a, **k: FAKE_MOBILE_SHARE)
    monkeypatch.setattr(services, "fetch_ga4_site_totals", lambda *a, **k: [])

    sync_ga4_and_generate_tasks(db_session, site.id)
    sync_ga4_and_generate_tasks(db_session, site.id)

    tasks = db_session.query(Task).filter(Task.site_id == site.id, Task.source == "ga4").all()
    assert len(tasks) == 1  # not duplicated


def test_does_not_touch_non_ga4_tasks(db_session, monkeypatch):
    site = _make_site(db_session)
    _make_connection(db_session, site.id)
    db_session.add(Benchmark(site_id=site.id, metric_key="engagement_rate", comparator="lt", target_value=0.55))
    db_session.add(Task(site_id=site.id, source="crawl", category="404_fix", title="Fix", description="D", severity="high"))
    db_session.commit()

    monkeypatch.setattr(services, "get_valid_access_token", lambda conn: "fake-token")
    monkeypatch.setattr(services, "fetch_page_metrics", lambda *a, **k: FAKE_PAGE_ROWS)
    monkeypatch.setattr(services, "fetch_mobile_share", lambda *a, **k: FAKE_MOBILE_SHARE)
    monkeypatch.setattr(services, "fetch_ga4_site_totals", lambda *a, **k: [])

    sync_ga4_and_generate_tasks(db_session, site.id)

    crawl_tasks = db_session.query(Task).filter(Task.site_id == site.id, Task.source == "crawl").all()
    assert len(crawl_tasks) == 1


def test_schedules_from_today_not_campaign_start(db_session, monkeypatch):
    site = _make_site(db_session)
    _make_connection(db_session, site.id)
    db_session.add(Benchmark(site_id=site.id, metric_key="engagement_rate", comparator="lt", target_value=0.55))
    db_session.add(Campaign(site_id=site.id, start_date=dt.date(2020, 1, 1), duration_months=6))
    db_session.commit()

    monkeypatch.setattr(services, "get_valid_access_token", lambda conn: "fake-token")
    monkeypatch.setattr(services, "fetch_page_metrics", lambda *a, **k: FAKE_PAGE_ROWS)
    monkeypatch.setattr(services, "fetch_mobile_share", lambda *a, **k: FAKE_MOBILE_SHARE)
    monkeypatch.setattr(services, "fetch_ga4_site_totals", lambda *a, **k: [])

    sync_ga4_and_generate_tasks(db_session, site.id)

    task = db_session.query(Task).filter(Task.site_id == site.id, Task.source == "ga4").one()
    assert task.target_date >= dt.date.today()
