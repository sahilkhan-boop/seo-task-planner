from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Benchmark, Site
from app.templating import templates

router = APIRouter()

# Reasonable starting points, not universal truths -- every one of these is
# editable/deletable from the UI. Tuned to cross-industry GA4/GSC averages;
# tighten to a site's own historical curve once a few months of data exist.
# "ctr"/pos_1_5 is the only position-banded CTR benchmark now (tier 1 of the
# GSC content-optimization workflow, app/rules/gsc_rules.py) -- tiers 2-3
# (content_expansion, the beyond-position-15 catch-all) use fixed opportunity
# thresholds instead of a configurable benchmark, same as MIN_IMPRESSIONS.
DEFAULT_BENCHMARKS = [
    dict(metric_key="engagement_rate", segment=None, comparator="lt", target_value=0.55),
    dict(metric_key="exit_rate", segment=None, comparator="gt", target_value=0.60),
    dict(metric_key="mobile_share", segment=None, comparator="lt", target_value=0.35),
    dict(metric_key="key_events", segment=None, comparator="lt", target_value=0.01),
    dict(metric_key="ctr", segment="pos_1_5", comparator="lt", target_value=0.18),
]


@router.get("/sites/{site_id}/benchmarks")
def list_benchmarks(site_id: int, request: Request, db: Session = Depends(get_db)):
    site = db.get(Site, site_id)
    benchmarks = db.scalars(
        select(Benchmark).where(Benchmark.site_id == site_id).order_by(Benchmark.metric_key)
    ).all()
    return templates.TemplateResponse(
        request, "benchmarks.html", {"site": site, "benchmarks": benchmarks}
    )


@router.post("/sites/{site_id}/benchmarks")
def add_benchmark(
    site_id: int,
    metric_key: str = Form(...),
    segment: str = Form(""),
    comparator: str = Form(...),
    target_value: float = Form(...),
    db: Session = Depends(get_db),
):
    db.add(
        Benchmark(
            site_id=site_id,
            metric_key=metric_key.strip(),
            segment=segment.strip() or None,
            comparator=comparator,
            target_value=target_value,
        )
    )
    db.commit()
    return RedirectResponse(url=f"/sites/{site_id}/benchmarks", status_code=303)


@router.post("/sites/{site_id}/benchmarks/seed-defaults")
def seed_defaults(site_id: int, db: Session = Depends(get_db)):
    existing_keys = {
        (b.metric_key, b.segment)
        for b in db.scalars(select(Benchmark).where(Benchmark.site_id == site_id)).all()
    }
    for defaults in DEFAULT_BENCHMARKS:
        if (defaults["metric_key"], defaults["segment"]) not in existing_keys:
            db.add(Benchmark(site_id=site_id, **defaults))
    db.commit()
    return RedirectResponse(url=f"/sites/{site_id}/benchmarks", status_code=303)


@router.post("/benchmarks/{benchmark_id}/delete")
def delete_benchmark(benchmark_id: int, db: Session = Depends(get_db)):
    benchmark = db.get(Benchmark, benchmark_id)
    if benchmark:
        site_id = benchmark.site_id
        db.delete(benchmark)
        db.commit()
        return RedirectResponse(url=f"/sites/{site_id}/benchmarks", status_code=303)
    return RedirectResponse(url="/sites", status_code=303)
