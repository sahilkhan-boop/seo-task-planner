from app.rules.optimization_levels import (
    CATEGORY_DEFAULTS,
    OPTIMIZATION_LEVELS,
    default_optimization_level,
)


def test_key_fix_categories():
    # ui_ux_review joins the GA4 systemic checks here (not Quick Win) -- the analyst's
    # own ordering groups it with "the rest of the UI/UX and other tasks" that come
    # right after technical work, still within Key Fix.
    for category in ["technical_audit", "indexation_blocking", "server_error", "404_fix",
                      "redirect_inlink_update", "low_mobile_share", "low_key_events", "ui_ux_review"]:
        assert default_optimization_level(category) == "key_fix"


def test_quick_win_categories():
    for category in ["meta_tag_reoptimization", "ctr_optimization", "high_exit_rate"]:
        assert default_optimization_level(category) == "quick_win"


def test_ongoing_content_categories():
    for category in ["content_expansion", "content_creation", "page_optimization", "llm_optimization"]:
        assert default_optimization_level(category) == "ongoing_content"


def test_benchmarking_category():
    assert default_optimization_level("prompt_keyword_benchmarking") == "benchmarking"


def test_unknown_category_has_no_default():
    assert default_optimization_level("custom") is None
    assert default_optimization_level("something_new") is None


def test_every_default_value_is_a_real_optimization_level():
    assert set(CATEGORY_DEFAULTS.values()) <= set(OPTIMIZATION_LEVELS)
