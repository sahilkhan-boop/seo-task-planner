"""Minimal Google OAuth2 flow for Search Console (and later GA4) -- direct
REST calls via httpx, no google-api-python-client. Kept intentionally small:
this app only ever needs "get an access token for this site's connection",
not the full googleapiclient surface.
"""
from __future__ import annotations

import datetime as dt
from urllib.parse import urlencode

import httpx

from app import config

AUTH_BASE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"

SCOPES = {
    "gsc": "https://www.googleapis.com/auth/webmasters.readonly",
    "ga4": "https://www.googleapis.com/auth/analytics.readonly",
}


def build_auth_url(provider: str, state: str) -> str:
    """`state` carries "{site_id}:{provider}" so the callback knows which site/provider
    this consent flow was for -- Google echoes `state` back unchanged."""
    params = {
        "client_id": config.GOOGLE_CLIENT_ID,
        "redirect_uri": config.GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope": SCOPES[provider],
        "access_type": "offline",  # required to get a refresh_token
        "prompt": "consent",  # forces a refresh_token even if the user connected before
        "state": state,
    }
    return f"{AUTH_BASE_URL}?{urlencode(params)}"


def exchange_code_for_tokens(code: str) -> dict:
    """Returns {"access_token", "refresh_token", "expires_in", ...} from Google."""
    resp = httpx.post(
        TOKEN_URL,
        data={
            "client_id": config.GOOGLE_CLIENT_ID,
            "client_secret": config.GOOGLE_CLIENT_SECRET,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": config.GOOGLE_REDIRECT_URI,
        },
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def get_valid_access_token(connection) -> str:
    """Returns a valid access token for this Connection row, refreshing (and mutating
    the row in place -- caller commits) if it's expired or about to expire."""
    if connection.expires_at > dt.datetime.utcnow() + dt.timedelta(seconds=60):
        return connection.access_token

    resp = httpx.post(
        TOKEN_URL,
        data={
            "client_id": config.GOOGLE_CLIENT_ID,
            "client_secret": config.GOOGLE_CLIENT_SECRET,
            "refresh_token": connection.refresh_token,
            "grant_type": "refresh_token",
        },
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    connection.access_token = data["access_token"]
    connection.expires_at = dt.datetime.utcnow() + dt.timedelta(seconds=data.get("expires_in", 3600))
    return connection.access_token
