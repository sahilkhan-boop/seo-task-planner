"""app/rules/worksheet_task_hours.py -- the worksheet's task vocabulary that
feeds the chat agent's plan_tasks_for_urls tool. Mostly a data-integrity check:
every row from the real CSV made it in, with a working fallback for anything
Sahil hasn't supplied real hours for yet.
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


def test_hours_for_worksheet_task_falls_back_to_default_when_unset():
    assert hours_for_worksheet_task("robots_txt_audit") == DEFAULT_TASK_HOURS


def test_hours_for_worksheet_task_falls_back_to_default_for_unknown_slug():
    assert hours_for_worksheet_task("not_a_real_task") == DEFAULT_TASK_HOURS


def test_optimization_level_for_worksheet_task_matches_its_discipline():
    assert optimization_level_for_worksheet_task("robots_txt_audit") == "key_fix"  # Technical
    assert optimization_level_for_worksheet_task("title_tag_audit") == "quick_win"  # On-Page SEO


def test_optimization_level_for_worksheet_task_none_for_unknown_slug():
    assert optimization_level_for_worksheet_task("not_a_real_task") is None


def test_missing_worksheet_hours_excludes_the_one_filled_row():
    missing = missing_worksheet_hours()
    assert len(missing) == 64
    assert "broken_internal_links_404s" not in {m["slug"] for m in missing}
    assert all(m["hours"] is None for m in missing)
