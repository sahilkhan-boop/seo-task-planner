from __future__ import annotations

import csv
import datetime as dt
import io

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse, Response, StreamingResponse
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.db import get_db
from app.exports.excel_calendar import build_calendar_xlsx
from app.exports.pdf_calendar import build_calendar_pdf
from app.models import Campaign, Site, Task
from app.rules.optimization_levels import OPTIMIZATION_LEVELS, OPTIMIZATION_LEVEL_LABELS
from app.scheduling.calendar_grid import build_campaign_calendar, calendar_span_months
from app.services import NoCampaignError, regenerate_content_plan
from app.templating import short_site_label, templates

router = APIRouter()

# Shown in brackets next to every task on the board/calendar -- exactly how/where it
# was populated from, so "why does this task exist" is never a mystery. Categories not
# listed here (a custom chat-created task, most commonly) show no bracket at all.
POPULATED_BY = {
    "technical_audit": "Screaming Frog crawl, consolidated",
    "indexation_blocking": "Screaming Frog crawl",
    "server_error": "Screaming Frog crawl",
    "404_fix": "Screaming Frog crawl",
    "redirect_inlink_update": "Screaming Frog crawl",
    "wasted_impressions": "Content plan — high impressions, near-zero CTR",
    "meta_tag_reoptimization": "GSC — CTR vs. position benchmark",
    "content_expansion": "GSC — query-gap opportunity",
    "ctr_optimization": "GSC — beyond position 15",
    "ui_ux_review": "GA4 — engagement rate",
    "high_exit_rate": "GA4 — bounce rate",
    "low_mobile_share": "GA4 — mobile share, site-wide",
    "low_key_events": "GA4 — key events, site-wide",
    "content_topic_research": "Content plan — stage 1 of 3",
    "content_brief_finalization": "Content plan — stage 2 of 3",
    "content_creation": "Content plan — stage 3 of 3",
    "page_optimization": "Content plan — package size",
    "llm_optimization": "Content plan — alongside content work",
    "prompt_keyword_benchmarking": "Seeded automatically, week 1",
    "schema_recommendations": "Seeded automatically, technical audit week",
    "anchor_optimization": "Seeded automatically, one-time",
    "url_structure_optimization": "Seeded automatically, one-time",
    "performance_dashboard": "Reporting cadence — last week of month 1",
    "weekly_report": "Reporting cadence — every Wednesday",
    "monthly_report_mbr": "Reporting cadence — last Wednesday of the month",
}


# Exact column names/order from a real Screaming Frog "All Inlinks" bulk export
# (Bulk Export > Links > All Inlinks) -- verified against mews.com's own actual
# export file, not approximated -- so this reads as a Screaming Frog file to anyone
# who already has a template/macro/pivot built around one. Columns this app has no
# data for (Size, Alt Text, Follow, Target, Rel, Path Type, Link Path, Link
# Position, Link Origin) are left blank rather than dropped, so the header still
# matches exactly. Task-tracking columns are appended after, clearly separated.
NATIVE_INLINKS_HEADER = [
    "Type", "Source", "Destination", "Size (Bytes)", "Alt Text", "Anchor", "Status Code", "Status",
    "Follow", "Target", "Rel", "Path Type", "Link Path", "Link Position", "Link Origin",
]
TASK_TRACKING_HEADER = ["Category", "Severity", "Task Assignee", "Task Status", "Due Date"]
CRAWL_CSV_HEADER = NATIVE_INLINKS_HEADER + TASK_TRACKING_HEADER

# Real Search Console Performance-report column names (Performance > Pages tab >
# Export) and real GA4 "Pages and screens" report column names (Reports >
# Engagement > Pages and screens) -- kept to exactly those native columns (plus our
# own task-tracking columns after) so the export reads like the real thing, not a
# platform export with extra invented columns mixed in.
GSC_CSV_HEADER = ["Top pages", "Clicks", "Impressions", "CTR", "Position"]
GA4_CSV_HEADER = ["Page path and screen class", "Sessions", "Active users", "Engagement rate", "Bounce rate"]
GSC_GA4_TRACKING_HEADER = ["Benchmark", "Category", "Severity", "Assignee", "Task Status", "Due Date"]


