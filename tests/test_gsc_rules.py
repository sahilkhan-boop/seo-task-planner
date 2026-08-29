from app.rules.crawl_rules import CAMPAIGN_TASK_BUDGET
from app.rules.gsc_rules import generate_gsc_tasks, is_branded_query, position_bucket

PAGE_BENCHMARK = {"pos_1_5": 0.18}


def test_position_bucket_boundaries():
    assert position_bucket(1) == "pos_1_5"
    assert position_bucket(5) == "pos_1_5"
    assert position_bucket(5.1) == "pos_5_15"
    assert position_bucket(15) == "pos_5_15"
    assert position_bucket(15.1) is None


def test_is_branded_query_matches_substring_case_insensitively():
    assert is_branded_query("acme pricing", ["acme"])
    assert is_branded_query("ACME Pricing", ["acme"])
    assert not is_branded_query("crm pricing", ["acme"])


def test_is_branded_query_with_no_brand_terms_is_never_branded():
    assert not is_branded_query("acme pricing", [])


def test_is_branded_query_regex_takes_priority_over_brand_terms():
    import re
    regex = re.compile(r"(?i)\bacme[a-z]*\b")
    # matches a variant brand_terms' plain substring list wouldn't catch
    assert is_branded_query("acmecorp pricing", [], regex)
    # brand_terms says "widgetco" is branded, but the regex is set and doesn't match --
    # regex wins, this is NOT branded
    assert not is_branded_query("widgetco pricing", ["widgetco"], regex)


def test_is_branded_query_invalid_regex_falls_back_to_brand_terms():
    from app.rules.gsc_rules import _compile_brand_regex
    assert _compile_brand_regex("(unclosed[") is None  # invalid pattern -> no regex compiled
    assert is_branded_query("acme pricing", ["acme"], _compile_brand_regex("(unclosed["))


def test_low_impressions_are_ignored_as_noise():
    rows = [{"page": "https://x.com/a", "clicks": 0, "impressions": 5, "ctr": 0.0, "position": 2}]
    tasks = generate_gsc_tasks(rows, [], PAGE_BENCHMARK)
    assert tasks == []


# ---------- Tier 1: meta_tag_reoptimization (position 1-5, CTR below benchmark) ----------


def test_tier1_ctr_above_benchmark_generates_no_task():
    rows = [{"page": "https://x.com/a", "clicks": 100, "impressions": 400, "ctr": 0.25, "position": 2}]
    tasks = generate_gsc_tasks(rows, [], PAGE_BENCHMARK)
    assert tasks == []


def test_tier1_ctr_below_benchmark_generates_meta_reopt_task():
    rows = [{"page": "https://x.com/a", "clicks": 10, "impressions": 500, "ctr": 0.05, "position": 2.0}]
    tasks = generate_gsc_tasks(rows, [], PAGE_BENCHMARK)
    assert len(tasks) == 1
    task = tasks[0]
    assert task.source == "gsc"
    assert task.category == "meta_tag_reoptimization"
    assert task.effort_tier == "low"
    assert task.affected_urls == ["https://x.com/a"]
    assert task.metric_actual == 0.05
    assert task.metric_benchmark == 0.18
    assert task.severity == "high"  # impressions >= 500


def test_tier1_severity_scales_with_impressions():
    rows = [
        {"page": "https://x.com/a", "clicks": 1, "impressions": 600, "ctr": 0.01, "position": 2},
        {"page": "https://x.com/b", "clicks": 1, "impressions": 150, "ctr": 0.01, "position": 2},
        {"page": "https://x.com/c", "clicks": 1, "impressions": 30, "ctr": 0.01, "position": 2},
    ]
    tasks = generate_gsc_tasks(rows, [], PAGE_BENCHMARK)
    severities = {t.affected_urls[0]: t.severity for t in tasks}
    assert severities["https://x.com/a"] == "high"
    assert severities["https://x.com/b"] == "medium"
    assert severities["https://x.com/c"] == "low"


def test_tier1_missing_benchmark_segment_is_skipped():
    rows = [{"page": "https://x.com/a", "clicks": 0, "impressions": 1000, "ctr": 0.001, "position": 2}]
    tasks = generate_gsc_tasks(rows, [], {})  # no pos_1_5 configured
    assert tasks == []


# ---------- Tier 1 branded/non-branded segmentation ----------
# Branded and non-branded searches have very different realistic CTR expectations
# even at the same position -- a page whose pos_1_5 impressions are dominated by
# one or the other gets checked against that segment specifically, once configured.


