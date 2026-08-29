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

from sqlalchemy.orm import Session

from app.models import Task

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
]

MUTATING_TOOLS = {"create_task", "update_task", "delete_task"}


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
    task = Task(
        site_id=site_id,
        source="chat",
        category=tool_input.get("category", "custom"),
        title=tool_input["title"],
        description=tool_input["description"],
        affected_urls=tool_input.get("affected_urls", []),
        severity=tool_input.get("severity", "medium"),
        optimization_level=tool_input.get("optimization_level"),
        target_date=target_date,
        month_index=month_index,
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
    return {"error": f"unknown tool '{tool_name}'"}, None