def _write_gsc_ga4_task_row(writer, task: Task, native: str) -> None:
    """One row per affected URL -- a batched task (e.g. "Rewrite title/meta for 205
    page-1 pages") has many pages in affected_urls, and only ever writing
    affected_urls[0] silently dropped every other page from the export.

    Task.url_details carries each page's own FULL native row (Clicks/Impressions/
    CTR/Position for GSC; Sessions/Active users/Engagement rate/Bounce rate for
    GA4) -- see the rule-engine's _native_gsc_row/_native_ga4_row -- so every
    native column gets a real value for every row, not just whichever single
    metric the check that flagged it happened to key off of. Falls back to
    metric_actual in just the one relevant native column (everything else blank)
    for tasks created before this carried a full row -- either an old-shape
    {"metric": value} entry, or no url_details at all for a genuinely unbatched
    legacy task.
    """
    tracking_tail = [
        task.category, task.severity, task.assignee or "Unassigned", task.status,
        task.target_date.isoformat() if task.target_date else "",
    ]
    for page in task.affected_urls or [""]:
        detail = (task.url_details or {}).get(page, {})
        tracking = [task.metric_benchmark if task.metric_benchmark is not None else ""] + tracking_tail
        if native == "gsc":
            if "impressions" in detail:
                writer.writerow([page, detail["clicks"], detail["impressions"], detail["ctr"], detail["position"]] + tracking)
            else:
                legacy_actual = detail.get("metric", task.metric_actual)
                ctr = legacy_actual if task.category in ("meta_tag_reoptimization", "wasted_impressions") else ""
                impressions = legacy_actual if task.category in ("content_expansion", "ctr_optimization") else ""
                writer.writerow([page, "", impressions, ctr, ""] + tracking)
        else:  # ga4
            if "sessions" in detail:
                writer.writerow(
                    [page, detail["sessions"], detail["active_users"], detail["engagement_rate"], detail["bounce_rate"]]
                    + tracking
                )
            else:
                legacy_actual = detail.get("metric", task.metric_actual)
                engagement = legacy_actual if task.category == "ui_ux_review" else ""
                bounce = legacy_actual if task.category == "high_exit_rate" else ""
                writer.writerow([page, "", "", engagement, bounce] + tracking)


def _status_label(status_code: int | None) -> str:
    if status_code is None:
        return ""
    if 200 <= status_code < 300:
        return "OK"
    if 300 <= status_code < 400:
        return "Redirection"
    if status_code == 404:
        return "Not Found"
    if 400 <= status_code < 500:
        return "Client Error"
    if status_code >= 500:
        return "Server Error"
    return ""


# technical_audit rows fall back to these when a url has no per-url category recorded
# in url_details (shouldn't happen for anything consolidated after url_details was
# added, but keeps old/unmigrated data from crashing rather than just reading oddly).
_CATEGORY_STATUS_LABEL = {
    "404_fix": "Not Found",
    "redirect_inlink_update": "Redirection",
    "server_error": "Server Error",
    "indexation_blocking": "Blocked from indexing",
}
# Only 404 has one fixed, always-true numeric code -- redirects span 301/302/307/etc.
# and server errors span 500/502/503/etc., neither of which consolidation retained a
# single number for, so those two get a text Status label with the numeric column left
# blank rather than a fabricated code.
_CATEGORY_STATUS_CODE = {"404_fix": 404}


def _write_crawl_task_rows(writer, task: Task) -> None:
    """One row per affected URL, not one row per task -- a redirect/404 task's whole
    point is the list of pages the analyst needs to go edit, and collapsing that list
    down to just a count (an earlier version of this export did exactly that) leaves
    the export useless for actually doing the work. Shaped as an actual Screaming Frog
    All Inlinks row: Source = the inlinking page, Destination = the issue URL --
    exactly what that native export means by those two columns.

    affected_urls[0] is the issue URL itself for 404_fix/redirect_inlink_update/
    server_error; the rest are inlinking pages (-> Source rows). indexation_blocking's
    and technical_audit's affected_urls are each a flat list of peer URLs instead (no
    single-issue inlink pairing to preserve -- technical_audit mixes several issue
    types into one task, so there's no one "the destination" URL to pair the rest
    against), so each is emitted as its own Destination with Source blank.

    For technical_audit specifically, Task.url_details carries forward each url's
    ORIGINAL sub-issue (404_fix/redirect_inlink_update/server_error/indexation_blocking)
    and severity from before consolidation flattened them together -- without it, every
    one of a consolidated task's rows exported as category="technical_audit"/
    severity="high" with no way to tell which check actually flagged a given url, or
    to cross-reference it back against the crawl the issue came from.
    """
    status_code = int(task.metric_actual) if task.metric_actual is not None else None
    default_label = _status_label(status_code)

    def native_row(source: str, destination: str, code, label: str) -> list:
        return ["Hyperlink", source, destination, "", "", "", code if code is not None else "",
                label, "", "", "", "", "", "", ""]

    def tracking_for(category: str, severity: str) -> list:
        return [category, severity, task.assignee or "Unassigned", task.status,
                task.target_date.isoformat() if task.target_date else ""]

    if task.category in ("indexation_blocking", "technical_audit"):
        for url in task.affected_urls or [""]:
            detail = (task.url_details or {}).get(url, {})
            row_category = detail.get("category", task.category)
            row_severity = detail.get("severity", task.severity)
            row_code = _CATEGORY_STATUS_CODE.get(row_category)
            row_label = _CATEGORY_STATUS_LABEL.get(row_category, default_label)
            writer.writerow(native_row("", url, row_code, row_label) + tracking_for(row_category, row_severity))
        return

    destination = task.affected_urls[0] if task.affected_urls else ""
    inlinks = task.affected_urls[1:] if task.affected_urls else []
    tracking = tracking_for(task.category, task.severity)
    if not inlinks:
        writer.writerow(
            native_row("(none found -- check sitemap/backlinks)", destination, status_code, default_label) + tracking
        )
        return
    for source in inlinks:
        writer.writerow(native_row(source, destination, status_code, default_label) + tracking)


