"""app/rules/worksheet_task_hours.py -- the worksheet's task vocabulary that
feeds the chat agent's plan_tasks_for_urls tool. Mostly a data-integrity check:
every row from the real CSV made it in, with real site-wide hours from Sahil
(confirmed 2026-09-15: these are one-time totals, not per-URL) and a working
fallback for the (currently none) that don't have a real number yet.
"""
from app.rules.task_hours import DEFAULT_TASK_HOURS
from app.rules.worksheet_task_hours import (
    DISCIPLINE_OPTIMIZATION_LEVEL,
    WORKSHEET_TASKS,
    hours_for_worksheet_task,
    missing_worksheet_hours,
    optimization_level_for_worksheet_task,
)


def test_loads_all_65_worksheet_rows():
    assert len(WORKSHEET_TASKS) == 65


def test_every_task_has_a_discipline_with_a_mapped_optimization_level():
    for slug, entry in WORKSHEET_TASKS.items():
        assert entry["discipline"] in DISCIPLINE_OPTIMIZATION_LEVEL, f"{slug} has an unmapped discipline"


def test_hours_for_worksheet_task_returns_the_real_value_when_set():
    assert hours_for_worksheet_task("broken_internal_links_404s") == 40.0
    assert hours_for_worksheet_task("robots_txt_audit") == 30.0


def test_hours_for_worksheet_task_falls_back_to_default_for_unknown_slug():
    assert hours_for_worksheet_task("not_a_real_task") == DEFAULT_TASK_HOURS


def test_optimization_level_for_worksheet_task_matches_its_discipline():
    assert optimization_level_for_worksheet_task("robots_txt_audit") == "key_fix"  # Technical
    assert optimization_level_for_worksheet_task("title_tag_audit") == "quick_win"  # On-Page SEO


def test_optimization_level_for_worksheet_task_none_for_unknown_slug():
    assert optimization_level_for_worksheet_task("not_a_real_task") is None


def test_missing_worksheet_hours_is_empty_now_that_every_row_is_filled():
    assert missing_worksheet_hours() == []


def test_no_stray_blank_row_leaked_in_from_the_csv():
    """The CSV had a trailing malformed row (empty Discipline/Task, stray Hours
    value from a spreadsheet fill-down) -- must be skipped, not loaded as a
    65th+ entry with an empty name."""
    assert "" not in WORKSHEET_TASKS
    assert all(entry["name"] and entry["discipline"] for entry in WORKSHEET_TASKS.values())
