"""OAuth connect/callback -- generic across Google providers (gsc now, ga4 later).
Shows a clear message rather than crashing if GOOGLE_CLIENT_ID/SECRET aren't set yet.
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import config
from app.db import get_db
from app.google_oauth import build_auth_url, exchange_code_for_tokens, get_userinfo
from app.models import Connection

router = APIRouter()
# The callback is registered separately (see app/main.py) and NOT behind
# require_login -- reaching it is exactly how a not-yet-logged-in visitor
# actually becomes logged in (the "login" state branch below); gating it would
# make that impossible to ever complete.
callback_router = APIRouter()


def _finish_login(request: Request, code: str) -> RedirectResponse:
    """The "login" branch of oauth_callback -- exchanges the code for a token (no
    refresh_token needed, this scope never re-fetches data later), fetches the
    signed-in Google account's own verified email via get_userinfo, and only
    actually grants a session if that email's domain matches
    config.ALLOWED_EMAIL_DOMAIN. A personal @gmail.com account (or any other
    domain) completes the Google sign-in step fine but is rejected here, same as
    if they'd never signed in at all."""
    token_data = exchange_code_for_tokens(code)
    userinfo = get_userinfo(token_data["access_token"])
    email = userinfo.get("email")
    if not email or not userinfo.get("email_verified") or not email.lower().endswith(f"@{config.ALLOWED_EMAIL_DOMAIN}"):
        return RedirectResponse(
            url=f"/login?error=Only @{config.ALLOWED_EMAIL_DOMAIN} accounts can sign in", status_code=303
        )
    request.session["email"] = email
    return RedirectResponse(url="/sites", status_code=303)


@router.get("/sites/{site_id}/connect/{provider}")
def start_oauth(site_id: int, provider: str, db: Session = Depends(get_db)):
    if provider not in ("gsc", "ga4"):
        return RedirectResponse(url=f"/sites/{site_id}/setup/connect?error=Unknown provider", status_code=303)
    if not config.google_oauth_configured():
        return RedirectResponse(
            url=f"/sites/{site_id}/setup/connect?error=GOOGLE_CLIENT_ID/SECRET not set in .env yet",
            status_code=303,
        )
    state = f"{site_id}:{provider}"
    return RedirectResponse(url=build_auth_url(provider, state), status_code=303)


@callback_router.get("/auth/google/callback")
def oauth_callback(
    request: Request,
    db: Session = Depends(get_db),
    code: str | None = Query(None),
    state: str | None = Query(None),
    error: str | None = Query(None),
):
    if error or not code or not state:
        # e.g. the user clicked "Cancel" on Google's consent screen -- login and the
        # per-site connect flow show this on two different pages, so route it back
        # to whichever one this actually was.
        message = error or "Google sign-in was cancelled"
        destination = "/login" if state == "login" else "/sites"
        return RedirectResponse(url=f"{destination}?error={message}", status_code=303)

    if state == "login":
        return _finish_login(request, code)

    try:
        site_id_str, provider = state.split(":", 1)
        site_id = int(site_id_str)
    except ValueError:
        return RedirectResponse(url="/sites?error=Invalid OAuth state", status_code=303)

    token_data = exchange_code_for_tokens(code)
    expires_at = dt.datetime.utcnow() + dt.timedelta(seconds=token_data.get("expires_in", 3600))

    # Saved as the SHARED, desktop-wide connection (site_id=None) regardless of which
    # site's Connect button triggered this -- not a per-site row. That's the whole
    # point: connecting once (from whichever site happens to be first) means every
    # other project reuses it automatically, never going through Google's consent
    # screen -- and whatever internal verification/approval it needs -- again. See
    # Connection's own docstring and services.find_connection for the full reasoning.
    existing = db.scalars(
        select(Connection).where(Connection.site_id.is_(None), Connection.provider == provider)
    ).first()
    if existing:
        existing.access_token = token_data["access_token"]
        # Google only returns refresh_token on the FIRST consent (or when prompt=consent forces
        # a fresh one, which we always pass) -- fall back to the existing one if absent.
        existing.refresh_token = token_data.get("refresh_token", existing.refresh_token)
        existing.expires_at = expires_at
    else:
        db.add(
            Connection(
                site_id=None,
                provider=provider,
                access_token=token_data["access_token"],
                refresh_token=token_data.get("refresh_token", ""),
                expires_at=expires_at,
            )
        )
    db.commit()

    # Land on the real property pick-list (routers/setup.py's select_property_page)
    # rather than jumping straight to the next setup step -- OAuth succeeding and
    # the actual property being configured used to be two disconnected manual
    # actions (see that route's own docstring for the real sites this broke).
    return RedirectResponse(
        url=f"/sites/{site_id}/setup/connect/select-property?provider={provider}", status_code=303
    )