def _filtered_tasks(
    db: Session, site_id: int, status: str | None, category: str | None, severity: str | None,
    optimization_level: str | None = None, assignee: str | None = None,
):
    query = select(Task).where(Task.site_id == site_id)
    if status:
        query = query.where(Task.status == status)
    if category:
        query = query.where(Task.category == category)
    if severity:
        query = query.where(Task.severity == severity)
    if optimization_level:
        query = query.where(Task.optimization_level == optimization_level)
    if assignee:
        # "none" is a real, selectable filter option (see task_board) -- Assignee is a
        # free-text column, not a foreign key, so an unassigned task has assignee=None,
        # not a real name that could collide with this sentinel.
        query = query.where(Task.assignee.is_(None)) if assignee == "none" else query.where(Task.assignee == assignee)
    return db.scalars(query.order_by(Task.month_index.is_(None), Task.month_index, Task.target_date)).all()


def _current_campaign(db: Session, site_id: int) -> Campaign | None:
    return db.scalars(
        select(Campaign).where(Campaign.site_id == site_id).order_by(Campaign.start_date.desc())
    ).first()


def _redirect_after(site_id: int, redirect_to: str) -> str:
    """redirect_to is normally a site-relative path (e.g. "/tasks") that needs the
    current site prefixed onto it. My Tasks (see my_tasks below) is the one place
    that edits tasks belonging to several different sites from a single page, so an
    edit made there needs to redirect back to itself rather than into whichever
    task's own site -- recognized by its own fixed, site-agnostic path instead of
    getting the usual site_id prefix."""
    if redirect_to.startswith("/my-tasks"):
        return redirect_to
    suffix = redirect_to if redirect_to.startswith("/") else f"/{redirect_to}"
    return f"/sites/{site_id}{suffix}"


def _tasks_for_assignee(
    db: Session,
    email: str | None,
    status: str | None = None,
    severity: str | None = None,
    optimization_level: str | None = None,
    site_id: int | None = None,
) -> list[Task]:
    """Every other task view in this app is scoped to one site -- someone assigned
    tasks across several client sites (the normal case: see Connection's shared,
    desktop-wide OAuth model for the same underlying reality) had no single place
    to see their own work without opening each site in turn and filtering by their
    own name. Keyed off the logged-in session email against Task.assignee, which is
    a plain free-text column, not a foreign key -- going forward assignee is meant
    to store the person's email (see the assignee input's own title text in
    tasks.html and the default_assignee hint in setup_campaign.html) so it lines up
    with the session email set at login. Older tasks assigned by first name only
    (e.g. "Priya") simply won't match here until reassigned -- expected, not a bug.

    email=None (login-optional deployments -- config.LOGIN_REQUIRED off, the
    standalone .exe -- have no session email at all) deliberately matches nothing
    rather than every task site-wide against a blank assignee.

    site_id narrows back down to one project -- the whole point of My Tasks is
    seeing every site at once, but a person juggling several client sites still
    sometimes wants just one of them front and center, same as the other filters.
    """
    if not email:
        return []
    query = select(Task).where(Task.assignee == email)
    if status:
        query = query.where(Task.status == status)
    if severity:
        query = query.where(Task.severity == severity)
    if optimization_level:
        query = query.where(Task.optimization_level == optimization_level)
    if site_id:
        query = query.where(Task.site_id == site_id)
    return db.scalars(query.order_by(Task.target_date.is_(None), Task.target_date)).all()


def _sites_for_assignee(db: Session, email: str | None) -> list[Site]:
    """Every project this person has at least one task on -- regardless of the
    other filters (status/severity/optimization_level) -- so the project dropdown
    itself doesn't shrink out options as soon as one of those is applied."""
    if not email:
        return []
    site_ids = set(db.scalars(select(Task.site_id).where(Task.assignee == email).distinct()).all())
    if not site_ids:
        return []
    return list(db.scalars(select(Site).where(Site.id.in_(site_ids)).order_by(Site.domain)).all())


