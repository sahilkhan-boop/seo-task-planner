from app.rules.ga4_rules import generate_ga4_tasks

# sessions >= 500 AND active_users >= 100 is the ui_ux_review gate (HIGH_TRAFFIC_SESSIONS,
# MIN_ACTIVE_USERS_FOR_UX_REVIEW) -- "highly visited... with good users", not just above
# the base MIN_SESSIONS noise floor used by the other three checks.
HIGH_TRAFFIC_ROW = {"sessions": 600, "active_users": 400, "engagement_rate": 0.1, "bounce_rate": 0.9, "key_events": 0}


def test_low_sessions_are_ignored_as_noise():
    rows = [{"page": "/a", "sessions": 5, "active_users": 5, "engagement_rate": 0.1, "bounce_rate": 0.9, "key_events": 0}]
    tasks = generate_ga4_tasks(rows, {}, {"engagement_rate": 0.55})
    assert tasks == []


# ---------- ui_ux_review (engagement, gated on high traffic + real users) ----------


def test_engagement_above_benchmark_generates_no_task():
    rows = [{"page": "/a", **{**HIGH_TRAFFIC_ROW, "engagement_rate": 0.7}}]
    tasks = generate_ga4_tasks(rows, {}, {"engagement_rate": 0.55})
    assert tasks == []


def test_high_traffic_low_engagement_generates_ui_ux_review_task():
    rows = [{"page": "/a", **HIGH_TRAFFIC_ROW}]
    tasks = generate_ga4_tasks(rows, {}, {"engagement_rate": 0.55})
    assert len(tasks) == 1
    task = tasks[0]
    assert task.source == "ga4"
    assert task.category == "ui_ux_review"
    assert task.affected_urls == ["/a"]
    assert task.metric_actual == 0.1
    assert task.metric_benchmark == 0.55
    assert task.severity == "high"  # sessions >= 500
    assert "ui/ux" in task.description.lower() or "page speed" in task.description.lower()


def test_low_engagement_but_below_traffic_threshold_generates_no_ui_ux_task():
    # sessions only 200 (above MIN_SESSIONS noise floor, but below the 500 high-traffic gate)
    rows = [{"page": "/a", "sessions": 200, "active_users": 400, "engagement_rate": 0.1, "bounce_rate": 0.3, "key_events": 5}]
    tasks = generate_ga4_tasks(rows, {}, {"engagement_rate": 0.55})
    assert tasks == []


def test_low_engagement_but_few_active_users_generates_no_ui_ux_task():
    # high sessions but suspiciously few distinct users -- not a real "good users" signal
    rows = [{"page": "/a", "sessions": 600, "active_users": 10, "engagement_rate": 0.1, "bounce_rate": 0.3, "key_events": 5}]
    tasks = generate_ga4_tasks(rows, {}, {"engagement_rate": 0.55})
    assert tasks == []


# ---------- exit_rate (same significance gate as ui_ux_review -- see below) ----------


def test_exit_rate_above_benchmark_generates_task():
    rows = [{"page": "/a", **{**HIGH_TRAFFIC_ROW, "bounce_rate": 0.8}}]
    tasks = generate_ga4_tasks(rows, {}, {"exit_rate": 0.60})
    assert len(tasks) == 1
    assert tasks[0].category == "high_exit_rate"
    assert tasks[0].metric_actual == 0.8
    assert tasks[0].severity == "high"  # sessions >= 500


def test_exit_rate_below_benchmark_generates_no_task():
    rows = [{"page": "/a", **{**HIGH_TRAFFIC_ROW, "bounce_rate": 0.3}}]
    tasks = generate_ga4_tasks(rows, {}, {"exit_rate": 0.60})
    assert tasks == []


def test_high_exit_rate_below_traffic_threshold_generates_no_task():
    """Regression: bounceRate is GA4's own defined complement of engagementRate
    (bounceRate = 1 - engagementRate, not an independent metric) -- this needs the
    exact same "is this even worth caring about" significance gate as ui_ux_review
    (real traffic AND real users), not just the general MIN_SESSIONS noise floor a
    page could clear at just 20 sessions."""
    rows = [{"page": "/a", "sessions": 200, "active_users": 150, "engagement_rate": 0.7, "bounce_rate": 0.8, "key_events": 5}]
    tasks = generate_ga4_tasks(rows, {}, {"exit_rate": 0.60})
    assert tasks == []


# ---------- mobile_share ----------


def test_low_mobile_share_generates_task():
    rows = [{"page": "/a", "sessions": 200, "active_users": 150, "engagement_rate": 0.7, "bounce_rate": 0.3, "key_events": 5}]
    tasks = generate_ga4_tasks(rows, {"/a": 0.1}, {"mobile_share": 0.35})
    assert len(tasks) == 1
    assert tasks[0].category == "low_mobile_share"
    assert tasks[0].metric_actual == 0.1
    assert tasks[0].metric_benchmark == 0.35