def test_tier1_uses_non_branded_segment_when_page_is_majority_non_branded():
    rows = [{"page": "https://x.com/a", "clicks": 10, "impressions": 500, "ctr": 0.05, "position": 2.0}]
    queries = [
        {"page": "https://x.com/a", "query": "widget guide", "impressions": 400, "clicks": 8, "ctr": 0.02, "position": 2},
        {"page": "https://x.com/a", "query": "acme widget guide", "impressions": 100, "clicks": 2, "ctr": 0.02, "position": 2},
    ]
    benchmarks = {"pos_1_5": 0.18, "branded_pos_1_5": 0.40, "non_branded_pos_1_5": 0.08}
    [task] = generate_gsc_tasks(rows, queries, benchmarks, brand_terms=["acme"])
    assert task.metric_benchmark == 0.08  # non_branded_pos_1_5, not the flat pos_1_5 or branded segment


def test_tier1_uses_branded_segment_when_page_is_majority_branded():
    rows = [{"page": "https://x.com/a", "clicks": 10, "impressions": 500, "ctr": 0.05, "position": 2.0}]
    queries = [
        {"page": "https://x.com/a", "query": "acme widget guide", "impressions": 400, "clicks": 8, "ctr": 0.02, "position": 2},
        {"page": "https://x.com/a", "query": "widget guide", "impressions": 100, "clicks": 2, "ctr": 0.02, "position": 2},
    ]
    benchmarks = {"pos_1_5": 0.18, "branded_pos_1_5": 0.40, "non_branded_pos_1_5": 0.08}
    [task] = generate_gsc_tasks(rows, queries, benchmarks, brand_terms=["acme"])
    assert task.metric_benchmark == 0.40  # branded_pos_1_5


def test_tier1_falls_back_to_flat_segment_when_split_not_configured():
    """A site that hasn't opted into the branded/non-branded split (only the plain
    pos_1_5 benchmark configured) keeps working exactly as before."""
    rows = [{"page": "https://x.com/a", "clicks": 10, "impressions": 500, "ctr": 0.05, "position": 2.0}]
    queries = [
        {"page": "https://x.com/a", "query": "acme widget guide", "impressions": 400, "clicks": 8, "ctr": 0.02, "position": 2},
    ]
    [task] = generate_gsc_tasks(rows, queries, PAGE_BENCHMARK, brand_terms=["acme"])  # only "pos_1_5" configured
    assert task.metric_benchmark == 0.18


def test_tier1_falls_back_to_flat_segment_when_page_has_no_query_data():
    rows = [{"page": "https://x.com/a", "clicks": 10, "impressions": 500, "ctr": 0.05, "position": 2.0}]
    benchmarks = {"pos_1_5": 0.18, "branded_pos_1_5": 0.40, "non_branded_pos_1_5": 0.08}
    [task] = generate_gsc_tasks(rows, [], benchmarks, brand_terms=["acme"])  # no query_rows at all for this page
    assert task.metric_benchmark == 0.18


def test_tier1_branded_segmentation_respects_brand_regex():
    rows = [{"page": "https://x.com/a", "clicks": 10, "impressions": 500, "ctr": 0.05, "position": 2.0}]
    queries = [
        {"page": "https://x.com/a", "query": "acmecorp widget guide", "impressions": 400, "clicks": 8, "ctr": 0.02, "position": 2},
        {"page": "https://x.com/a", "query": "widget guide", "impressions": 100, "clicks": 2, "ctr": 0.02, "position": 2},
    ]
    benchmarks = {"pos_1_5": 0.18, "branded_pos_1_5": 0.40, "non_branded_pos_1_5": 0.08}
    # brand_terms (a completely different term here) wouldn't catch "acmecorp" at all,
    # but the regex does -- confirms the regex, not brand_terms, drove the classification
    [task] = generate_gsc_tasks(
        rows, queries, benchmarks, brand_terms=["widgets inc"], brand_regex=r"(?i)\bacmecorp\b"
    )
    assert task.metric_benchmark == 0.40  # branded_pos_1_5, thanks to the regex match


# ---------- Tier 2: content_expansion (position 5-15, non-branded query gap) ----------


