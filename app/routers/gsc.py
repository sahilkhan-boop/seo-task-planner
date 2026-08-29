from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.services import NoConnectionError, sync_gsc_and_generate_tasks

router = APIRouter()


@router.post("/sites/{site_id}/gsc/sync")
def sync_gsc(site_id: int, db: Session = Depends(get_db)):
    try:
        sync_gsc_and_generate_tasks(db, site_id)
        return RedirectResponse(url=f"/sites/{site_id}/tasks", status_code=303)
    except NoConnectionError as exc:
        return RedirectResponse(url=f"/sites/{site_id}/setup/connect?error={exc}", status_code=303)
