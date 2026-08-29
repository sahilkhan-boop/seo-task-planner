"""Guided setup wizard: Connect platforms -> Campaign & package details.

Same two screens are reused later from Settings for editing, not just for
first-time onboarding.
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import config
from app.db import get_db
from app.google_oauth import build_auth_url
from app.models import Campaign, Connection, CrawlImport, Site
from app.templating import templates

router = APIRouter()


@router.get("/sites/{site_id}/setup/connect")
def connect_step(site_id: int, request: Request, db: Session = Depends(get_db), error: str | None = None):
    site = db.get(Site, site_id)
    has_crawl_data = (
        db.scalars(select(CrawlImport).where(CrawlImport.site_id == site_id)).first() is not None
    )
    gsc_connection = db.scalars(
        select(Connection).where(Connection.site_id == site_id, Connection.provider == "gsc")
    ).first()
    ga4_connection = db.scalars(
        select(Connection).where(Connection.site_id == site_id, Connection.provider == "ga4")
    ).first()
    return templates.TemplateResponse(
        request,
        "setup_connect.html",
        {
            "site": site,
            "has_crawl_data": has_crawl_data,
            "gsc_connected": gsc_connection is not None,
            "ga4_connected": ga4_connection is not None,
            "google_oauth_configured": config.google_oauth_configured(),
            "error": error,
        },
    )


def _save_connect_fields(
    site: Site,
    gsc_site_url: str,
    ga4_property_id: str,
    brand_terms: str,
    brand_regex: str,
    gsc_page_filter_regex: str,
    gsc_page_filter_mode: str,
    gsc_query_filter_regex: str,
    gsc_query_filter_mode: str,
    ga4_page_filter_regex: str,
    ga4_page_filter_mode: str,
) -> None:
    """Shared by all three connect-step POST routes below (plain save, GSC-oauth,
    GA4-oauth) -- each needs to persist the same fields before doing its own thing
    (redirect to campaign step, or kick off the OAuth flow)."""
    site.gsc_site_url = gsc_site_url.strip() or None
    site.ga4_property_id = ga4_property_id.strip() or None
    site.brand_terms = brand_terms.strip() or None
    site.brand_regex = brand_regex.strip() or None
    site.gsc_page_filter_regex = gsc_page_filter_regex.strip() or None
    site.gsc_page_filter_mode = gsc_page_filter_mode if gsc_page_filter_mode == "exclude" else "include"
    site.gsc_query_filter_regex = gsc_query_filter_regex.strip() or None
    site.gsc_query_filter_mode = gsc_query_filter_mode if gsc_query_filter_mode == "exclude" else "include"
    site.ga4_page_filter_regex = ga4_page_filter_regex.strip() or None
    site.ga4_page_filter_mode = ga4_page_filter_mode if ga4_page_filter_mode == "exclude" else "include"


@router.post("/sites/{site_id}/setup/connect")
def save_connect_step(
    site_id: int,
    gsc_site_url: str = Form(""),
    ga4_property_id: str = Form(""),
    brand_terms: str = Form(""),
    brand_regex: str = Form(""),
    gsc_page_filter_regex: str = Form(""),
    gsc_page_filter_mode: str = Form("include"),
    gsc_query_filter_regex: str = Form(""),
    gsc_query_filter_mode: str = Form("include"),
    ga4_page_filter_regex: str = Form(""),
    ga4_page_filter_mode: str = Form("include"),
    db: Session = Depends(get_db),
):
    site = db.get(Site, site_id)
    _save_connect_fields(
        site, gsc_site_url, ga4_property_id, brand_terms, brand_regex, gsc_page_filter_regex, gsc_page_filter_mode,
        gsc_query_filter_regex, gsc_query_filter_mode, ga4_page_filter_regex, ga4_page_filter_mode,
    )
    db.commit()
    return RedirectResponse(url=f"/sites/{site_id}/setup/campaign", status_code=303)


@router.post("/sites/{site_id}/setup/connect/gsc-oauth")
def save_connect_step_and_start_oauth(
    site_id: int,
    gsc_site_url: str = Form(""),
    ga4_property_id: str = Form(""),
    brand_terms: str = Form(""),
    brand_regex: str = Form(""),
    gsc_page_filter_regex: str = Form(""),
    gsc_page_filter_mode: str = Form("include"),
    gsc_query_filter_regex: str = Form(""),
    gsc_query_filter_mode: str = Form("include"),
    ga4_page_filter_regex: str = Form(""),
    ga4_page_filter_mode: str = Form("include"),
    db: Session = Depends(get_db),
):
    """Same save as save_connect_step, but then redirects into the Google OAuth
    flow instead of to campaign/package -- the "Connect via Google" button submits
    here (via formaction) so the property URL is saved before the redirect."""
    site = db.get(Site, site_id)
    _save_connect_fields(
        site, gsc_site_url, ga4_property_id, brand_terms, brand_regex, gsc_page_filter_regex, gsc_page_filter_mode,
        gsc_query_filter_regex, gsc_query_filter_mode, ga4_page_filter_regex, ga4_page_filter_mode,
    )
    db.commit()

    if not config.google_oauth_configured():
        return RedirectResponse(
            url=f"/sites/{site_id}/setup/connect?error=GOOGLE_CLIENT_ID/SECRET not set in .env yet",
            status_code=303,
        )
    return RedirectResponse(url=build_auth_url("gsc", f"{site_id}:gsc"), status_code=303)


@router.post("/sites/{site_id}/setup/connect/ga4-oauth")
def save_connect_step_and_start_oauth_ga4(
    site_id: int,
    gsc_site_url: str = Form(""),
    ga4_property_id: str = Form(""),
    brand_terms: str = Form(""),
    brand_regex: str = Form(""),
    gsc_page_filter_regex: str = Form(""),
    gsc_page_filter_mode: str = Form("include"),
    gsc_query_filter_regex: str = Form(""),
    gsc_query_filter_mode: str = Form("include"),
    ga4_page_filter_regex: str = Form(""),
    ga4_page_filter_mode: str = Form("include"),
    db: Session = Depends(get_db),
):
    """Same save-then-redirect pattern as save_connect_step_and_start_oauth, for the
    GA4 "Connect via Google" button -- the property ID needs saving before the redirect
    into consent, same reason as GSC's version."""
    site = db.get(Site, site_id)
    _save_connect_fields(
        site, gsc_site_url, ga4_property_id, brand_terms, brand_regex, gsc_page_filter_regex, gsc_page_filter_mode,
        gsc_query_filter_regex, gsc_query_filter_mode, ga4_page_filter_regex, ga4_page_filter_mode,
    )
    db.commit()

    if not config.google_oauth_configured():
        return RedirectResponse(
            url=f"/sites/{site_id}/setup/connect?error=GOOGLE_CLIENT_ID/SECRET not set in .env yet",
            status_code=303,
        )
    return RedirectResponse(url=build_auth_url("ga4", f"{site_id}:ga4"), status_code=303)


