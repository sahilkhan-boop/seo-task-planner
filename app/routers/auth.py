"""Login gate -- "Sign in with Google", restricted to accounts on
config.ALLOWED_EMAIL_DOMAIN (peppercontent.io by default). Separate from
routers/google_auth.py's per-site GSC/GA4 *connection* flow (different purpose,
different scope -- see google_oauth.py's SCOPES["login"]), but they share the
same registered Google OAuth redirect URI and callback route, branching on
`state` ("login" here vs "{site_id}:{provider}" there) rather than needing a
second redirect URI registered in Google Cloud Console.

This router's own routes (login/login/start) are deliberately NOT behind
require_login (see app/main.py) -- a page that isn't logged in yet has to be
able to reach the page that lets them log in.
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from app import config
from app.google_oauth import build_auth_url
from app.templating import templates

router = APIRouter()


@router.get("/login")
def login_page(request: Request, error: str | None = None):
    return templates.TemplateResponse(request, "login.html", {"error": error, "config": config})


@router.get("/login/start")
def login_start():
    if not config.google_oauth_configured():
        return RedirectResponse(url="/login?error=GOOGLE_CLIENT_ID/SECRET not set yet", status_code=303)
    return RedirectResponse(url=build_auth_url("login", "login"), status_code=303)


@router.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)
