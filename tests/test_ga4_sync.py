import datetime as dt

import httpx

from app.ingestion.ga4_sync import fetch_mobile_share, fetch_page_metrics


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