def test_missing_mobile_share_data_is_skipped_not_flagged():
    rows = [{"page": "/a", "sessions": 200, "active_users": 150, "engagement_rate": 0.7, "bounce_rate": 0.3, "key_events": 5}]
    tasks = generate_ga4_tasks(rows, {}, {"mobile_share": 0.35})  # no mobile-share data for /a
    assert tasks == []


# ---------- key_events ----------


def test_low_key_event_rate_generates_task():
    rows = [{"page": "/a", "sessions": 1000, "active_users": 800, "engagement_rate": 0.7, "bounce_rate": 0.3, "key_events": 2}]
    tasks = generate_ga4_tasks(rows, {}, {"key_events": 0.01})  # 2/1000 = 0.002 < 0.01
    assert len(tasks) == 1
    assert tasks[0].category == "low_key_events"
    assert round(tasks[0].metric_actual, 3) == 0.002


# ---------- combinations ----------


def test_a_page_can_trigger_multiple_rules_at_once():
    rows = [{"page": "/a", **HIGH_TRAFFIC_ROW}]
    benchmarks = {"engagement_rate": 0.55, "exit_rate": 0.60, "mobile_share": 0.35, "key_events": 0.01}
    tasks = generate_ga4_tasks(rows, {"/a": 0.1}, benchmarks)
    categories = {t.category for t in tasks}
    assert categories == {"ui_ux_review", "high_exit_rate", "low_mobile_share", "low_key_events"}


def test_no_configured_benchmark_means_that_metric_is_not_checked():
    rows = [{"page": "/a", **HIGH_TRAFFIC_ROW}]
    tasks = generate_ga4_tasks(rows, {"/a": 0.1}, {})  # no benchmarks configured at all
    assert tasks == []


def test_severity_scales_with_sessions():
    rows = [
        {"page": "/a", "sessions": 600, "active_users": 500, "engagement_rate": 0.7, "bounce_rate": 0.3, "key_events": 0},
        {"page": "/b", "sessions": 150, "active_users": 120, "engagement_rate": 0.7, "bounce_rate": 0.3, "key_events": 0},
        {"page": "/c", "sessions": 30, "active_users": 20, "engagement_rate": 0.7, "bounce_rate": 0.3, "key_events": 0},
    ]
    # key_events only gates on the base MIN_SESSIONS noise floor (20), not a high-traffic
    # significance gate like ui_ux_review/high_exit_rate do -- so severity-by-sessions is
    # visible across all three rows here.
    tasks = generate_ga4_tasks(rows, {}, {"key_events": 0.01})
    severities = {t.affected_urls[0]: t.severity for t in tasks}
    assert severities["/a"] == "high"
    assert severities["/b"] == "medium"
    assert severities["/c"] == "low"


# ---------- systemic collapse (high_exit_rate / low_mobile_share / low_key_events only) ----------


def _make_rows(n, **overrides):
    base = {"sessions": 100, "active_users": 80, "engagement_rate": 0.7, "bounce_rate": 0.2, "key_events": 5}
    base.update(overrides)
    return [{"page": f"/p{i}", **base} for i in range(n)]


def test_check_failing_on_most_of_a_large_site_collapses_to_one_task():
    rows = _make_rows(20, key_events=0)  # 20 pages, 0 key events each -- 100% failure rate
    tasks = generate_ga4_tasks(rows, {}, {"key_events": 0.01})
    assert len(tasks) == 1
    task = tasks[0]
    assert task.category == "low_key_events"
    assert task.severity == "high"
    assert "20 of 20" in task.title
    assert len(task.affected_urls) == 20  # exactly SYSTEMIC_SAMPLE_SIZE, so no truncation note


def test_collapsed_task_keeps_the_full_url_list_not_just_a_sample():
    # affected_urls keeps every page (exportable), even though the description just
    # explains the systemic finding rather than listing pages inline (see
    # _collapse_if_systemic's docstring).
    rows = _make_rows(30, key_events=0)
    tasks = generate_ga4_tasks(rows, {}, {"key_events": 0.01})
    assert len(tasks) == 1
    assert len(tasks[0].affected_urls) == 30
    assert "30 of 30" in tasks[0].description


