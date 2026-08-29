import datetime as dt

from app.rules.content_rules import generate_benchmarking_task, generate_content_plan


def test_every_month_of_the_campaign_gets_tasks():
    tasks = generate_content_plan(dt.date(2026, 8, 15), 6, content_pieces_per_month=4, pages_to_optimize_per_month=2)
    months_with_tasks = {t.month_index for t in tasks}
    assert months_with_tasks == set(range(6))  # months 0..5, not just month 0


def test_task_counts_match_the_package_per_month():
    # content work is a fixed 3-stage pipeline (research -> brief -> article) covering
    # the WHOLE month's package as one unit, not one task per piece -- see
    # test_content_pipeline_* below for that behavior specifically. page_optimization
    # is the same: one consolidated task covering all N pages, not N separate
    # look-alike tickets -- see test_page_optimization_* below.
    tasks = generate_content_plan(dt.date(2026, 8, 15), 3, content_pieces_per_month=4, pages_to_optimize_per_month=2)
    for month_index in range(3):
        month_tasks = [t for t in tasks if t.month_index == month_index]
        assert len([t for t in month_tasks if t.category == "content_topic_research"]) == 1
        assert len([t for t in month_tasks if t.category == "content_brief_finalization"]) == 1
        assert len([t for t in month_tasks if t.category == "content_creation"]) == 1
        assert len([t for t in month_tasks if t.category == "page_optimization"]) == 1


def test_dates_land_within_their_own_calendar_month_on_weekdays():
    tasks = generate_content_plan(dt.date(2026, 8, 15), 2, content_pieces_per_month=6, pages_to_optimize_per_month=3)
    for t in tasks:
        assert t.target_date.weekday() < 5
        expected_month = (8 + t.month_index - 1) % 12 + 1
        assert t.target_date.month == expected_month


def test_zero_or_none_package_produces_no_tasks_for_that_category():
    tasks = generate_content_plan(dt.date(2026, 8, 15), 2, content_pieces_per_month=None, pages_to_optimize_per_month=0)
    assert tasks == []


def test_large_package_size_still_produces_one_pipeline_not_one_task_per_piece():
    # a large package (more pieces than a month has business days) still collapses to
    # the same 3-stage pipeline -- the piece count shows up in each stage's
    # description, not as more tasks (same "don't fragment into look-alike tasks"
    # reasoning as crawl_rules.py's campaign-wide batching).
    tasks = generate_content_plan(dt.date(2026, 8, 15), 1, content_pieces_per_month=40, pages_to_optimize_per_month=0)
    assert len([t for t in tasks if t.category == "content_creation"]) == 1
    pipeline = [t for t in tasks if t.category != "llm_optimization"]
    assert len(pipeline) == 3  # research + brief + article, nothing else this month
    assert all("40" in t.description for t in pipeline)


def test_severity_is_low_so_it_reads_as_planned_work_not_an_urgent_fix():
    tasks = generate_content_plan(dt.date(2026, 8, 15), 1, content_pieces_per_month=2, pages_to_optimize_per_month=1)
    assert all(t.severity == "low" for t in tasks)
    assert all(t.source == "content_plan" for t in tasks)


# ---------- llm_optimization (runs alongside real content/optimize work each month) ----------


def test_llm_optimization_generated_once_per_month_with_active_content_work():
    tasks = generate_content_plan(dt.date(2026, 8, 15), 3, content_pieces_per_month=2, pages_to_optimize_per_month=0)
    llm_tasks = [t for t in tasks if t.category == "llm_optimization"]
    assert len(llm_tasks) == 3  # one per month
    assert {t.month_index for t in llm_tasks} == {0, 1, 2}


def test_llm_optimization_skipped_when_no_content_work_that_campaign():
    tasks = generate_content_plan(dt.date(2026, 8, 15), 2, content_pieces_per_month=None, pages_to_optimize_per_month=0)
    assert not [t for t in tasks if t.category == "llm_optimization"]


def test_llm_optimization_lands_on_a_different_day_than_content_creation():
    tasks = generate_content_plan(dt.date(2026, 8, 15), 1, content_pieces_per_month=1, pages_to_optimize_per_month=0)
    content_date = next(t.target_date for t in tasks if t.category == "content_creation")
    llm_date = next(t.target_date for t in tasks if t.category == "llm_optimization")
    assert llm_date != content_date


# ---------- generate_benchmarking_task (one-off week-1 kickoff task) ----------


