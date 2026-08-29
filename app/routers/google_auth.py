"""OAuth connect/callback -- generic across Google providers (gsc now, ga4 later).
Shows a clear message rather than crashing if GOOGLE_CLIENT_ID/SECRET aren't set yet.
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, Query
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import config
from app.db import get_db
from app.google_oauth import build_auth_url, exchange_code_for_tokens
from app.models import Connection

router = APIRouter()


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


@router.get("/auth/google/callback")
def oauth_callback(
    db: Session = Depends(get_db),
    code: str | None = Query(None),
    state: str | None = Query(None),
    error: str | None = Query(None),
):
    if error or not code or not state:
        # e.g. the user clicked "Cancel" on Google's consent screen
        return RedirectResponse(url="/sites?error=" + (error or "Google sign-in was cancelled"), status_code=303)

    try:
        site_id_str, provider = state.split(":", 1)
        site_id = int(site_id_str)
    except ValueError:
        return RedirectResponse(url="/sites?error=Invalid OAuth state", status_code=303)

    token_data = exchange_code_for_tokens(code)
    expires_at = dt.datetime.utcnow() + dt.timedelta(seconds=token_data.get("expires_in", 3600))

    existing = db.scalars(
        select(Connection).where(Connection.site_id == site_id, Connection.provider == provider)
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
                site_id=site_id,
                provider=provider,
                access_token=token_data["access_token"],
                refresh_token=token_data.get("refresh_token", ""),
                expires_at=expires_at,
            )
        )
    db.commit()

    next_step = "/setup/campaign" if provider == "gsc" else "/settings"
    return RedirectResponse(url=f"/sites/{site_id}{next_step}", status_code=303)