def test_tier2_non_branded_impressions_above_threshold_generates_task():
    rows = [{"page": "https://x.com/a", "clicks": 20, "impressions": 300, "ctr": 0.07, "position": 8.0}]
    queries = [
        {"page": "https://x.com/a", "query": "best running shoes", "clicks": 5, "impressions": 80, "ctr": 0.06, "position": 8},
        {"page": "https://x.com/a", "query": "running shoe guide", "clicks": 3, "impressions": 60, "ctr": 0.05, "position": 9},
        {"page": "https://x.com/a", "query": "acme running shoes", "clicks": 10, "impressions": 40, "ctr": 0.25, "position": 3},
    ]
    tasks = generate_gsc_tasks(rows, queries, PAGE_BENCHMARK, brand_terms=["acme"])
    assert len(tasks) == 1
    task = tasks[0]
    assert task.category == "content_expansion"
    assert task.effort_tier == "medium"
    assert task.affected_urls == ["https://x.com/a"]
    assert task.metric_actual == 140  # 80 + 60, excludes the branded "acme running shoes" row
    assert "best running shoes" in task.description
    assert "acme running shoes" not in task.description


def test_tier2_below_threshold_generates_no_task():
    rows = [{"page": "https://x.com/a", "clicks": 5, "impressions": 100, "ctr": 0.05, "position": 8.0}]
    queries = [
        {"page": "https://x.com/a", "query": "running shoes", "clicks": 2, "impressions": 50, "ctr": 0.04, "position": 8},
    ]
    tasks = generate_gsc_tasks(rows, queries, PAGE_BENCHMARK)
    assert tasks == []


def test_tier2_caps_number_of_queries_listed():
    rows = [{"page": "https://x.com/a", "clicks": 20, "impressions": 500, "ctr": 0.04, "position": 8.0}]
    queries = [
        {"page": "https://x.com/a", "query": f"query {i}", "clicks": 1, "impressions": 30, "ctr": 0.03, "position": 8}
        for i in range(10)
    ]
    tasks = generate_gsc_tasks(rows, queries, PAGE_BENCHMARK)
    assert len(tasks) == 1
    # 5 queries named "query N (30 impr.)" -- capped, even though 10 queries qualify
    assert tasks[0].description.count("impr.)") == 5


def test_tier2_a_page_with_only_branded_queries_generates_no_task():
    rows = [{"page": "https://x.com/a", "clicks": 20, "impressions": 300, "ctr": 0.07, "position": 8.0}]
    queries = [
        {"page": "https://x.com/a", "query": "acme shoes", "clicks": 20, "impressions": 300, "ctr": 0.07, "position": 8},
    ]
    tasks = generate_gsc_tasks(rows, queries, PAGE_BENCHMARK, brand_terms=["acme"])
    assert tasks == []


# ---------- Tier 3: ctr_optimization (beyond position 15, gradual catch-all) ----------


def test_tier3_beyond_position_15_above_threshold_generates_task():
    rows = [{"page": "https://x.com/a", "clicks": 5, "impressions": 400, "ctr": 0.01, "position": 22.0}]
    tasks = generate_gsc_tasks(rows, [], PAGE_BENCHMARK)
    assert len(tasks) == 1
    task = tasks[0]
    assert task.category == "ctr_optimization"
    assert task.effort_tier == "medium"
    assert task.metric_actual == 400
    assert task.metric_benchmark == 300


def test_tier3_below_threshold_generates_no_task():
    rows = [{"page": "https://x.com/a", "clicks": 0, "impressions": 250, "ctr": 0.001, "position": 45}]
    tasks = generate_gsc_tasks(rows, [], PAGE_BENCHMARK)
    assert tasks == []


# wasted_impressions used to be a Tier 0 live-sync check here (real visibility,
# essentially no clicks) -- it's since moved to services.py's
# _ranked_wasted_impression_pages + content_rules.py's monthly content-plan cycle
# (see gsc_rules.py's module docstring). Its tests moved with it: see
# test_content_plan_service.py's "wasted_impressions ranked by real GSC data"
# section and test_content_rules.py's "wasted_impressions" section.


def test_a_page_only_ever_falls_into_one_tier():
    # position 3 (tier 1) but also has non-branded query data -- must not double-count into tier 2.
    rows = [{"page": "https://x.com/a", "clicks": 10, "impressions": 500, "ctr": 0.05, "position": 3.0}]
    queries = [
        {"page": "https://x.com/a", "query": "widgets", "clicks": 10, "impressions": 500, "ctr": 0.05, "position": 3},
    ]
    tasks = generate_gsc_tasks(rows, queries, PAGE_BENCHMARK)
    assert len(tasks) == 1
    assert tasks[0].category == "meta_tag_reoptimization"


# ---------- campaign-wide batching ----------


def test_meta_tag_reoptimization_collapses_to_one_task_once_real_count_exceeds_budget():
    budget = CAMPAIGN_TASK_BUDGET["meta_tag_reoptimization"]
    rows = [
        {"page": f"https://x.com/{i}", "clicks": 1, "impressions": 200, "ctr": 0.01, "position": 2.0}
        for i in range(budget * 4)
    ]
    tasks = generate_gsc_tasks(rows, [], PAGE_BENCHMARK)
    # one consolidated task, not several look-alike ones landing on different days
    assert len(tasks) == 1
    assert tasks[0].category == "meta_tag_reoptimization"
    assert set(tasks[0].affected_urls) == {r["page"] for r in rows}