def test_benchmarking_task_is_a_single_week_one_task():
    task = generate_benchmarking_task(dt.date(2026, 8, 24))
    assert task.category == "prompt_keyword_benchmarking"
    assert task.target_date == dt.date(2026, 8, 24)
    assert task.month_index == 0
    assert "Benchmarking" in task.title


# ---------- content pipeline (research -> brief -> article, one deliverable/week) ----------


def test_content_pipeline_has_exactly_three_stages_per_month():
    tasks = generate_content_plan(dt.date(2026, 8, 15), 1, content_pieces_per_month=8, pages_to_optimize_per_month=0)
    pipeline_categories = [t.category for t in tasks if t.category != "llm_optimization"]
    assert pipeline_categories == ["content_topic_research", "content_brief_finalization", "content_creation"]


def test_content_pipeline_stages_land_on_increasing_dates_within_the_month():
    tasks = generate_content_plan(dt.date(2026, 8, 15), 1, content_pieces_per_month=8, pages_to_optimize_per_month=0)
    research, brief, article = (t.target_date for t in tasks if t.category != "llm_optimization")
    assert research < brief < article


# ---------- page optimization (one consolidated task/month, not one per page) ----------


def test_page_optimization_is_a_single_task_regardless_of_page_count():
    """Regression: page_optimization used to generate one look-alike task per page
    ("Optimize existing page for more traffic 1/8", "2/8", ...) -- the exact
    "N identical-looking tasks" problem the content pipeline above was already
    written to avoid, just never applied here. One consolidated task covering all
    N pages instead, same as content_creation covers all N pieces."""
    tasks = generate_content_plan(dt.date(2026, 8, 15), 1, content_pieces_per_month=0, pages_to_optimize_per_month=8)
    page_opt_tasks = [t for t in tasks if t.category == "page_optimization"]
    assert len(page_opt_tasks) == 1
    assert "8" in page_opt_tasks[0].title


def test_page_optimization_lands_after_the_content_pipeline_within_the_month():
    tasks = generate_content_plan(dt.date(2026, 8, 15), 1, content_pieces_per_month=4, pages_to_optimize_per_month=6)
    article_date = next(t.target_date for t in tasks if t.category == "content_creation")
    page_opt_date = next(t.target_date for t in tasks if t.category == "page_optimization")
    assert page_opt_date > article_date


def test_page_optimization_generated_alone_when_no_content_pieces_this_campaign():
    tasks = generate_content_plan(dt.date(2026, 8, 15), 2, content_pieces_per_month=None, pages_to_optimize_per_month=5)
    assert len([t for t in tasks if t.category == "page_optimization"]) == 2  # one per month
    assert not [t for t in tasks if t.category in ("content_topic_research", "content_brief_finalization", "content_creation")]


# ---------- page_optimization ranked by real GSC data (highest impressions first) ----------


def _ranked(n, start_impressions=100_000, step=-1000):
    """n fake ranked pages, descending impressions, matching what
    services._ranked_gsc_pages_for_optimization would hand generate_content_plan."""
    return [
        {"page": f"https://x.com/p{i}", "impressions": start_impressions + step * i, "clicks": 10, "ctr": 0.01, "position": 5.0}
        for i in range(n)
    ]


def test_page_optimization_uses_the_top_n_ranked_pages_for_month_zero():
    """Batch size is optimize_n PLUS PAGE_OPTIMIZATION_BUFFER (8 + 2 = 10 spare
    backups) -- see test_page_optimization_batch_includes_the_buffer below for
    that specifically."""
    ranked = _ranked(20)
    tasks = generate_content_plan(
        dt.date(2026, 8, 15), 1, content_pieces_per_month=0, pages_to_optimize_per_month=8, ranked_pages=ranked,
    )
    [task] = [t for t in tasks if t.category == "page_optimization"]
    assert task.affected_urls == [p["page"] for p in ranked[:10]]  # 8 planned + 2 buffer, in rank order


def test_page_optimization_advances_to_the_next_tier_the_following_month():
    """The whole point: month 1 doesn't re-suggest month 0's pages -- it picks up
    exactly where month 0 left off in the ranking (by the full buffered batch
    size, not just the base quota -- see test_page_optimization_batch_includes_the_buffer)."""
    ranked = _ranked(20)
    tasks = generate_content_plan(
        dt.date(2026, 8, 15), 2, content_pieces_per_month=0, pages_to_optimize_per_month=8, ranked_pages=ranked,
    )
    month0, month1 = [t for t in tasks if t.category == "page_optimization"]
    assert month0.affected_urls == [p["page"] for p in ranked[:10]]
    assert month1.affected_urls == [p["page"] for p in ranked[10:20]]
    assert not set(month0.affected_urls) & set(month1.affected_urls)  # zero overlap