class _SiteLabeledTask:
    """Read-only display wrapper -- prefixes a task's title with its own site's
    short domain label. The calendar/PDF/Excel views (see below) mix tasks from
    every site into one grid; without some per-task site label every cell reads
    like it's all one project. Delegates every other attribute to the real Task
    row via __getattr__ rather than copying/mutating it, so the real row is never
    touched -- this is purely a rendering-time label."""

    def __init__(self, task: Task, site_label: str):
        self._task = task
        self.title = f"[{site_label}] {task.title}"

    def __getattr__(self, name):
        return getattr(self._task, name)


def _labeled_tasks_for_calendar(
    db: Session,
    email: str | None,
    status: str | None = None,
    severity: str | None = None,
    optimization_level: str | None = None,
    site_id: int | None = None,
) -> list:
    tasks = _tasks_for_assignee(db, email, status, severity, optimization_level, site_id)
    site_ids = {t.site_id for t in tasks}
    sites_by_id = {s.id: s for s in db.scalars(select(Site).where(Site.id.in_(site_ids))).all()} if site_ids else {}
    return [_SiteLabeledTask(t, short_site_label(sites_by_id[t.site_id].domain)) for t in tasks]


def _calendar_months_for_tasks(tasks: list) -> list:
    """Span the grid off the tasks' own due dates instead of a campaign's
    start_date/duration_months (what build_campaign_calendar normally gets fed
    from) -- there's no single campaign here, these tasks span 4 different
    sites each with their own campaign on their own schedule. Empty when nothing
    has a due date at all, rather than defaulting to an arbitrary single month."""
    dated = [t.target_date for t in tasks if t.target_date]
    if not dated:
        return []
    start = min(dated).replace(day=1)
    end = max(dated)
    duration_months = (end.year - start.year) * 12 + (end.month - start.month) + 1
    return build_campaign_calendar(start, duration_months, tasks)


@router.get("/my-tasks/calendar")
def my_tasks_calendar(
    request: Request,
    db: Session = Depends(get_db),
    status: str | None = None,
    severity: str | None = None,
    optimization_level: str | None = None,
    site_id: int | None = None,
):
    email = request.session.get("email")
    labeled = _labeled_tasks_for_calendar(db, email, status, severity, optimization_level, site_id)
    months = _calendar_months_for_tasks(labeled)
    all_sites = _sites_for_assignee(db, email)

    return templates.TemplateResponse(
        request,
        "my_tasks_calendar.html",
        {
            "email": email,
            "months": months,
            "today": dt.date.today(),
            "populated_by": POPULATED_BY,
            "optimization_levels": OPTIMIZATION_LEVELS,
            "optimization_level_labels": OPTIMIZATION_LEVEL_LABELS,
            "all_sites": all_sites,
            "filters": {
                "status": status or "", "severity": severity or "", "optimization_level": optimization_level or "",
                "site_id": site_id or "",
            },
            "total": len(labeled),
        },
    )


@router.get("/my-tasks/calendar.pdf")
def my_tasks_calendar_pdf(request: Request, db: Session = Depends(get_db)):
    email = request.session.get("email")
    labeled = _labeled_tasks_for_calendar(db, email)
    months = _calendar_months_for_tasks(labeled)
    pdf_bytes = build_calendar_pdf(email or "My Tasks", months)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": "attachment; filename=my_task_calendar.pdf"},
    )


@router.get("/my-tasks/calendar.xlsx")
def my_tasks_calendar_xlsx(request: Request, db: Session = Depends(get_db)):
    email = request.session.get("email")
    labeled = _labeled_tasks_for_calendar(db, email)
    months = _calendar_months_for_tasks(labeled)
    xlsx_bytes = build_calendar_xlsx(email or "My Tasks", months)
    return Response(
        content=xlsx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=my_task_calendar.xlsx"},
    )