def test_ctr_optimization_collapses_independently_of_meta_tag_volume():
    # both tiers well over their own thresholds at once -- each collapses to its own single task
    meta_budget = CAMPAIGN_TASK_BUDGET["meta_tag_reoptimization"]
    ctr_budget = CAMPAIGN_TASK_BUDGET["ctr_optimization"]
    rows = [
        {"page": f"https://x.com/meta{i}", "clicks": 1, "impressions": 200, "ctr": 0.01, "position": 2.0}
        for i in range(meta_budget * 2)
    ] + [
        {"page": f"https://x.com/ctr{i}", "clicks": 1, "impressions": 400, "ctr": 0.01, "position": 30.0}
        for i in range(ctr_budget * 2)
    ]
    tasks = generate_gsc_tasks(rows, [], PAGE_BENCHMARK)
    meta_tasks = [t for t in tasks if t.category == "meta_tag_reoptimization"]
    ctr_tasks = [t for t in tasks if t.category == "ctr_optimization"]
    assert len(meta_tasks) == 1
    assert len(ctr_tasks) == 1


def test_batched_meta_tag_task_keeps_each_pages_own_ctr_in_url_details():
    """Regression: a batched task's per-page CTR used to be discarded entirely --
    metric_actual is only ever set on unbatched tasks, so every row of a batch's
    export showed a blank actual-CTR column. url_details must carry each page's own
    FULL native row (clicks/impressions/ctr/position -- see _native_gsc_row)
    forward, and metric_benchmark should carry the (page-1-wide, constant) target
    so the export's Benchmark column isn't blank either."""
    budget = CAMPAIGN_TASK_BUDGET["meta_tag_reoptimization"]
    # ctr stays well under the 0.18 benchmark for every row (0.001-0.050) so all of
    # them qualify as candidates and collapse into one batch, while still varying
    # enough per-page to prove each page's own number survives into url_details.
    rows = [
        {"page": f"https://x.com/{i}", "clicks": 1, "impressions": 200, "ctr": 0.001 * (i + 1), "position": 2.0}
        for i in range(budget * 2)
    ]
    [task] = generate_gsc_tasks(rows, [], PAGE_BENCHMARK)
    assert task.metric_benchmark == PAGE_BENCHMARK["pos_1_5"]
    for row in rows:
        detail = task.url_details[row["page"]]
        assert detail["ctr"] == row["ctr"]
        assert detail["clicks"] == row["clicks"]
        assert detail["impressions"] == row["impressions"]
        assert detail["position"] == row["position"]


def test_batched_task_severity_is_high_if_any_page_in_the_batch_is_high():
    budget = CAMPAIGN_TASK_BUDGET["meta_tag_reoptimization"]
    rows = [
        {"page": f"https://x.com/{i}", "clicks": 1, "impressions": 600, "ctr": 0.01, "position": 2.0}  # high severity
        for i in range(budget * 3)
    ]
    tasks = generate_gsc_tasks(rows, [], PAGE_BENCHMARK)
    assert all(t.severity == "high" for t in tasks)


def test_content_expansion_cross_references_the_prompt_analysis_findings():
    rows = [{"page": "https://x.com/a", "clicks": 20, "impressions": 300, "ctr": 0.07, "position": 8.0}]
    queries = [
        {"page": "https://x.com/a", "query": "widget guide", "clicks": 5, "impressions": 200, "ctr": 0.06, "position": 8},
    ]
    [task] = generate_gsc_tasks(rows, queries, PAGE_BENCHMARK)
    assert task.category == "content_expansion"
    assert "Prompt Analysis & Keyword Research" in task.description


def test_batched_content_expansion_also_cross_references_the_prompt_analysis_findings():
    budget = CAMPAIGN_TASK_BUDGET["content_expansion"]
    rows = [
        {"page": f"https://x.com/{i}", "clicks": 20, "impressions": 300, "ctr": 0.07, "position": 8.0}
        for i in range(budget * 2)
    ]
    queries = [
        {"page": f"https://x.com/{i}", "query": "widget guide", "clicks": 5, "impressions": 200, "ctr": 0.06, "position": 8}
        for i in range(budget * 2)
    ]
    tasks = generate_gsc_tasks(rows, queries, PAGE_BENCHMARK)
    assert all("Prompt Analysis & Keyword Research" in t.description for t in tasks)