def test_systemic_collapse_keeps_each_pages_own_rate_in_url_details():
    """Regression: the systemic-collapse task used to only keep affected_urls, dropping
    each page's own key_event_rate entirely -- every row of its export showed a blank
    actual-value column. url_details must carry each page's own FULL native row
    (sessions/active_users/engagement_rate/bounce_rate -- see _native_ga4_row)
    forward, and metric_benchmark should carry the (site-wide, constant) benchmark
    so the export's Benchmark column isn't blank either."""
    rows = [{**_make_rows(1, key_events=0)[0], "sessions": 100 + i, "key_events": i} for i in range(25)]
    for r, i in zip(rows, range(25)):
        r["page"] = f"/p{i}"
    tasks = generate_ga4_tasks(rows, {}, {"key_events": 0.5})
    assert len(tasks) == 1
    task = tasks[0]
    assert task.metric_benchmark == 0.5
    for row in rows:
        detail = task.url_details[row["page"]]
        assert detail["sessions"] == row["sessions"]
        assert detail["active_users"] == row["active_users"]
        assert detail["engagement_rate"] == row["engagement_rate"]
        assert detail["bounce_rate"] == row["bounce_rate"]


def test_check_failing_on_a_minority_of_a_large_site_stays_per_page():
    failing = _make_rows(5, key_events=0)
    passing = _make_rows(20, key_events=50)  # 50/100 = 0.5 >= 0.01 benchmark, passes
    passing = [{**r, "page": f"/pass{i}"} for i, r in enumerate(passing)]
    tasks = generate_ga4_tasks(failing + passing, {}, {"key_events": 0.01})
    assert len(tasks) == 5  # below the 30% systemic threshold (5 of 25 evaluated = 20%)
    assert all(t.category == "low_key_events" for t in tasks)


def test_majority_failure_on_a_small_site_does_not_systemic_collapse_but_still_batches():
    # 10 evaluated pages is below MIN_EVALUATED_PAGES_FOR_SYSTEMIC_CHECK, even at 100% failure --
    # so the systemic-collapse rule doesn't fire. But 10 still exceeds the low_key_events campaign
    # threshold of 8, so the general collapse-to-one-task rule applies instead.
    rows = _make_rows(10, key_events=0)
    tasks = generate_ga4_tasks(rows, {}, {"key_events": 0.01})
    # one consolidated task, not several look-alike ones landing on different days
    assert len(tasks) == 1
    assert tasks[0].category == "low_key_events"
    assert len(tasks[0].affected_urls) == 10  # no page dropped, just grouped into one task


def test_ui_ux_review_collapses_to_one_task_at_high_volume():
    rows = _make_rows(25, sessions=600, active_users=400, engagement_rate=0.1)
    tasks = generate_ga4_tasks(rows, {}, {"engagement_rate": 0.55})
    assert len(tasks) == 1
    assert tasks[0].category == "ui_ux_review"
    assert len(tasks[0].affected_urls) == 25  # every page still accounted for


def test_batched_ui_ux_review_keeps_each_pages_own_engagement_rate_in_url_details():
    """Regression: ui_ux_review's batch collapse (no systemic concept, always goes
    through _batch_generated_tasks directly) had the same per-page-value-discarded bug
    as the housekeeping checks' systemic collapse -- url_details must carry each page's
    FULL native row forward, keyed by its own url, not just the first page's."""
    rows = [
        {**_make_rows(1, sessions=600, active_users=400)[0], "page": f"/p{i}", "engagement_rate": 0.1 + i * 0.01}
        for i in range(25)
    ]
    [task] = generate_ga4_tasks(rows, {}, {"engagement_rate": 0.55})
    assert task.category == "ui_ux_review"
    assert task.metric_benchmark == 0.55
    for row in rows:
        detail = task.url_details[row["page"]]
        assert detail["engagement_rate"] == row["engagement_rate"]
        assert detail["sessions"] == row["sessions"]
        assert detail["active_users"] == row["active_users"]
        assert detail["bounce_rate"] == row["bounce_rate"]


def test_ui_ux_review_stays_one_task_per_page_below_budget():
    rows = _make_rows(5, sessions=600, active_users=400, engagement_rate=0.1)
    tasks = generate_ga4_tasks(rows, {}, {"engagement_rate": 0.55})
    assert len(tasks) == 5
    assert all(len(t.affected_urls) == 1 for t in tasks)


def test_each_housekeeping_category_collapses_independently():
    # key_events fails site-wide (collapses); exit_rate only fails on one page (stays per-page)
    rows = _make_rows(25, key_events=0)
    rows[0]["bounce_rate"] = 0.9
    rows[0]["sessions"] = 600  # clear high_exit_rate's traffic significance gate (see its own tests)
    rows[0]["active_users"] = 400
    tasks = generate_ga4_tasks(rows, {}, {"key_events": 0.01, "exit_rate": 0.60})
    key_event_tasks = [t for t in tasks if t.category == "low_key_events"]
    exit_tasks = [t for t in tasks if t.category == "high_exit_rate"]
    assert len(key_event_tasks) == 1  # collapsed
    assert len(exit_tasks) == 1  # 1 of 25 = 4%, stays per-page (still just 1 task, but not collapsed-labeled)
    assert "of 25 pages" not in exit_tasks[0].title