@router.get("/my-tasks")
def my_tasks(
    request: Request,
    db: Session = Depends(get_db),
    status: str | None = None,
    severity: str | None = None,
    optimization_level: str | None = None,
    site_id: int | None = None,
):
    email = request.session.get("email")
    tasks = _tasks_for_assignee(db, email, status, severity, optimization_level, site_id)
    all_sites = _sites_for_assignee(db, email)

    site_ids = {t.site_id for t in tasks}
    sites_by_id = {s.id: s for s in db.scalars(select(Site).where(Site.id.in_(site_ids))).all()} if site_ids else {}

    # Cross-site, not per-site -- this is the same "assigned by name, before the
    # email convention" cleanup surfaced everywhere assignee shows up (see
    # _tasks_for_assignee's own docstring), so gathering it here means the whole
    # backlog can be swept in a couple of clicks instead of hunting site by site.
    other_assignees = sorted({
        t.assignee for t in db.scalars(select(Task)).all() if t.assignee and t.assignee != email
    })

    return templates.TemplateResponse(
        request,
        "my_tasks.html",
        {
            "email": email,
            "tasks": tasks,
            "sites_by_id": sites_by_id,
            "other_assignees": other_assignees,
            "all_sites": all_sites,
            "populated_by": POPULATED_BY,
            "optimization_levels": OPTIMIZATION_LEVELS,
            "optimization_level_labels": OPTIMIZATION_LEVEL_LABELS,
            "filters": {
                "status": status or "", "severity": severity or "", "optimization_level": optimization_level or "",
                "site_id": site_id or "",
            },
            "total": len(tasks),
        },
    )


def _bulk_reassign(db: Session, old_assignee: str, new_assignee: str) -> int:
    """One-time cleanup tool for the "assignee used to be a free-text name"
    transition -- every task currently assigned to old_assignee (e.g. "Sahil" /
    "Sahil Khan", typed by hand before the email convention) moves to
    new_assignee in one shot, across every site, instead of hand-editing each
    task's assignee field one at a time. Self-service and reversible (it's just
    another assignee edit) -- deliberately NOT a raw database script, so it works
    the same on Render's Postgres as it does locally without anyone needing
    direct database access. Returns how many rows moved, so blank/no-op input
    (either field empty after stripping) is a safe no-op rather than a match-
    everything or match-nothing surprise.
    """
    old = old_assignee.strip()
    new = new_assignee.strip()
    if not old or not new:
        return 0
    result = db.execute(update(Task).where(Task.assignee == old).values(assignee=new))
    db.commit()
    return result.rowcount


@router.post("/my-tasks/reassign")
def bulk_reassign(
    request: Request,
    old_assignee: str = Form(...),
    new_assignee: str = Form(...),
    db: Session = Depends(get_db),
):
    _bulk_reassign(db, old_assignee, new_assignee)
    return RedirectResponse(url="/my-tasks", status_code=303)


@router.get("/sites/{site_id}/tasks")
def task_board(
    site_id: int,
    request: Request,
    db: Session = Depends(get_db),
    status: str | None = None,
    category: str | None = None,
    severity: str | None = None,
    optimization_level: str | None = None,
    assignee: str | None = None,
    month: str | None = None,
):
    """One month per page, not the whole campaign at once -- a real backlog can span
    dozens of months and thousands of tasks (each row has 3 separate auto-submitting
    forms), which made the full-campaign page heavy enough to noticeably lag in the
    browser once real data volume showed up."""
    site = db.get(Site, site_id)
    campaign = _current_campaign(db, site_id)
    tasks = _filtered_tasks(db, site_id, status, category, severity, optimization_level, assignee)

    by_month: dict[int | None, list[Task]] = {}
    for t in tasks:
        by_month.setdefault(t.month_index, []).append(t)
    available_months = sorted(by_month.keys(), key=lambda m: (m is None, m))

    if month == "none":
        requested_month = None
    elif month is not None and month.lstrip("-").isdigit():
        requested_month = int(month)
    else:
        requested_month = None if not available_months else available_months[0]

    current_month = requested_month if requested_month in by_month else (available_months[0] if available_months else None)
    current_index = available_months.index(current_month) if current_month in available_months else 0
    has_prev = current_index > 0
    has_next = current_index < len(available_months) - 1
    prev_month = available_months[current_index - 1] if has_prev else None
    next_month = available_months[current_index + 1] if has_next else None

    all_site_tasks = db.scalars(select(Task).where(Task.site_id == site_id)).all()
    all_categories = sorted({t.category for t in all_site_tasks})
    sources_present = {t.source for t in all_site_tasks}
    # Real names only, sorted -- "unassigned" is its own explicit filter option (see
    # the "none" sentinel in _filtered_tasks/the template) rather than one more
    # unpredictable entry mixed into this list.
    all_assignees = sorted({t.assignee for t in all_site_tasks if t.assignee})

    return templates.TemplateResponse(
        request,
        "tasks.html",
        {
            "site": site,
            "campaign": campaign,
            "current_month": current_month,
            "page_tasks": by_month.get(current_month, []),
            "available_months": available_months,
            "prev_month": prev_month,
            "next_month": next_month,
            "has_prev": has_prev,
            "has_next": has_next,
            "all_categories": all_categories,
            "all_assignees": all_assignees,
            "sources_present": sources_present,
            "populated_by": POPULATED_BY,
            "optimization_levels": OPTIMIZATION_LEVELS,
            "optimization_level_labels": OPTIMIZATION_LEVEL_LABELS,
            "filters": {
                "status": status or "", "category": category or "", "severity": severity or "",
                "optimization_level": optimization_level or "", "assignee": assignee or "",
            },
            "total": len(tasks),
        },
    )


