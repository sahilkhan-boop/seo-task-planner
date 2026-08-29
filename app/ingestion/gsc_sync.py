"""Pulls per-page Search Analytics data from the real Google Search Console API
via a stored OAuth connection. No google-api-python-client -- the Search
Analytics endpoint is a single plain REST call.
"""
from __future__ import annotations

import datetime as dt
from urllib.parse import quote

import httpx

SEARCH_ANALYTICS_URL_TEMPLATE = "https://www.googleapis.com/webmasters/v3/sites/{site_url}/searchAnalytics/query"
SITES_LIST_URL = "https://www.googleapis.com/webmasters/v3/sites"


def fetch_gsc_properties(access_token: str) -> list[dict]:
    """Every Search Console property this OAuth connection's Google account can
    actually access -- {"site_url":, "permission_level":}. Lets the setup flow show
    a real pick-list instead of asking the analyst to hand-type an exact property
    URL (see routers/setup.py's select_gsc_property) -- the #1 way that field ended
    up blank/wrong for real sites (Mews, Accuquote) despite the OAuth connection
    itself succeeding: the connect step and "which exact property" were two
    disconnected manual actions."""
    resp = httpx.get(SITES_LIST_URL, headers={"Authorization": f"Bearer {access_token}"}, timeout=15)
    resp.raise_for_status()
    return [
        {"site_url": entry["siteUrl"], "permission_level": entry.get("permissionLevel", "")}
        for entry in resp.json().get("siteEntry", [])
    ]


def fetch_page_analytics(
    access_token: str, site_url: str, start_date: dt.date, end_date: dt.date, row_limit: int = 5000
) -> list[dict]:
    """Returns [{"page": url, "clicks": int, "impressions": int, "ctr": float, "position": float}, ...]
    aggregated per page over [start_date, end_date] inclusive.
    """
    url = SEARCH_ANALYTICS_URL_TEMPLATE.format(site_url=quote(site_url, safe=""))
    resp = httpx.post(
        url,
        headers={"Authorization": f"Bearer {access_token}"},
        json={
            "startDate": start_date.isoformat(),
            "endDate": end_date.isoformat(),
            "dimensions": ["page"],
            "rowLimit": row_limit,
        },
        timeout=30,
    )
    resp.raise_for_status()
    rows = resp.json().get("rows", [])
    return [
        {
            "page": row["keys"][0],
            "clicks": row.get("clicks", 0),
            "impressions": row.get("impressions", 0),
            "ctr": row.get("ctr", 0.0),
            "position": row.get("position", 0.0),
        }
        for row in rows
    ]


def fetch_page_query_analytics(
    access_token: str, site_url: str, start_date: dt.date, end_date: dt.date, row_limit: int = 25000
) -> list[dict]:
    """Same endpoint, but dimensioned by (page, query) instead of page alone -- needed
    to tell which specific queries are driving a page's impressions, so the content-gap
    rule (app/rules/gsc_rules.py) can tell branded from non-branded demand per page.
    Returns [{"page":..., "query":..., "clicks":..., "impressions":..., "ctr":..., "position":...}, ...].
    """
    url = SEARCH_ANALYTICS_URL_TEMPLATE.format(site_url=quote(site_url, safe=""))
    resp = httpx.post(
        url,
        headers={"Authorization": f"Bearer {access_token}"},
        json={
            "startDate": start_date.isoformat(),
            "endDate": end_date.isoformat(),
            "dimensions": ["page", "query"],
            "rowLimit": row_limit,
        },
        timeout=30,
    )
    resp.raise_for_status()
    rows = resp.json().get("rows", [])
    return [
        {
            "page": row["keys"][0],
            "query": row["keys"][1],
            "clicks": row.get("clicks", 0),
            "impressions": row.get("impressions", 0),
            "ctr": row.get("ctr", 0.0),
            "position": row.get("position", 0.0),
        }
        for row in rows
    ]