def test_page_optimization_batch_includes_the_buffer():
    """The exported list is the base quota PLUS PAGE_OPTIMIZATION_BUFFER spare
    pages, so the analyst always has backups in the same export if the client
    passes on a pick -- not just exactly the configured quota."""
    from app.rules.content_rules import PAGE_OPTIMIZATION_BUFFER

    ranked = _ranked(20)
    tasks = generate_content_plan(
        dt.date(2026, 8, 15), 1, content_pieces_per_month=0, pages_to_optimize_per_month=8, ranked_pages=ranked,
    )
    [task] = [t for t in tasks if t.category == "page_optimization"]
    assert len(task.affected_urls) == 8 + PAGE_OPTIMIZATION_BUFFER
    assert "backup" in task.description


def test_page_optimization_carries_each_pages_real_metrics_in_url_details():
    ranked = _ranked(8)
    [task] = [
        t for t in generate_content_plan(
            dt.date(2026, 8, 15), 1, content_pieces_per_month=0, pages_to_optimize_per_month=8, ranked_pages=ranked,
        )
        if t.category == "page_optimization"
    ]
    for p in ranked:
        detail = task.url_details[p["page"]]
        assert detail["impressions"] == p["impressions"]
        assert detail["clicks"] == p["clicks"]
        assert detail["ctr"] == p["ctr"]
        assert detail["position"] == p["position"]


def test_page_optimization_falls_back_to_generic_placeholder_with_no_ranked_pages():
    """No GSC connection/sync yet -- ranked_pages is None, not an empty list. Same
    generic "go pick pages yourself" placeholder as before this feature existed,
    not a fabricated or empty page list."""
    tasks = generate_content_plan(
        dt.date(2026, 8, 15), 1, content_pieces_per_month=0, pages_to_optimize_per_month=8, ranked_pages=None,
    )
    [task] = [t for t in tasks if t.category == "page_optimization"]
    assert task.affected_urls == []
    assert "8" in task.title


def test_page_optimization_uses_a_partial_batch_rather_than_discard_real_pages():
    """Only 14 real pages exist -- month 1's slice (ranks 10-19, batch size 10 =
    8 planned + 2 buffer) only has 4 real pages left. Using those 4 real pages is
    more honest than either fabricating 6 more or discarding 4 perfectly good ones
    just to hit a round number."""
    ranked = _ranked(14)
    tasks = generate_content_plan(
        dt.date(2026, 8, 15), 2, content_pieces_per_month=0, pages_to_optimize_per_month=8, ranked_pages=ranked,
    )
    month0, month1 = [t for t in tasks if t.category == "page_optimization"]
    assert month0.affected_urls == [p["page"] for p in ranked[:10]]
    assert month1.affected_urls == [p["page"] for p in ranked[10:14]]  # only 4 real pages left, not 10


def test_page_optimization_falls_back_once_the_ranked_list_is_fully_exhausted():
    """Once a month's slice has NO real pages left at all (not even a partial
    batch), fall back to the generic placeholder rather than an empty task."""
    ranked = _ranked(10)  # exactly enough for month 0's buffered batch, nothing left for month 1
    tasks = generate_content_plan(
        dt.date(2026, 8, 15), 2, content_pieces_per_month=0, pages_to_optimize_per_month=8, ranked_pages=ranked,
    )
    month0, month1 = [t for t in tasks if t.category == "page_optimization"]
    assert month0.affected_urls == [p["page"] for p in ranked[:10]]
    assert month1.affected_urls == []
    assert "8" in month1.title  # generic placeholder, not "0 pages"


# ---------- page_work_mode="create_new" (no existing pages worth optimizing) ----------


def test_create_new_mode_generates_new_page_creation_not_page_optimization():
    tasks = generate_content_plan(
        dt.date(2026, 8, 15), 1, content_pieces_per_month=0, pages_to_optimize_per_month=5,
        page_work_mode="create_new",
    )
    categories = {t.category for t in tasks}
    assert "new_page_creation" in categories
    assert "page_optimization" not in categories
    [task] = [t for t in tasks if t.category == "new_page_creation"]
    assert "5" in task.title