@router.get("/sites/{site_id}/setup/campaign")
def campaign_step(site_id: int, request: Request, db: Session = Depends(get_db)):
    site = db.get(Site, site_id)
    campaign = db.scalars(
        select(Campaign).where(Campaign.site_id == site_id).order_by(Campaign.start_date.desc())
    ).first()
    return templates.TemplateResponse(request, "setup_campaign.html", {"site": site, "campaign": campaign})


@router.post("/sites/{site_id}/setup/campaign")
def save_campaign_step(
    site_id: int,
    start_date: str = Form(...),
    duration_months: int = Form(6),
    content_pieces_per_month: str = Form(""),
    pages_to_optimize_per_month: str = Form(""),
    page_work_mode: str = Form("optimize_existing"),
    capacity_per_week: int = Form(5),
    default_assignee: str = Form(""),
    notes: str = Form(""),
    consolidate_technical_tasks: str = Form("true"),
    db: Session = Depends(get_db),
):
    campaign = db.scalars(select(Campaign).where(Campaign.site_id == site_id)).first()
    parsed_date = dt.date.fromisoformat(start_date)
    pieces = int(content_pieces_per_month) if content_pieces_per_month.strip() else None
    pages = int(pages_to_optimize_per_month) if pages_to_optimize_per_month.strip() else None
    mode = "create_new" if page_work_mode == "create_new" else "optimize_existing"
    consolidate = consolidate_technical_tasks == "true"
    if campaign:
        campaign.start_date = parsed_date
        campaign.duration_months = duration_months
        campaign.capacity_per_week = capacity_per_week
        campaign.content_pieces_per_month = pieces
        campaign.pages_to_optimize_per_month = pages
        campaign.page_work_mode = mode
        campaign.default_assignee = default_assignee.strip() or None
        campaign.notes = notes.strip() or None
        campaign.consolidate_technical_tasks = consolidate
    else:
        campaign = Campaign(
            site_id=site_id,
            start_date=parsed_date,
            duration_months=duration_months,
            capacity_per_week=capacity_per_week,
            content_pieces_per_month=pieces,
            pages_to_optimize_per_month=pages,
            page_work_mode=mode,
            default_assignee=default_assignee.strip() or None,
            notes=notes.strip() or None,
            consolidate_technical_tasks=consolidate,
        )
        db.add(campaign)
    db.commit()
    return RedirectResponse(url=f"/sites/{site_id}", status_code=303)


@router.get("/sites/{site_id}/settings")
def settings_index(site_id: int, request: Request, db: Session = Depends(get_db)):
    site = db.get(Site, site_id)
    campaign = db.scalars(
        select(Campaign).where(Campaign.site_id == site_id).order_by(Campaign.start_date.desc())
    ).first()
    gsc_connection = db.scalars(
        select(Connection).where(Connection.site_id == site_id, Connection.provider == "gsc")
    ).first()
    ga4_connection = db.scalars(
        select(Connection).where(Connection.site_id == site_id, Connection.provider == "ga4")
    ).first()
    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "site": site,
            "campaign": campaign,
            "gsc_connected": gsc_connection is not None,
            "ga4_connected": ga4_connection is not None,
        },
    )
