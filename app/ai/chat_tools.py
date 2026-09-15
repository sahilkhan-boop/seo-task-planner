"""Tool definitions + executors for the plan-editing chat.

Every executor takes (db, site_id, tool_input) and returns
(result_for_model, human_summary). `human_summary` is a short plain-English
description of what actually changed (e.g. 'Moved task #12 to 2026-10-03') --
this is what gets shown to the analyst as a record of what the chat did,
never raw tool-call JSON.

Every executor re-checks `task.site_id == site_id` before mutating anything --
the model's own claims about which site/task it's targeting are never trusted
on their own; the route always supplies the real site_id from the URL.
"""
from __future__ import annotations

import datetime as dt
import re

from sqlalchemy.orm import Session

from app.models import Task
from app.rules.task_hours import estimated_hours_for
from app.rules.worksheet_task_hours import (
    WORKSHEET_TASKS,
    hours_for_worksheet_task,
    optimization_level_for_worksheet_task,
)

TOOLS = [
    {
        "name": "list_tasks",
        "description": (
            "List current tasks for this site, optionally filtered by status/category/severity/month. "
            "Use this to see what exists before creating, editing, or deleting anything."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["todo", "in_progress", "done"]},
                "category": {"type": "string"},
                "severity": {"type": "string", "enum": ["high", "medium", "low"]},
                "month_index": {"type": "integer", "description": "0-based month within the campaign"},
            },
        },
    },
    {
        "name": "create_task",
        "description": "Create a new custom task on the plan.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "description": {"type": "string"},
                "category": {"type": "string", "description": "e.g. 'custom', 'content_creation', 'page_optimization'"},
                "severity": {"type": "string", "enum": ["high", "medium", "low"]},
                "optimization_level": {
                    "type": "string",
                    "enum": ["benchmarking", "key_fix", "quick_win", "ongoing_content"],
                    "description": "Analyst-facing priority framing -- what phase of work this belongs to.",
                },
                "target_date": {"type": "string", "description": "YYYY-MM-DD"},
                "assignee": {"type": "string"},
                "affected_urls": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["title", "description"],
        },
    },
    {
        "name": "update_task",
        "description": (
            "Edit an existing task's fields -- status, severity, optimization_level, assignee, "
            "target_date, title, or description. Only supply the fields you want to change."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "integer"},
                "title": {"type": "string"},
                "description": {"type": "string"},
                "severity": {"type": "string", "enum": ["high", "medium", "low"]},
                "optimization_level": {
                    "type": "string",
                    "enum": ["benchmarking", "key_fix", "quick_win", "ongoing_content"],
                    "description": "Analyst-facing priority framing -- what phase of work this belongs to.",
                },
                "status": {"type": "string", "enum": ["todo", "in_progress", "done"]},
                "assignee": {"type": "string"},
                "target_date": {"type": "string", "description": "YYYY-MM-DD"},
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "delete_task",
        "description": "Permanently remove a task from the plan.",
        "input_schema": {
            "type": "object",
            "properties": {"task_id": {"type": "integer"}},
            "required": ["task_id"],
        },
    },
    {
        "name": "plan_tasks_for_urls",
        "description": (
            "Given a batch of URLs and which SEO check/fix each one needs, groups every URL that "
            "needs the SAME check into one task (worksheet hours are site-wide, one-time totals -- "
            "e.g. a 'Canonical tag audit' covering 10 URLs is still one task, not 10), then schedules "
            "every resulting task onto the calendar using the site's real analyst capacity (an "
            "8-hour workday) and priority phases (benchmarking -> technical audit -> key fixes -> "
            "quick wins -> ongoing content) -- the exact same capacity-aware scheduler every "
            "GSC/GA4/crawl-generated task already goes through. This is how planning actually works: "
            "never pick target_date yourself for a batch like this -- call this tool and let it place "
            "them. Match each task to one of the known checks by name where possible (e.g. 'Redirect "
            "chains & loops', 'Canonical tag audit', 'Robots.txt audit') for an accurate time estimate; "
            "a close free-text description still works, just with a conservative 1-hour default."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "entries": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "url": {"type": "string"},
                            "task": {
                                "type": "string",
                                "description": "Which check/fix this URL needs -- a known worksheet task name if possible.",
                            },
                            "severity": {"type": "string", "enum": ["high", "medium", "low"]},
                        },
                        "required": ["url", "task"],
                    },
                }
            },
            "required": ["entries"],
        },
    },
]

MUTATING_TOOLS = {"create_task", "update_task", "delete_task", "plan_tasks_for_urls"}