def test_optimize_existing_is_the_default_mode():
    tasks = generate_content_plan(dt.date(2026, 8, 15), 1, content_pieces_per_month=0, pages_to_optimize_per_month=5)
    categories = {t.category for t in tasks}
    assert "page_optimization" in categories
    assert "new_page_creation" not in categories


def test_create_new_mode_still_consolidates_to_one_task_per_month():
    tasks = generate_content_plan(
        dt.date(2026, 8, 15), 2, content_pieces_per_month=0, pages_to_optimize_per_month=8,
        page_work_mode="create_new",
    )
    for month_index in range(2):
        month_tasks = [t for t in tasks if t.month_index == month_index and t.category == "new_page_creation"]
        assert len(month_tasks) == 1  # not 8 look-alike tasks


def test_create_new_mode_still_slots_into_the_same_weekly_rhythm():
    tasks = generate_content_plan(
        dt.date(2026, 8, 15), 1, content_pieces_per_month=4, pages_to_optimize_per_month=6,
        page_work_mode="create_new",
    )
    article_date = next(t.target_date for t in tasks if t.category == "content_creation")
    new_page_date = next(t.target_date for t in tasks if t.category == "new_page_creation")
    assert new_page_date > article_date


def test_start_month_work_never_lands_before_the_campaign_actually_starts():
    # campaign starts mid-month (the 24th) -- nothing that month should be scheduled
    # before that, even though the calendar month itself started on the 1st.
    start = dt.date(2026, 8, 24)
    tasks = generate_content_plan(start, 1, content_pieces_per_month=8, pages_to_optimize_per_month=3)
    assert all(t.target_date >= start for t in tasks)


def test_later_months_are_unaffected_by_the_start_date_filter():
    start = dt.date(2026, 8, 24)
    tasks = generate_content_plan(start, 2, content_pieces_per_month=8, pages_to_optimize_per_month=0)
    month_1_tasks = [t for t in tasks if t.month_index == 1]
    assert month_1_tasks  # September still gets its full pipeline
    assert all(t.target_date.month == 9 for t in month_1_tasks)


def test_content_pipeline_scales_with_package_size_only_in_description():
    small = [t for t in generate_content_plan(dt.date(2026, 8, 15), 1, content_pieces_per_month=2, pages_to_optimize_per_month=0) if t.category != "llm_optimization"]
    large = [t for t in generate_content_plan(dt.date(2026, 8, 15), 1, content_pieces_per_month=8, pages_to_optimize_per_month=0) if t.category != "llm_optimization"]
    assert len(small) == len(large) == 3  # same 3 tasks regardless of package size
    assert any("2 pieces" in t.title or "2 topics" in t.description for t in small)
    assert any("8 pieces" in t.title or "8 topics" in t.description for t in large)


# ---------- prompt-analysis cross-reference (topic selection informed by benchmarking) ----------


def test_topic_research_cross_references_the_prompt_analysis_findings():
    [research] = [
        t for t in generate_content_plan(dt.date(2026, 9, 1), 1, content_pieces_per_month=4, pages_to_optimize_per_month=0)
        if t.category == "content_topic_research"
    ]
    assert "Prompt Analysis & Keyword Research" in research.description


def test_llm_optimization_calls_out_specific_third_party_platforms():
    [llm_task] = [
        t for t in generate_content_plan(dt.date(2026, 9, 1), 1, content_pieces_per_month=4, pages_to_optimize_per_month=0)
        if t.category == "llm_optimization"
    ]
    assert "Prompt Analysis & Keyword Research" in llm_task.description
    assert "LinkedIn" in llm_task.description and "Reddit" in llm_task.description
    assert "generic" in llm_task.description.lower()  # explicitly warns against a generic "social" placeholder


# ---------- wasted_impressions (same monthly, ranked, non-repeating pacing as
# page_optimization -- see services.py's _ranked_wasted_impression_pages) ----------


def test_wasted_impressions_generated_alongside_page_optimization_by_default():
    tasks = generate_content_plan(dt.date(2026, 8, 15), 1, content_pieces_per_month=0, pages_to_optimize_per_month=8)
    categories = {t.category for t in tasks}
    assert "wasted_impressions" in categories
    assert "page_optimization" in categories