@router.get("/sites/{site_id}/tasks/calendar")
def task_calendar(
    site_id: int,
    request: Request,
    db: Session = Depends(get_db),
    status: str | None = None,
    category: str | None = None,
    severity: str | None = None,
    optimization_level: str | None = None,
    assignee: str | None = None,
):
    site = db.get(Site, site_id)
    campaign = _current_campaign(db, site_id)
    tasks = _filtered_tasks(db, site_id, status, category, severity, optimization_level, assignee)
    all_assignees = sorted({
        t.assignee for t in db.scalars(select(Task).where(Task.site_id == site_id)).all() if t.assignee
    })

    months = []
    span_months = campaign.duration_months if campaign else 0
    if campaign:
        # Span is computed off every task for the site, not just the filtered set --
        # otherwise applying a filter could shrink/grow how many months are shown,
        # which should be a campaign-level fact, not a side effect of the current filter.
        all_tasks = db.scalars(select(Task).where(Task.site_id == site_id)).all()
        span_months = calendar_span_months(campaign.duration_months, all_tasks)
        months = build_campaign_calendar(campaign.start_date, span_months, tasks)

    return templates.TemplateResponse(
        request,
        "tasks_calendar.html",
        {
            "site": site,
            "campaign": campaign,
            "months": months,
            "span_months": span_months,
            "today": dt.date.today(),
            "populated_by": POPULATED_BY,
            "optimization_level_labels": OPTIMIZATION_LEVEL_LABELS,
            "all_assignees": all_assignees,
            "filters": {
                "status": status or "", "category": category or "", "severity": severity or "",
                "optimization_level": optimization_level or "", "assignee": assignee or "",
            },
        },
    )


@router.post("/sites/{site_id}/tasks/{task_id}/status")
def update_task_status(
    site_id: int,
    task_id: int,
    status: str = Form(...),
    redirect_to: str = Form("/tasks"),
    db: Session = Depends(get_db),
):
    task = db.get(Task, task_id)
    if task and task.site_id == site_id:
        task.status = status
        db.commit()
    return RedirectResponse(url=_redirect_after(site_id, redirect_to), status_code=303)


@router.post("/sites/{site_id}/tasks/{task_id}/due-date")
def update_task_due_date(
    site_id: int,
    task_id: int,
    target_date: str = Form(...),
    redirect_to: str = Form("/tasks"),
    db: Session = Depends(get_db),
):
    """Manual override for when a task's auto-scheduled date collides with another
    project/deadline on the analyst's plate -- the scheduler's placement is a starting
    point, not a constraint the analyst is stuck with."""
    task = db.get(Task, task_id)
    if task and task.site_id == site_id and target_date.strip():
        try:
            new_date = dt.date.fromisoformat(target_date.strip())
        except ValueError:
            new_date = None
        if new_date:
            task.target_date = new_date
            campaign = _current_campaign(db, site_id)
            if campaign:
                task.month_index = (new_date.year - campaign.start_date.year) * 12 + (new_date.month - campaign.start_date.month)
            # A deliberate human choice -- reschedule_all_tasks leaves it alone on its
            # next run (still counting its hours against that day's capacity) instead
            # of silently re-deriving and overwriting it. See Task.manually_scheduled.
            task.manually_scheduled = True
            db.commit()
    return RedirectResponse(url=_redirect_after(site_id, redirect_to), status_code=303)


@router.post("/sites/{site_id}/tasks/{task_id}/assignee")
def update_task_assignee(
    site_id: int,
    task_id: int,
    assignee: str = Form(""),
    redirect_to: str = Form("/tasks"),
    db: Session = Depends(get_db),
):
    task = db.get(Task, task_id)
    if task and task.site_id == site_id:
        task.assignee = assignee.strip() or None
        db.commit()
    return RedirectResponse(url=_redirect_after(site_id, redirect_to), status_code=303)


@router.post("/sites/{site_id}/tasks/{task_id}/optimization-level")
def update_task_optimization_level(
    site_id: int,
    task_id: int,
    optimization_level: str = Form(""),
    redirect_to: str = Form("/tasks"),
    db: Session = Depends(get_db),
):
    """The analyst's override of which optimization level a task belongs to --
    generated tasks start with a sensible default (see
    app/rules/optimization_levels.py), but this is a plain editable classification,
    not a fixed platform rule, exactly like assignee/status/due-date above."""
    task = db.get(Task, task_id)
    if task and task.site_id == site_id:
        task.optimization_level = optimization_level.strip() or None
        db.commit()
    return RedirectResponse(url=_redirect_after(site_id, redirect_to), status_code=303)


