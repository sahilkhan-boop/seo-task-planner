"""Pulls per-page engagement/conversion data from the real Google Analytics 4
Data API via a stored OAuth connection. Same style as ingestion/gsc_sync.py --
plain REST calls via httpx, no google-analytics-data client library.

Two calls per sync:
  - fetch_page_metrics: sessions/engagement/bounce/key-events per page
  - fetch_mobile_share: sessions split by device category per page, so the
    rule engine can flag pages under-indexing on mobile traffic

GA4 has no first-class "exit rate" metric the way Universal Analytics did --
`bounceRate` is the closest native equivalent, and is what the "exit_rate"
Benchmark is actually compared against here. Likewise "key events" is
Google's 2024 rename of "conversions"; `keyEvents` is the current Data API
metric name for it.
"""
from __future__ import annotations

import datetime as dt

import httpx

RUN_REPORT_URL_TEMPLATE = "https://analyticsdata.googleapis.com/v1beta/properties/{property_id}:runReport"

PAGE_METRICS = ["sessions", "activeUsers", "engagementRate", "bounceRate", "keyEvents"]


def _run_report(access_token: str, property_id: str, body: dict) -> dict:
    url = RUN_REPORT_URL_TEMPLATE.format(property_id=property_id)
    resp = httpx.post(url, headers={"Authorization": f"Bearer {access_token}"}, json=body, timeout=30)
    resp.raise_for_status()
    return resp.json()


def fetch_page_metrics(
    access_token: str, property_id: str, start_date: dt.date, end_date: dt.date, row_limit: int = 5000
) -> list[dict]:
    """Returns [{"page": path, "sessions": int, "active_users": int, "engagement_rate": float,
    "bounce_rate": float, "key_events": float}, ...] aggregated per page over
    [start_date, end_date] inclusive.
    """
    body = {
        "dateRanges": [{"startDate": start_date.isoformat(), "endDate": end_date.isoformat()}],
        "dimensions": [{"name": "pagePath"}],
        "metrics": [{"name": m} for m in PAGE_METRICS],
        "limit": row_limit,
    }
    data = _run_report(access_token, property_id, body)
    metric_names = [h["name"] for h in data.get("metricHeaders", [])]
    results = []
    for row in data.get("rows", []):
        page = row["dimensionValues"][0]["value"]
        values = {name: row["metricValues"][i]["value"] for i, name in enumerate(metric_names)}
        results.append(
            {
                "page": page,
                "sessions": int(float(values.get("sessions", 0))),
                "active_users": int(float(values.get("activeUsers", 0))),
                "engagement_rate": float(values.get("engagementRate", 0.0)),
                "bounce_rate": float(values.get("bounceRate", 0.0)),
                "key_events": float(values.get("keyEvents", 0.0)),
            }
        )
    return results


def fetch_mobile_share(
    access_token: str, property_id: str, start_date: dt.date, end_date: dt.date, row_limit: int = 10000
) -> dict[str, float]:
    """Returns {page: mobile_session_share} -- mobile sessions / total sessions per page.
    Pages with zero sessions are omitted (nothing to compute a share from).
    """
    body = {
        "dateRanges": [{"startDate": start_date.isoformat(), "endDate": end_date.isoformat()}],
        "dimensions": [{"name": "pagePath"}, {"name": "deviceCategory"}],
        "metrics": [{"name": "sessions"}],
        "limit": row_limit,
    }
    data = _run_report(access_token, property_id, body)
    totals: dict[str, float] = {}
    mobile: dict[str, float] = {}
    for row in data.get("rows", []):
        page = row["dimensionValues"][0]["value"]
        device = row["dimensionValues"][1]["value"]
        sessions = float(row["metricValues"][0]["value"])
        totals[page] = totals.get(page, 0.0) + sessions
        if device == "mobile":
            mobile[page] = mobile.get(page, 0.0) + sessions
    return {page: mobile.get(page, 0.0) / total for page, total in totals.items() if total > 0}