def test_wasted_impressions_skipped_in_create_new_mode():
    """No existing-page backlog to draw from in create_new mode -- same gating as
    page_optimization itself (see the page_optimization/new_page_creation branch)."""
    tasks = generate_content_plan(
        dt.date(2026, 8, 15), 1, content_pieces_per_month=0, pages_to_optimize_per_month=8,
        page_work_mode="create_new",
    )
    assert "wasted_impressions" not in {t.category for t in tasks}


def test_wasted_impressions_skipped_when_no_page_work_this_campaign():
    tasks = generate_content_plan(dt.date(2026, 8, 15), 1, content_pieces_per_month=4, pages_to_optimize_per_month=0)
    assert "wasted_impressions" not in {t.category for t in tasks}


def test_wasted_impressions_uses_the_top_ranked_pages_for_month_zero():
    ranked = _ranked(20)
    tasks = generate_content_plan(
        dt.date(2026, 8, 15), 1, content_pieces_per_month=0, pages_to_optimize_per_month=8,
        ranked_wasted_pages=ranked,
    )
    [task] = [t for t in tasks if t.category == "wasted_impressions"]
    assert task.affected_urls == [p["page"] for p in ranked[:10]]  # 8 planned + 2 buffer, in rank order


def test_wasted_impressions_advances_to_the_next_batch_the_following_month():
    ranked = _ranked(20)
    tasks = generate_content_plan(
        dt.date(2026, 8, 15), 2, content_pieces_per_month=0, pages_to_optimize_per_month=8,
        ranked_wasted_pages=ranked,
    )
    month0, month1 = [t for t in tasks if t.category == "wasted_impressions"]
    assert month0.affected_urls == [p["page"] for p in ranked[:10]]
    assert month1.affected_urls == [p["page"] for p in ranked[10:20]]
    assert not set(month0.affected_urls) & set(month1.affected_urls)  # zero overlap


def test_wasted_impressions_carries_each_pages_real_metrics_in_url_details():
    ranked = _ranked(8)
    [task] = [
        t for t in generate_content_plan(
            dt.date(2026, 8, 15), 1, content_pieces_per_month=0, pages_to_optimize_per_month=8,
            ranked_wasted_pages=ranked,
        )
        if t.category == "wasted_impressions"
    ]
    for p in ranked:
        detail = task.url_details[p["page"]]
        assert detail["impressions"] == p["impressions"]
        assert detail["clicks"] == p["clicks"]
        assert detail["ctr"] == p["ctr"]
        assert detail["position"] == p["position"]


def test_wasted_impressions_falls_back_to_generic_placeholder_with_no_ranked_pages():
    """No GSC connection/sync yet, or no "high_impression_wasted" benchmark
    configured -- ranked_wasted_pages is None, not an empty list. Generic
    "go find them yourself" placeholder, not a fabricated page list."""
    tasks = generate_content_plan(
        dt.date(2026, 8, 15), 1, content_pieces_per_month=0, pages_to_optimize_per_month=8,
        ranked_wasted_pages=None,
    )
    [task] = [t for t in tasks if t.category == "wasted_impressions"]
    assert task.affected_urls == []


def test_wasted_impressions_and_page_optimization_rank_independently():
    """Two distinct ranked lists (different filter criteria upstream in
    services.py) -- page_optimization's ranking shouldn't leak into
    wasted_impressions' picks or vice versa. Deliberately disjoint URL sets so a
    cross-wiring bug (e.g. wasted_impressions accidentally reading ranked_pages)
    would actually fail this rather than coincidentally passing."""
    page_ranked = [{"page": f"https://x.com/opt{i}", "impressions": 100_000 - i, "clicks": 10, "ctr": 0.01, "position": 5.0} for i in range(10)]
    wasted_ranked = [{"page": f"https://x.com/waste{i}", "impressions": 5_000 - i, "clicks": 1, "ctr": 0.001, "position": 5.0} for i in range(10)]
    tasks = generate_content_plan(
        dt.date(2026, 8, 15), 1, content_pieces_per_month=0, pages_to_optimize_per_month=8,
        ranked_pages=page_ranked, ranked_wasted_pages=wasted_ranked,
    )
    [page_task] = [t for t in tasks if t.category == "page_optimization"]
    [wasted_task] = [t for t in tasks if t.category == "wasted_impressions"]
    assert page_task.affected_urls == [p["page"] for p in page_ranked[:10]]
    assert wasted_task.affected_urls == [p["page"] for p in wasted_ranked[:10]]