def _month_index(campaign_start: dt.date, target_date: dt.date) -> int:
    return (target_date.year - campaign_start.year) * 12 + (target_date.month - campaign_start.month)


def _serialize(task: Task) -> dict:
    return {
        "id": task.id,
        "title": task.title,
        "description": task.description,
        "category": task.category,
        "severity": task.severity,
        "optimization_level": task.optimization_level,
        "status": task.status,
        "assignee": task.assignee,
        "target_date": task.target_date.isoformat() if task.target_date else None,
        "month_index": task.month_index,
        "affected_urls": task.affected_urls,
    }


def _list_tasks(db: Session, site_id: int, tool_input: dict):
    query = db.query(Task).filter(Task.site_id == site_id)
    if tool_input.get("status"):
        query = query.filter(Task.status == tool_input["status"])
    if tool_input.get("category"):
        query = query.filter(Task.category == tool_input["category"])
    if tool_input.get("severity"):
        query = query.filter(Task.severity == tool_input["severity"])
    if tool_input.get("month_index") is not None:
        query = query.filter(Task.month_index == tool_input["month_index"])
    tasks = query.order_by(Task.month_index, Task.target_date).limit(200).all()
    return [_serialize(t) for t in tasks], None


def _create_task(db: Session, site_id: int, tool_input: dict, campaign_start_date: dt.date | None):
    target_date = None
    month_index = None
    if tool_input.get("target_date"):
        target_date = dt.date.fromisoformat(tool_input["target_date"])
        if campaign_start_date:
            month_index = _month_index(campaign_start_date, target_date)
    category = tool_input.get("category", "custom")
    task = Task(
        site_id=site_id,
        source="chat",
        category=category,
        title=tool_input["title"],
        description=tool_input["description"],
        affected_urls=tool_input.get("affected_urls", []),
        severity=tool_input.get("severity", "medium"),
        optimization_level=tool_input.get("optimization_level"),
        target_date=target_date,
        month_index=month_index,
        estimated_hours=estimated_hours_for(category),
        # An explicit date given here is a deliberate placement (same reasoning as a
        # manual drag on the Task Plan page) -- reschedule_all_tasks leaves it alone
        # rather than re-deriving and silently moving it on its next run.
        manually_scheduled=target_date is not None,
        assignee=tool_input.get("assignee"),
        status="todo",
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return _serialize(task), f'Created task #{task.id}: "{task.title}"'


def _update_task(db: Session, site_id: int, tool_input: dict, campaign_start_date: dt.date | None):
    task = db.get(Task, tool_input["task_id"])
    if not task or task.site_id != site_id:
        return {"error": "task not found for this site"}, None

    changed_fields = []
    for field in ("title", "description", "severity", "optimization_level", "status", "assignee"):
        if tool_input.get(field) is not None:
            setattr(task, field, tool_input[field])
            changed_fields.append(field)

    if tool_input.get("target_date"):
        task.target_date = dt.date.fromisoformat(tool_input["target_date"])
        if campaign_start_date:
            task.month_index = _month_index(campaign_start_date, task.target_date)
        task.manually_scheduled = True
        changed_fields.append("target_date")

    db.commit()
    summary = f'Updated task #{task.id} ({", ".join(changed_fields)}): "{task.title}"' if changed_fields else None
    return _serialize(task), summary


def _delete_task(db: Session, site_id: int, tool_input: dict):
    task = db.get(Task, tool_input["task_id"])
    if not task or task.site_id != site_id:
        return {"error": "task not found for this site"}, None
    title = task.title
    task_id = task.id
    db.delete(task)
    db.commit()
    return {"deleted": True}, f'Deleted task #{task_id}: "{title}"'


def _match_worksheet_task(task_text: str) -> str | None:
    """Matches free text against a known worksheet task, by slug or by exact
    (case-insensitive) name -- lets the model pass either the worksheet's own
    exact phrasing or a close paraphrase of it and still get a real hours
    estimate + optimization_level instead of falling through to the generic
    'custom' default."""
    text = task_text.strip().lower()
    slug_guess = re.sub(r"[()/]", " ", text)
    slug_guess = re.sub(r"[^a-z0-9]+", "_", slug_guess).strip("_")
    if slug_guess in WORKSHEET_TASKS:
        return slug_guess
    for slug, entry in WORKSHEET_TASKS.items():
        if entry["name"].strip().lower() == text:
            return slug
    return None


_SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2}


