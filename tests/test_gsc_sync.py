import datetime as dt

import httpx

from app.ingestion.gsc_sync import fetch_gsc_properties, fetch_page_analytics, fetch_page_query_analytics


class _FakeResponse:
    def __init__(self, json_data):
        self._json = json_data

    def raise_for_status(self):
        pass

    def json(self):
        return self._json


def test_fetch_page_analytics_encodes_site_url_and_maps_rows(monkeypatch):
    captured = {}

    def fake_post(url, headers, json, timeout):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        return _FakeResponse(
            {
                "rows": [
                    {"keys": ["https://example.com/a"], "clicks": 5, "impressions": 100, "ctr": 0.05, "position": 4.2},
                ]
            }
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    rows = fetch_page_analytics(
        "token-abc", "https://example.com/", dt.date(2026, 7, 1), dt.date(2026, 7, 28)
    )
    assert rows == [
        {"page": "https://example.com/a", "clicks": 5, "impressions": 100, "ctr": 0.05, "position": 4.2}
    ]
    assert "https%3A%2F%2Fexample.com%2F" in captured["url"]
    assert captured["headers"]["Authorization"] == "Bearer token-abc"
    assert captured["json"]["startDate"] == "2026-07-01"
    assert captured["json"]["endDate"] == "2026-07-28"
    assert captured["json"]["dimensions"] == ["page"]


def test_fetch_page_analytics_returns_empty_list_when_no_rows(monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _FakeResponse({}))
    rows = fetch_page_analytics("token", "https://example.com/", dt.date(2026, 7, 1), dt.date(2026, 7, 28))
    assert rows == []


def test_fetch_page_query_analytics_dimensions_by_page_and_query(monkeypatch):
    captured = {}

    def fake_post(url, headers, json, timeout):
        captured["json"] = json
        return _FakeResponse(
            {
                "rows": [
                    {
                        "keys": ["https://example.com/a", "best widgets"],
                        "clicks": 3,
                        "impressions": 40,
                        "ctr": 0.075,
                        "position": 6.2,
                    },
                ]
            }
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    rows = fetch_page_query_analytics(
        "token-abc", "https://example.com/", dt.date(2026, 7, 1), dt.date(2026, 7, 28)
    )
    assert rows == [
        {
            "page": "https://example.com/a",
            "query": "best widgets",
            "clicks": 3,
            "impressions": 40,
            "ctr": 0.075,
            "position": 6.2,
        }
    ]
    assert captured["json"]["dimensions"] == ["page", "query"]


def test_fetch_page_query_analytics_returns_empty_list_when_no_rows(monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _FakeResponse({}))
    rows = fetch_page_query_analytics("token", "https://example.com/", dt.date(2026, 7, 1), dt.date(2026, 7, 28))
    assert rows == []


# ---------- fetch_gsc_properties (see routers/setup.py's property picker) ----------


def test_fetch_gsc_properties_maps_site_entries(monkeypatch):
    captured = {}

    def fake_get(url, headers, timeout):
        captured["url"] = url
        captured["headers"] = headers
        return _FakeResponse({
            "siteEntry": [
                {"siteUrl": "https://example.com/", "permissionLevel": "siteOwner"},
                {"siteUrl": "sc-domain:example.com", "permissionLevel": "siteFullUser"},
            ]
        })

    monkeypatch.setattr(httpx, "get", fake_get)
    properties = fetch_gsc_properties("some-token")
    assert properties == [
        {"site_url": "https://example.com/", "permission_level": "siteOwner"},
        {"site_url": "sc-domain:example.com", "permission_level": "siteFullUser"},
    ]
    assert captured["url"] == "https://www.googleapis.com/webmasters/v3/sites"
    assert captured["headers"] == {"Authorization": "Bearer some-token"}


def test_fetch_gsc_properties_returns_empty_list_when_none_accessible(monkeypatch):
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResponse({}))
    assert fetch_gsc_properties("token") == []
