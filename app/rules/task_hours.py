"""Per-category hour estimates -- how much of a person's 8-hour day each task
category actually takes. Drives services.reschedule_all_tasks' daily-capacity
packing (see its own docstring): tasks fill each business day up to 8 hours
before spilling to the next, instead of the old fixed "one task per week".

These are real, analyst-supplied numbers (Sahil, 2026-08-27), not placeholder
guesses -- every category in optimization_levels.CATEGORY_DEFAULTS has one.
DEFAULT_TASK_HOURS only covers a category this dict doesn't know about (a
custom chat-created task with a made-up category, most commonly).
"""
from __future__ import annotations

DEFAULT_TASK_HOURS = 1.0

HOURS_BY_CATEGORY = {
    # Key Fix
    "technical_audit": 3.0,
    "indexation_blocking": 1.0,
    "server_error": 1.0,
    "404_fix": 1.0,
    "redirect_inlink_update": 1.0,
    "low_mobile_share": 0.75,
    "low_key_events": 0.75,
    "ui_ux_review": 0.75,
    "schema_recommendations": 0.75,
    "url_structure_optimization": 1.0,
    # Quick Win
    "meta_tag_reoptimization": 1.5,
    "ctr_optimization": 1.0,
    "high_exit_rate": 1.0,
    "anchor_optimization": 1.0,
    # Ongoing Content
    "content_topic_research": 1.0,
    "content_brief_finalization": 2.0,
    "content_creation": 4.0,
    "page_optimization": 2.0,
    "wasted_impressions": 1.5,
    "new_page_creation": 1.0,
    "content_expansion": 1.0,
    "llm_optimization": 1.0,
    # Benchmarking / Reporting
    "prompt_keyword_benchmarking": 3.0,
    "performance_dashboard": 3.0,
    "weekly_report": 1.0,
    "monthly_report_mbr": 4.0,
}


def estimated_hours_for(category: str) -> float:
    return HOURS_BY_CATEGORY.get(category, DEFAULT_TASK_HOURS)
