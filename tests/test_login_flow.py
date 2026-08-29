"""_finish_login (routers/google_auth.py) -- the domain-restricted login gate.
Real HTTP calls (exchange_code_for_tokens/get_userinfo) are faked the same way
test_google_oauth.py fakes them; the request object only needs a `.session` dict,
same as Starlette's SessionMiddleware actually provides.
"""
import httpx

from app import config
from app.routers.google_auth import _finish_login


class _FakeResponse:
    def __init__(self, json_data):
        self._json = json_data

    def raise_for_status(self):
        pass

    def json(self):
        return self._json


class _FakeRequest:
    def __init__(self):
        self.session = {}


def _stub_google(monkeypatch, email, email_verified=True):
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _FakeResponse({"access_token": "at", "expires_in": 3600}))
    monkeypatch.setattr(
        httpx, "get", lambda *a, **k: _FakeResponse({"email": email, "email_verified": email_verified})
    )


def test_allowed_domain_grants_a_session_and_redirects_to_sites(monkeypatch):
    monkeypatch.setattr(config, "ALLOWED_EMAIL_DOMAIN", "peppercontent.io")
    _stub_google(monkeypatch, "sahil.khan@peppercontent.io")
    request = _FakeRequest()

    response = _finish_login(request, "some-code")

    assert request.session["email"] == "sahil.khan@peppercontent.io"
    assert response.status_code == 303
    assert response.headers["location"] == "/sites"


def test_wrong_domain_is_rejected_and_session_stays_empty(monkeypatch):
    monkeypatch.setattr(config, "ALLOWED_EMAIL_DOMAIN", "peppercontent.io")
    _stub_google(monkeypatch, "someone@gmail.com")
    request = _FakeRequest()

    response = _finish_login(request, "some-code")

    assert "email" not in request.session
    assert response.status_code == 303
    assert response.headers["location"].startswith("/login?error=")


def test_unverified_email_is_rejected_even_on_the_right_domain(monkeypatch):
    """Google's own email_verified flag, not just a domain-suffix string match --
    an unverified email on the right-looking domain still shouldn't grant access."""
    monkeypatch.setattr(config, "ALLOWED_EMAIL_DOMAIN", "peppercontent.io")
    _stub_google(monkeypatch, "sahil@peppercontent.io", email_verified=False)
    request = _FakeRequest()

    response = _finish_login(request, "some-code")

    assert "email" not in request.session
    assert response.status_code == 303
    assert response.headers["location"].startswith("/login?error=")


def test_domain_match_is_a_suffix_check_not_a_substring_check(monkeypatch):
    """A real regression risk with naive string matching: "notpeppercontent.io" or
    "peppercontent.io.evil.com" must NOT pass just because "peppercontent.io"
    appears somewhere in the string."""
    monkeypatch.setattr(config, "ALLOWED_EMAIL_DOMAIN", "peppercontent.io")
    _stub_google(monkeypatch, "someone@notpeppercontent.io")
    request = _FakeRequest()

    response = _finish_login(request, "some-code")

    assert "email" not in request.session
    assert response.status_code == 303