def _plan_tasks_for_urls(db: Session, site_id: int, tool_input: dict):
    """Creates one Task per DISTINCT check, not one per URL -- worksheet hours are
    SITE-WIDE, ONE-TIME totals (confirmed with Sahil 2026-09-15; see
    worksheet_task_hours.py's module docstring), so every URL that needs the
    same check is grouped into a single task covering all of them, using that
    check's hours value once -- not multiplied per URL, which would wildly
    overstate the real effort (10 URLs needing a 45h site-wide audit is still
    one 45h audit, not 450h).

    The whole batch is then handed to the same capacity-aware scheduler every
    other task source uses (services.reschedule_all_tasks) -- this is the
    actual "planning": nothing here picks a target_date itself, every new task
    is created unscheduled and manually_scheduled=False specifically so the
    8-hour-day/priority-phase scheduler places it, same as a real GSC/GA4/crawl
    sync would.

    Local import (not top-level) to keep app.services and app.ai decoupled --
    services.py has no reason to import anything from here, and this avoids
    ever having to reason about which one imports the other first.
    """
    from app.services import reschedule_all_tasks

    entries = tool_input.get("entries") or []
    groups: dict[str, dict] = {}  # group_key -> {slug, title, urls, severities}
    unmatched_texts: set[str] = set()

    for entry in entries:
        url = entry.get("url")
        task_text = (entry.get("task") or "").strip()
        if not url or not task_text:
            continue
        slug = _match_worksheet_task(task_text)
        if slug:
            group_key = f"worksheet:{slug}"
            title = WORKSHEET_TASKS[slug]["name"]
        else:
            # No worksheet match -- still plan it rather than silently dropping
            # the URL, just grouped by its own exact text (distinct free-text
            # descriptions never merge with each other) and with the same
            # conservative defaults the rest of this codebase falls back to for
            # an unrecognized category (task_hours.py's DEFAULT_TASK_HOURS /
            # _schedule_phase_for's "ongoing_content" fallback).
            group_key = f"custom:{task_text.lower()}"
            title = task_text
            unmatched_texts.add(task_text)
        group = groups.setdefault(group_key, {"slug": slug, "title": title, "urls": [], "severities": []})
        if url not in group["urls"]:
            group["urls"].append(url)
        group["severities"].append(entry.get("severity", "medium"))

    created: list[Task] = []
    for group in groups.values():
        slug = group["slug"]
        if slug:
            category = slug
            hours = hours_for_worksheet_task(slug)
            optimization_level = optimization_level_for_worksheet_task(slug)
        else:
            category = "custom"
            hours = estimated_hours_for(category)
            optimization_level = None
        # Worst-case severity across the group -- one URL needing this check
        # urgently is enough to raise the whole (now-consolidated) task's severity.
        severity = min(group["severities"], key=lambda s: _SEVERITY_RANK.get(s, 1))
        url_count = len(group["urls"])
        task = Task(
            site_id=site_id,
            source="chat",
            category=category,
            title=f"{group['title']} ({url_count} URL{'s' if url_count != 1 else ''})",
            description=(
                f"Planned by the chat agent from a batch URL request: {group['title']} "
                f"across {url_count} URL(s)."
            ),
            affected_urls=group["urls"],
            severity=severity,
            optimization_level=optimization_level,
            estimated_hours=hours,
            status="todo",
        )
        db.add(task)
        created.append(task)
    db.commit()

    if created:
        reschedule_all_tasks(db, site_id)

    result = [_serialize(t) for t in created]
    total_urls = sum(len(t.affected_urls) for t in created)
    summary = (
        f"Planned {len(created)} task(s) covering {total_urls} URL(s), scheduled within the "
        f"site's 8-hour/day capacity."
    )
    if unmatched_texts:
        summary += (
            f" {len(unmatched_texts)} check(s) didn't match a known worksheet task and used a "
            f"1-hour default estimate."
        )
    return result, summary


def execute_tool(
    db: Session, site_id: int, tool_name: str, tool_input: dict, campaign_start_date: dt.date | None = None
):
    """Returns (result_for_model, human_summary_or_None)."""
    if tool_name == "list_tasks":
        return _list_tasks(db, site_id, tool_input)
    if tool_name == "create_task":
        return _create_task(db, site_id, tool_input, campaign_start_date)
    if tool_name == "update_task":
        return _update_task(db, site_id, tool_input, campaign_start_date)
    if tool_name == "delete_task":
        return _delete_task(db, site_id, tool_input)
    if tool_name == "plan_tasks_for_urls":
        return _plan_tasks_for_urls(db, site_id, tool_input)
    return {"error": f"unknown tool '{tool_name}'"}, None