@router.get("/sites/{site_id}/tasks/{task_id}/export.csv")
def export_single_task_csv(site_id: int, task_id: int, db: Session = Depends(get_db)):
    """One task, shaped like its own source's real native export -- for pulling a
    single task out into whatever spreadsheet/ticket the analyst is working from,
    without downloading the whole category. Crawl tasks expand to one row per
    affected URL (see _write_crawl_task_rows), same as the bulk crawl export."""
    task = db.get(Task, task_id)
    if not task or task.site_id != site_id:
        return RedirectResponse(url=f"/sites/{site_id}/tasks", status_code=303)

    buffer = io.StringIO()
    writer = csv.writer(buffer)

    if task.source == "crawl":
        writer.writerow(CRAWL_CSV_HEADER)
        _write_crawl_task_rows(writer, task)
    elif task.category in ("page_optimization", "wasted_impressions") and task.url_details:
        # source="content_plan" like every other recurring task, but these two now
        # carry real GSC-shaped per-page data (see services.py's
        # _ranked_gsc_pages_for_optimization / _ranked_wasted_impression_pages) once
        # a real sync has run -- export it natively instead of the generic one-line
        # content-plan summary below, or its real Clicks/Impressions/CTR/Position
        # would be silently dropped.
        writer.writerow(GSC_CSV_HEADER + GSC_GA4_TRACKING_HEADER)
        _write_gsc_ga4_task_row(writer, task, native="gsc")
    elif task.source == "content_plan":
        writer.writerow(["Publish/Due Date", "Type", "Title", "Assignee", "Status"])
        writer.writerow(
            [
                task.target_date.isoformat() if task.target_date else "",
                task.category.replace("_", " "),
                task.title,
                task.assignee or "Unassigned",
                task.status,
            ]
        )
    elif task.source == "gsc":
        writer.writerow(GSC_CSV_HEADER + GSC_GA4_TRACKING_HEADER)
        _write_gsc_ga4_task_row(writer, task, native="gsc")
    else:  # ga4
        writer.writerow(GA4_CSV_HEADER + GSC_GA4_TRACKING_HEADER)
        _write_gsc_ga4_task_row(writer, task, native="ga4")

    buffer.seek(0)
    safe_title = "".join(c if c.isalnum() else "_" for c in task.title)[:40]
    filename = f"task_{task.id}_{safe_title}.csv"
    return StreamingResponse(
        iter([buffer.getvalue()]), media_type="text/csv", headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@router.post("/sites/{site_id}/tasks/generate-content-plan")
def generate_content_plan_route(site_id: int, db: Session = Depends(get_db)):
    try:
        regenerate_content_plan(db, site_id)
    except NoCampaignError:
        return RedirectResponse(url=f"/sites/{site_id}/setup/campaign", status_code=303)
    return RedirectResponse(url=f"/sites/{site_id}/tasks/calendar", status_code=303)


@router.get("/sites/{site_id}/tasks/export.csv")
def export_tasks_csv(site_id: int, db: Session = Depends(get_db)):
    site = db.get(Site, site_id)
    tasks = db.scalars(select(Task).where(Task.site_id == site_id).order_by(Task.month_index, Task.target_date)).all()

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        ["Month", "Target Date", "Project", "Assignee", "Source", "Category", "Severity", "Status", "Title",
         "Description", "Affected URLs"]
    )
    for t in tasks:
        writer.writerow(
            [
                (t.month_index + 1) if t.month_index is not None else "",
                t.target_date.isoformat() if t.target_date else "",
                site.domain if site else "",
                t.assignee or "Unassigned",
                t.source,
                t.category,
                t.severity,
                t.status,
                t.title,
                t.description,
                "; ".join(t.affected_urls or []),
            ]
        )
    buffer.seek(0)
    filename = f"{site.domain if site else 'seo'}_task_plan.csv"
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get("/sites/{site_id}/tasks/export/crawl.csv")
def export_crawl_csv(site_id: int, db: Session = Depends(get_db)):
    """Real Screaming Frog All Inlinks column shape (see NATIVE_INLINKS_HEADER) --
    one row per affected URL, not one row per task, so every page the analyst needs
    to actually go edit is present, not just a count of how many there are."""
    site = db.get(Site, site_id)
    tasks = db.scalars(
        select(Task).where(Task.site_id == site_id, Task.source == "crawl").order_by(Task.month_index, Task.target_date)
    ).all()

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(CRAWL_CSV_HEADER)
    for t in tasks:
        _write_crawl_task_rows(writer, t)
    buffer.seek(0)
    filename = f"{site.domain if site else 'seo'}_crawl_tasks.csv"
    return StreamingResponse(
        iter([buffer.getvalue()]), media_type="text/csv", headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@router.get("/sites/{site_id}/tasks/export/gsc.csv")
def export_gsc_csv(site_id: int, db: Session = Depends(get_db)):
    """Real Search Console Performance-report column names (Search Console >
    Performance > Pages tab > Export). CTR/Impressions are populated natively when
    that's what the task's category actually measured; Clicks/Position aren't
    persisted on the Task row so they're left blank rather than guessed."""
    site = db.get(Site, site_id)
    tasks = db.scalars(
        select(Task).where(Task.site_id == site_id, Task.source == "gsc").order_by(Task.month_index, Task.target_date)
    ).all()

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(GSC_CSV_HEADER + GSC_GA4_TRACKING_HEADER)
    for t in tasks:
        _write_gsc_ga4_task_row(writer, t, native="gsc")
    buffer.seek(0)
    filename = f"{site.domain if site else 'seo'}_gsc_tasks.csv"
    return StreamingResponse(
        iter([buffer.getvalue()]), media_type="text/csv", headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@router.get("/sites/{site_id}/tasks/export/ga4.csv")
def export_ga4_csv(site_id: int, db: Session = Depends(get_db)):
    """Real GA4 "Pages and screens" report column names (Reports > Engagement >
    Pages and screens). Engagement Rate/Bounce Rate populate natively when that's
    the task's actual category; mobile_share/key_events have no equivalent native
    GA4 report column, so they fall back to the Metric/Value columns instead of
    being invented as a fake native column."""
    site = db.get(Site, site_id)
    tasks = db.scalars(
        select(Task).where(Task.site_id == site_id, Task.source == "ga4").order_by(Task.month_index, Task.target_date)
    ).all()

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(GA4_CSV_HEADER + GSC_GA4_TRACKING_HEADER)
    for t in tasks:
        _write_gsc_ga4_task_row(writer, t, native="ga4")
    buffer.seek(0)
    filename = f"{site.domain if site else 'seo'}_ga4_tasks.csv"
    return StreamingResponse(
        iter([buffer.getvalue()]), media_type="text/csv", headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@router.get("/sites/{site_id}/tasks/export/content_plan.csv")
def export_content_plan_csv(site_id: int, db: Session = Depends(get_db)):
    """Editorial-calendar shape: no source tool to mirror here (this is the platform's
    own recurring content/growth plan), so it's Publish Date + Type + Title -- the
    columns a content calendar spreadsheet actually leads with."""
    site = db.get(Site, site_id)
    tasks = db.scalars(
        select(Task)
        .where(Task.site_id == site_id, Task.source == "content_plan")
        .order_by(Task.month_index, Task.target_date)
    ).all()

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["Publish/Due Date", "Type", "Title", "Assignee", "Status"])
    for t in tasks:
        writer.writerow(
            [
                t.target_date.isoformat() if t.target_date else "",
                t.category.replace("_", " "),
                t.title,
                t.assignee or "Unassigned",
                t.status,
            ]
        )
    buffer.seek(0)
    filename = f"{site.domain if site else 'seo'}_content_plan.csv"
    return StreamingResponse(
        iter([buffer.getvalue()]), media_type="text/csv", headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@router.get("/sites/{site_id}/tasks/calendar.pdf")
def export_calendar_pdf(site_id: int, db: Session = Depends(get_db)):
    site = db.get(Site, site_id)
    campaign = _current_campaign(db, site_id)
    tasks = db.scalars(select(Task).where(Task.site_id == site_id)).all()
    months = (
        build_campaign_calendar(campaign.start_date, calendar_span_months(campaign.duration_months, tasks), tasks)
        if campaign
        else []
    )

    pdf_bytes = build_calendar_pdf(site.domain if site else "site", months)
    filename = f"{site.domain if site else 'seo'}_task_calendar.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get("/sites/{site_id}/tasks/calendar.xlsx")
def export_calendar_xlsx(site_id: int, db: Session = Depends(get_db)):
    site = db.get(Site, site_id)
    campaign = _current_campaign(db, site_id)
    tasks = db.scalars(select(Task).where(Task.site_id == site_id)).all()
    months = (
        build_campaign_calendar(campaign.start_date, calendar_span_months(campaign.duration_months, tasks), tasks)
        if campaign
        else []
    )

    xlsx_bytes = build_calendar_xlsx(site.domain if site else "site", months)
    filename = f"{site.domain if site else 'seo'}_task_calendar.xlsx"
    return Response(
        content=xlsx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
