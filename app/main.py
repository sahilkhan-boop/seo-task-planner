from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.db import init_db
from app.paths import STATIC_DIR
from app.routers import benchmarks, chat, ga4, google_auth, gsc, imports, setup, sites, tasks

app = FastAPI(title="SEO Task Planner")


@app.on_event("startup")
def on_startup() -> None:
    init_db()


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

app.include_router(sites.router)
app.include_router(setup.router)
app.include_router(benchmarks.router)
app.include_router(imports.router)
app.include_router(tasks.router)
app.include_router(chat.router)
app.include_router(google_auth.router)
app.include_router(gsc.router)
app.include_router(ga4.router)


@app.get("/")
def root():
    return RedirectResponse(url="/sites")
