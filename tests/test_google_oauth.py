import datetime as dt

import httpx
import pytest

from app import config
from app.google_oauth import build_auth_url, exchange_code_for_tokens, get_valid_access_token


class _FakeResponse:
    def __init__(self, json_data, status_code=200):
        self._json = json_data
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=self)

    def json(self):
        return self._json


class _FakeConnection:
    def __init__(self, access_token, refresh_token, expires_at):
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.expires_at = expires_at


def test_build_auth_url_includes_state_scope_and_offline_access(monkeypatch):
    monkeypatch.setattr(config, "GOOGLE_CLIENT_ID", "test-client-id")
    monkeypatch.setattr(config, "GOOGLE_REDIRECT_URI", "http://localhost:8000/auth/google/callback")
    url = build_auth_url("gsc", "42:gsc")
    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert "client_id=test-client-id" in url
    assert "state=42%3Agsc" in url
    assert "access_type=offline" in url
    assert "prompt=consent" in url
    assert "webmasters.readonly" in url


def test_exchange_code_for_tokens_posts_to_token_url_and_returns_json(monkeypatch):
    captured = {}

    def fake_post(url, data, timeout):
        captured["url"] = url
        captured["data"] = data
        return _FakeResponse({"access_token": "at", "refresh_token": "rt", "expires_in": 3600})

    monkeypatch.setattr(httpx, "post", fake_post)
    result = exchange_code_for_tokens("some-code")
    assert result == {"access_token": "at", "refresh_token": "rt", "expires_in": 3600}
    assert captured["url"] == "https://oauth2.googleapis.com/token"
    assert captured["data"]["code"] == "some-code"
    assert captured["data"]["grant_type"] == "authorization_code"


def test_get_valid_access_token_returns_existing_token_when_not_near_expiry(monkeypatch):
    def fail_post(*args, **kwargs):
        raise AssertionError("should not call the network when token is still valid")

    monkeypatch.setattr(httpx, "post", fail_post)
    conn = _FakeConnection("still-valid", "rt", dt.datetime.utcnow() + dt.timedelta(hours=1))
    token = get_valid_access_token(conn)
    assert token == "still-valid"


def test_get_valid_access_token_refreshes_when_expired(monkeypatch):
    def fake_post(url, data, timeout):
        assert data["grant_type"] == "refresh_token"
        assert data["refresh_token"] == "old-rt"
        return _FakeResponse({"access_token": "new-at", "expires_in": 3600})

    monkeypatch.setattr(httpx, "post", fake_post)
    conn = _FakeConnection("expired-at", "old-rt", dt.datetime.utcnow() - dt.timedelta(seconds=5))
    token = get_valid_access_token(conn)
    assert token == "new-at"
    assert conn.access_token == "new-at"
    assert conn.expires_at > dt.datetime.utcnow()


def test_get_valid_access_token_refreshes_when_within_60s_of_expiry(monkeypatch):
    def fake_post(url, data, timeout):
        return _FakeResponse({"access_token": "refreshed", "expires_in": 3600})

    monkeypatch.setattr(httpx, "post", fake_post)
    conn = _FakeConnection("about-to-expire", "old-rt", dt.datetime.utcnow() + dt.timedelta(seconds=30))
    token = get_valid_access_token(conn)
    assert token == "refreshed"
