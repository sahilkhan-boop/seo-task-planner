from __future__ import annotations

from fastapi import Depends, FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app import config
from app.db import init_db
from app.paths import STATIC_DIR
from app.routers import auth, benchmarks, chat, ga4, google_auth, gsc, imports, setup, sites, tasks

app = FastAPI(title="SEO Task Planner")

# Signs the "email" session cookie set on successful login (see routers/auth.py /
# routers/google_auth.py's _finish_login) -- session-only cookie (no explicit
# max_age), so closing the browser ends it; a page reload/new tab keeps it.
app.add_middleware(SessionMiddleware, secret_key=config.SESSION_SECRET_KEY)


class NotLoggedIn(Exception):
    """Raised by require_login below; caught by the handler under it to turn "you
    can't see this" into an actual redirect rather than a raw 500."""


@app.exception_handler(NotLoggedIn)
async def handle_not_logged_in(request: Request, exc: NotLoggedIn) -> RedirectResponse:
    return RedirectResponse(url="/login")


def require_login(request: Request) -> str | None:
    """Dependency applied to every router below except auth.router itself (a page
    that isn't logged in yet has to be able to reach the page that lets them log
    in) -- see the login gate's design note in routers/auth.py. A no-op when
    config.LOGIN_REQUIRED is off (the standalone .exe -- see its own comment on
    LOGIN_REQUIRED for why)."""
    if not config.LOGIN_REQUIRED:
        return None
    email = request.session.get("email")
    if not email:
        raise NotLoggedIn()
    return email


@app.on_event("startup")
def on_startup() -> None:
    init_db()


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

app.include_router(auth.router)
app.include_router(google_auth.callback_router)  # ungated -- see its own comment for why
app.include_router(sites.router, dependencies=[Depends(require_login)])
app.include_router(setup.router, dependencies=[Depends(require_login)])
app.include_router(benchmarks.router, dependencies=[Depends(require_login)])
app.include_router(imports.router, dependencies=[Depends(require_login)])
app.include_router(tasks.router, dependencies=[Depends(require_login)])
app.include_router(chat.router, dependencies=[Depends(require_login)])
app.include_router(google_auth.router, dependencies=[Depends(require_login)])
app.include_router(gsc.router, dependencies=[Depends(require_login)])
app.include_router(ga4.router, dependencies=[Depends(require_login)])


@app.get("/")
def root():
    return RedirectResponse(url="/sites")
