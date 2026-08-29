from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Campaign, CrawlImport, Site, Task
from app.rules.optimization_levels import OPTIMIZATION_LEVEL_LABELS, OPTIMIZATION_LEVELS, default_optimization_level
from app.templating import templates

router = APIRouter()


@router.get("/sites")
def list_sites(request: Request, db: Session = Depends(get_db)):
    sites = db.scalars(select(Site).order_by(Site.created_at.desc())).all()
    return templates.TemplateResponse(request, "sites.html", {"sites": sites})


@router.post("/sites")
def create_site(domain: str = Form(...), db: Session = Depends(get_db)):
    site = Site(domain=domain.strip())
    db.add(site)
    db.commit()
    db.refresh(site)
    # New sites go straight into the guided setup wizard: connect platforms first,
    # then campaign/package details -- rather than landing on an empty dashboard.
    return RedirectResponse(url=f"/sites/{site.id}/setup/connect", status_code=303)


@router.get("/sites/{site_id}")
def site_dashboard(site_id: int, request: Request, db: Session = Depends(get_db)):
    site = db.get(Site, site_id)
    if not site:
        return RedirectResponse(url="/sites", status_code=303)

    campaign = db.scalars(
        select(Campaign).where(Campaign.site_id == site_id).order_by(Campaign.start_date.desc())
    ).first()

    status_counts = dict(
        db.execute(
            select(Task.status, func.count(Task.id)).where(Task.site_id == site_id).group_by(Task.status)
        ).all()
    )
    severity_counts = dict(
        db.execute(
            select(Task.severity, func.count(Task.id)).where(Task.site_id == site_id).group_by(Task.severity)
        ).all()
    )
    # Optimization-level counts are the Overview's headline breakdown (what phase is
    # the work in, not how severe each finding is) -- computed in Python rather than a
    # SQL group-by, since a task with no optimization_level set yet still needs its
    # category-based default applied (see default_optimization_level) to be counted
    # correctly instead of silently vanishing from the summary.
    optimization_level_counts: dict[str, int] = {}
    rows = db.execute(select(Task.optimization_level, Task.category).where(Task.site_id == site_id)).all()
    for level, category in rows:
        resolved = level or default_optimization_level(category) or "unclassified"
        optimization_level_counts[resolved] = optimization_level_counts.get(resolved, 0) + 1

    recent_imports = db.scalars(
        select(CrawlImport).where(CrawlImport.site_id == site_id).order_by(CrawlImport.imported_at.desc()).limit(5)
    ).all()

    return templates.TemplateResponse(
        request,
        "site_dashboard.html",
        {
            "site": site,
            "campaign": campaign,
            "status_counts": status_counts,
            "severity_counts": severity_counts,
            "optimization_level_counts": optimization_level_counts,
            "optimization_levels": OPTIMIZATION_LEVELS,
            "optimization_level_labels": OPTIMIZATION_LEVEL_LABELS,
            "recent_imports": recent_imports,
            "total_tasks": sum(status_counts.values()),
        },
    )
