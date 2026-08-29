from app.rules.optimization_levels import CATEGORY_DEFAULTS
from app.rules.task_hours import DEFAULT_TASK_HOURS, HOURS_BY_CATEGORY, estimated_hours_for


def test_every_generated_category_has_a_real_hour_estimate():
    """Every category a rule engine can actually produce (see
    optimization_levels.CATEGORY_DEFAULTS) must have a real, analyst-supplied hour
    estimate here -- a silent fallback to DEFAULT_TASK_HOURS for one of these would
    mean the 8-hour/day scheduler is packing real work against a guessed number
    instead of the real one Sahil provided (2026-08-27)."""
    missing = set(CATEGORY_DEFAULTS) - set(HOURS_BY_CATEGORY)
    assert not missing, f"categories missing a real hour estimate: {missing}"


def test_unknown_category_falls_back_to_the_default():
    assert estimated_hours_for("some_future_custom_category") == DEFAULT_TASK_HOURS


def test_known_category_returns_its_real_estimate():
    assert estimated_hours_for("technical_audit") == 3.0
    assert estimated_hours_for("low_mobile_share") == 0.75
