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
USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"

SCOPES = {
    "gsc": "https://www.googleapis.com/auth/webmasters.readonly",
    "ga4": "https://www.googleapis.com/auth/analytics.readonly",
    # Just enough to identify who's signing in (see routers/auth.py) -- no Search
    # Console/Analytics data access requested by this flow at all.
    "login": "openid email profile",
}


def build_auth_url(provider: str, state: str) -> str:
    """`state` carries "{site_id}:{provider}" for a GSC/GA4 connect flow, or the
    literal string "login" for the sign-in flow (see routers/auth.py) -- Google
    echoes `state` back unchanged, and the shared callback (routers/google_auth.py)
    branches on which shape it is."""
    params = {
        "client_id": config.GOOGLE_CLIENT_ID,
        "redirect_uri": config.GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope": SCOPES[provider],
        "state": state,
    }
    if provider != "login":
        # offline access / a refresh_token only matters for GSC/GA4, which need to
        # pull data again long after this consent screen -- signing in just needs a
        # one-time "who is this" check, and forcing the "offline access" consent
        # screen for a plain login would be a confusing, unnecessary extra prompt.
        params["access_type"] = "offline"  # required to get a refresh_token
        params["prompt"] = "consent"  # forces a refresh_token even if the user connected before
    return f"{AUTH_BASE_URL}?{urlencode(params)}"


def get_userinfo(access_token: str) -> dict:
    """Returns Google's own verified profile info for whoever just signed in --
    {"email":, "email_verified":, "hd":, "name":, ...}. `hd` (hosted domain) is only
    present for a real Google Workspace account (e.g. peppercontent.io), never for a
    plain @gmail.com one -- routers/auth.py checks the email's own domain suffix
    instead of relying on `hd` being present, since a personal Gmail account
    genuinely has no `hd` claim rather than an absent-but-still-trustworthy one."""
    resp = httpx.get(USERINFO_URL, headers={"Authorization": f"Bearer {access_token}"}, timeout=15)
    resp.raise_for_status()
    return resp.json()


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
