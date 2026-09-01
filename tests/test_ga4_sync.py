import datetime as dt

import httpx

from app.ingestion.ga4_sync import (
    fetch_ga4_properties,
    fetch_mobile_share,
    fetch_page_metrics,
    fetch_site_totals_by_date,
)


class _FakeResponse:
    def __init__(self, json_data):
        self._json = json_data

    def raise_for_status(self):
        pass

    def json(self):
        return self._json


def test_fetch_page_metrics_maps_rows_and_sends_correct_request(monkeypatch):
    captured = {}

    def fake_post(url, headers, json, timeout):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        return _FakeResponse(
            {
                "metricHeaders": [
                    {"name": "sessions"},
                    {"name": "activeUsers"},
                    {"name": "engagementRate"},
                    {"name": "bounceRate"},
                    {"name": "keyEvents"},
                ],
                "rows": [
                    {
                        "dimensionValues": [{"value": "/pricing"}],
                        "metricValues": [
                            {"value": "500"},
                            {"value": "350"},
                            {"value": "0.42"},
                            {"value": "0.55"},
                            {"value": "5"},
                        ],
                    }
                ],
            }
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    rows = fetch_page_metrics("token-abc", "123456789", dt.date(2026, 7, 1), dt.date(2026, 7, 28))

    assert rows == [
        {
            "page": "/pricing",
            "sessions": 500,
            "active_users": 350,
            "engagement_rate": 0.42,
            "bounce_rate": 0.55,
            "key_events": 5.0,
        }
    ]
    assert captured["url"] == "https://analyticsdata.googleapis.com/v1beta/properties/123456789:runReport"
    assert captured["headers"]["Authorization"] == "Bearer token-abc"
    assert captured["json"]["dateRanges"] == [{"startDate": "2026-07-01", "endDate": "2026-07-28"}]
    assert captured["json"]["dimensions"] == [{"name": "pagePath"}]


def test_fetch_page_metrics_returns_empty_list_when_no_rows(monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _FakeResponse({}))
    rows = fetch_page_metrics("token", "123", dt.date(2026, 7, 1), dt.date(2026, 7, 28))
    assert rows == []


def test_fetch_mobile_share_computes_ratio_per_page(monkeypatch):
    def fake_post(url, headers, json, timeout):
        return _FakeResponse(
            {
                "rows": [
                    {"dimensionValues": [{"value": "/a"}, {"value": "mobile"}], "metricValues": [{"value": "30"}]},
                    {"dimensionValues": [{"value": "/a"}, {"value": "desktop"}], "metricValues": [{"value": "70"}]},
                    {"dimensionValues": [{"value": "/b"}, {"value": "mobile"}], "metricValues": [{"value": "0"}]},
                    {"dimensionValues": [{"value": "/b"}, {"value": "tablet"}], "metricValues": [{"value": "10"}]},
                ]
            }
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    shares = fetch_mobile_share("token", "123", dt.date(2026, 7, 1), dt.date(2026, 7, 28))

    assert shares == {"/a": 0.3, "/b": 0.0}


def test_fetch_mobile_share_omits_pages_with_zero_total_sessions(monkeypatch):
    def fake_post(url, headers, json, timeout):
        return _FakeResponse({"rows": []})

    monkeypatch.setattr(httpx, "post", fake_post)
    shares = fetch_mobile_share("token", "123", dt.date(2026, 7, 1), dt.date(2026, 7, 28))
    assert shares == {}


# ---------- fetch_site_totals_by_date (feeds VolumeBenchmark, see rules/volume_rules.py) ----------


def test_fetch_site_totals_by_date_dimensions_by_date_only(monkeypatch):
    captured = {}

    def fake_post(url, headers, json, timeout):
        captured["json"] = json
        return _FakeResponse(
            {
                "rows": [
                    {"dimensionValues": [{"value": "20260829"}], "metricValues": [{"value": "500"}, {"value": "350"}]},
                    {"dimensionValues": [{"value": "20260830"}], "metricValues": [{"value": "600"}, {"value": "400"}]},
                ]
            }
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    rows = fetch_site_totals_by_date("token-abc", "123456789", dt.date(2026, 8, 29), dt.date(2026, 8, 30))

    assert rows == [
        {"date": dt.date(2026, 8, 29), "sessions": 500, "active_users": 350},
        {"date": dt.date(2026, 8, 30), "sessions": 600, "active_users": 400},
    ]
    assert captured["json"]["dimensions"] == [{"name": "date"}]


def test_fetch_site_totals_by_date_returns_empty_list_when_no_rows(monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _FakeResponse({}))
    rows = fetch_site_totals_by_date("token", "123", dt.date(2026, 8, 1), dt.date(2026, 8, 30))
    assert rows == []


# ---------- fetch_ga4_properties (see routers/setup.py's property picker) ----------


def test_fetch_ga4_properties_flattens_accounts_and_strips_the_properties_prefix(monkeypatch):
    captured = {}

    def fake_get(url, headers, params, timeout):
        captured["url"] = url
        captured["headers"] = headers
        return _FakeResponse({
            "accountSummaries": [
                {
                    "displayName": "Acme Inc",
                    "propertySummaries": [
                        {"property": "properties/242923674", "displayName": "acme.com"},
                        {"property": "properties/320230386", "displayName": "acme blog"},
                    ],
                },
                {"displayName": "Other Account", "propertySummaries": [{"property": "properties/999", "displayName": "other.com"}]},
            ]
        })

    monkeypatch.setattr(httpx, "get", fake_get)
    properties = fetch_ga4_properties("some-token")
    assert properties == [
        {"property_id": "242923674", "display_name": "acme.com", "account_name": "Acme Inc"},
        {"property_id": "320230386", "display_name": "acme blog", "account_name": "Acme Inc"},
        {"property_id": "999", "display_name": "other.com", "account_name": "Other Account"},
    ]
    assert captured["url"] == "https://analyticsadmin.googleapis.com/v1beta/accountSummaries"
    assert captured["headers"] == {"Authorization": "Bearer some-token"}


def test_fetch_ga4_properties_returns_empty_list_when_no_accounts(monkeypatch):
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResponse({}))
    assert fetch_ga4_properties("token") == []
