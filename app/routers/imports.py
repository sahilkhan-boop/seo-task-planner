from __future__ import annotations

import datetime as dt
import os

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.ingestion.screaming_frog import preview_crawl_folder
from app.models import CrawlImport, Site
from app.paths import IMPORTS_DIR
from app.services import run_crawl_import
from app.templating import templates

router = APIRouter()

IMPORTS_ROOT = str(IMPORTS_DIR)


def _resolve_folder(folder: str) -> str:
    return os.path.join(IMPORTS_ROOT, folder.strip().lstrip("/"))


@router.get("/sites/{site_id}/imports")
def list_imports(site_id: int, request: Request, db: Session = Depends(get_db), error: str | None = None):
    site = db.get(Site, site_id)
    imports = db.scalars(
        select(CrawlImport).where(CrawlImport.site_id == site_id).order_by(CrawlImport.imported_at.desc())
    ).all()
    return templates.TemplateResponse(
        request,
        "imports.html",
        {"site": site, "imports": imports, "imports_root": IMPORTS_ROOT, "error": error},
    )


@router.post("/sites/{site_id}/imports/preview")
def preview_import(
    site_id: int,
    request: Request,
    folder: str = Form(...),
    crawl_date: str = Form(...),
    db: Session = Depends(get_db),
):
    """What trigger_import (below) would actually do with this folder, shown first --
    every file's detected type/row count/columns, so the analyst confirms what got
    understood (and notices anything skipped as unrecognized) before tasks get
    generated from it, rather than generation just silently happening on submit."""
    site = db.get(Site, site_id)
    full_path = _resolve_folder(folder)
    if not os.path.isdir(full_path):
        return RedirectResponse(
            url=f"/sites/{site_id}/imports?error=Folder not found: {full_path}", status_code=303
        )
    preview = preview_crawl_folder(full_path)
    return templates.TemplateResponse(
        request,
        "import_preview.html",
        {"site": site, "folder": folder, "crawl_date": crawl_date, "preview": preview},
    )


@router.post("/sites/{site_id}/imports")
def trigger_import(
    site_id: int,
    folder: str = Form(...),
    crawl_date: str = Form(...),
    db: Session = Depends(get_db),
):
    full_path = _resolve_folder(folder)
    if not os.path.isdir(full_path):
        return RedirectResponse(
            url=f"/sites/{site_id}/imports?error=Folder not found: {full_path}", status_code=303
        )
    try:
        run_crawl_import(db, site_id, full_path, dt.date.fromisoformat(crawl_date))
    except FileNotFoundError as exc:
        return RedirectResponse(url=f"/sites/{site_id}/imports?error={exc}", status_code=303)
    return RedirectResponse(url=f"/sites/{site_id}/tasks", status_code=303)
