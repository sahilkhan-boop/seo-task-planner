from app.ingestion.screaming_frog import CrawlIssueRow
from app.rules.crawl_rules import (
    CAMPAIGN_TASK_BUDGET,
    GeneratedTask,
    batch_items,
    consolidate_technical_tasks,
    generate_crawl_tasks,
    generate_indexation_blocking_tasks,
)


def test_404_with_inlinks_is_high_severity_and_lists_them():
    issue = CrawlIssueRow(
        issue_type="404",
        url="https://example.com/old-guide",
        status_code=404,
        inlinking_urls=["https://example.com/a", "https://example.com/b", "https://example.com/c",
                          "https://example.com/d", "https://example.com/e"],
    )
    [task] = generate_crawl_tasks([issue])
    assert task.category == "404_fix"
    assert task.severity == "high"  # >= HIGH_INLINK_THRESHOLD (5) inlinks
    # inlinks are tracked (and exportable) via affected_urls, not printed in the
    # description -- see crawl_rules.py's module docstring on why.
    assert "https://example.com/a" in task.affected_urls
    assert issue.url in task.affected_urls
    assert "export" in task.description.lower()


def test_404_with_no_inlinks_is_medium_and_says_check_sitemap():
    issue = CrawlIssueRow(issue_type="404", url="https://example.com/orphan", status_code=404)
    [task] = generate_crawl_tasks([issue])
    assert task.severity == "medium"
    assert "sitemap" in task.description.lower()


def test_redirect_task_names_old_and_new_url_for_inlink_updates():
    issue = CrawlIssueRow(
        issue_type="301",
        url="https://example.com/old-product",
        status_code=301,
        redirects_to="https://example.com/new-product",
        inlinking_urls=["https://example.com/home", "https://example.com/category"],
    )
    [task] = generate_crawl_tasks([issue])
    assert task.category == "redirect_inlink_update"
    assert "https://example.com/old-product" in task.title
    assert "https://example.com/new-product" in task.description
    # inlinks are tracked (and exportable) via affected_urls, not printed in the description
    assert "https://example.com/home" in task.affected_urls


def test_5xx_is_always_high_severity():
    issue = CrawlIssueRow(issue_type="5xx", url="https://example.com/broken-api", status_code=500)
    [task] = generate_crawl_tasks([issue])
    assert task.category == "server_error"
    assert task.severity == "high"


def test_non_issue_status_codes_are_ignored():
    issue = CrawlIssueRow(issue_type="200", url="https://example.com/", status_code=200)
    assert generate_crawl_tasks([issue]) == []


# ---------- batch_items ----------


def test_batch_items_returns_singleton_groups_below_budget():
    assert batch_items([1, 2, 3], budget=15) == [[1], [2], [3]]


def test_batch_items_collapses_to_a_single_batch_above_budget():
    # above budget, everything collapses into ONE batch -- not several similarly-sized
    # ones that would each become their own task and read as the same task repeating.
    items = list(range(10))
    batches = batch_items(items, budget=3)
    assert len(batches) == 1
    assert batches[0] == items  # order preserved, nothing dropped


def test_batch_items_returns_empty_for_no_items():
    assert batch_items([], budget=15) == []


# ---------- campaign-wide batching for 404_fix / redirect_inlink_update ----------


def _issue(n, issue_type="404", inlinks=0):
    return CrawlIssueRow(
        issue_type=issue_type,
        url=f"https://example.com/page-{n}",
        status_code=404 if issue_type == "404" else 301,
        redirects_to="https://example.com/new" if issue_type != "404" else None,
        inlinking_urls=[f"https://example.com/inlink-{n}-{i}" for i in range(inlinks)],
    )


def test_404_stays_one_task_per_issue_below_budget():
    budget = CAMPAIGN_TASK_BUDGET["404_fix"]
    issues = [_issue(i) for i in range(budget)]  # exactly at budget, no batching needed
    tasks = generate_crawl_tasks(issues)
    assert len(tasks) == budget
    assert all(len(t.affected_urls) == 1 for t in tasks)


def test_404_collapses_to_one_task_once_real_count_exceeds_budget():
    budget = CAMPAIGN_TASK_BUDGET["404_fix"]
    issues = [_issue(i, inlinks=2) for i in range(budget * 3)]  # 3x the budget
    tasks = generate_crawl_tasks(issues)
    # one consolidated task, not several look-alike ones landing on different days
    assert len(tasks) == 1
    assert tasks[0].category == "404_fix"
    # every original issue URL still shows up, just in one task instead of many
    assert set(tasks[0].affected_urls) == {i.url for i in issues}


def test_redirect_collapses_to_one_task_once_real_count_exceeds_budget():
    budget = CAMPAIGN_TASK_BUDGET["redirect_inlink_update"]
    issues = [_issue(i, issue_type="301", inlinks=1) for i in range(budget * 5)]
    tasks = generate_crawl_tasks(issues)
    assert len(tasks) == 1
    assert tasks[0].category == "redirect_inlink_update"


def test_server_error_is_never_batched_regardless_of_volume():
    # server_error has no entry in CAMPAIGN_TASK_BUDGET -- always one task per issue
    issues = [CrawlIssueRow(issue_type="5xx", url=f"https://example.com/api-{i}", status_code=500) for i in range(50)]
    tasks = generate_crawl_tasks(issues)
    assert len(tasks) == 50
    assert all(t.category == "server_error" for t in tasks)


def test_batched_404_task_description_explains_the_work_without_listing_urls():
    """The description's job is explaining the logic (what/why/how), not repeating
    data that's already in affected_urls and the CSV export -- a large batch used to
    balloon the description into a multi-thousand-character wall of sampled URLs,
    which is exactly the rendering-cost problem this design avoids."""
    budget = CAMPAIGN_TASK_BUDGET["404_fix"]
    issues = [_issue(i, inlinks=2) for i in range(budget * 5)]  # well past budget
    [task] = generate_crawl_tasks(issues)
    assert len(task.affected_urls) == budget * 5  # every URL still tracked, just not printed
    for url in task.affected_urls:
        assert url not in task.description
    assert "export" in task.description.lower()
    assert len(task.description) < 500  # stays a short explanation regardless of batch size


# ---------- consolidate_technical_tasks (analyst-chosen "one go" workflow) ----------


def test_consolidate_merges_every_technical_task_into_one():
    issues = [
        _issue(1, issue_type="404"),
        _issue(2, issue_type="301", inlinks=1),
        CrawlIssueRow(issue_type="5xx", url="https://example.com/api", status_code=500),
    ]
    crawl_tasks = generate_crawl_tasks(issues)
    idx_tasks = generate_indexation_blocking_tasks({"noindex": ["https://example.com/x"]})
    merged = consolidate_technical_tasks(crawl_tasks + idx_tasks)

    assert len(merged) == 1
    task = merged[0]
    assert task.source == "crawl"
    assert task.category == "technical_audit"
    assert task.title == "Technical Audit"
    assert task.severity == "high"


def test_consolidate_keeps_every_affected_url_deduped():
    issues = [_issue(1, issue_type="404", inlinks=2)]
    crawl_tasks = generate_crawl_tasks(issues)
    idx_tasks = generate_indexation_blocking_tasks({"noindex": ["https://example.com/x", issues[0].url]})
    merged = consolidate_technical_tasks(crawl_tasks + idx_tasks)

    all_source_urls = {u for t in crawl_tasks + idx_tasks for u in t.affected_urls}
    assert set(merged[0].affected_urls) == all_source_urls
    # the 404's own URL appears in both source tasks -- consolidation shouldn't duplicate it
    assert merged[0].affected_urls.count(issues[0].url) == 1


def test_consolidate_orders_sections_by_technical_priority():
    issues = [
        _issue(1, issue_type="404"),
        CrawlIssueRow(issue_type="5xx", url="https://example.com/api", status_code=500),
    ]
    crawl_tasks = generate_crawl_tasks(issues)
    idx_tasks = generate_indexation_blocking_tasks({"noindex": ["https://example.com/x"]})
    merged = consolidate_technical_tasks(crawl_tasks + idx_tasks)

    description = merged[0].description
    # indexation-blocking and server errors are worked before 404s, per the module
    # docstring -- now reflected as category-count order in the one-line summary,
    # not section order in a long concatenated description (see consolidate_technical_
    # tasks: descriptions are kept to 1-2 sentences, no per-category breakdown text).
    assert description.index("indexation-blocking") < description.index("404")
    assert description.index("server error") < description.index("404")


def test_consolidate_remembers_each_urls_original_category_and_severity():
    """Regression: consolidation used to flatten every source task's affected_urls
    into one plain list, discarding which check (404/redirect/indexation-blocking/
    server error) actually flagged each url -- every exported row read as
    category="technical_audit" with no way to tell them apart or verify against the
    crawl. url_details must carry each url's real origin forward."""
    issues = [
        _issue(1, issue_type="404"),
        CrawlIssueRow(issue_type="5xx", url="https://example.com/api", status_code=500),
    ]
    crawl_tasks = generate_crawl_tasks(issues)
    idx_tasks = generate_indexation_blocking_tasks({"noindex": ["https://example.com/x"]})
    merged = consolidate_technical_tasks(crawl_tasks + idx_tasks)

    by_category = {t.category: t for t in crawl_tasks}
    details = merged[0].url_details
    assert details[issues[0].url] == {"category": "404_fix", "severity": by_category["404_fix"].severity}
    assert details["https://example.com/api"] == {
        "category": "server_error", "severity": by_category["server_error"].severity,
    }
    assert details["https://example.com/x"] == {"category": "indexation_blocking", "severity": idx_tasks[0].severity}
    # every affected url has an entry -- nothing falls through ungrouped
    assert set(details.keys()) == set(merged[0].affected_urls)


def test_consolidate_is_a_noop_with_zero_or_one_task():
    assert consolidate_technical_tasks([]) == []
    [single] = generate_crawl_tasks([_issue(1, issue_type="404")])
    assert consolidate_technical_tasks([single]) == [single]


def test_consolidate_falls_back_to_default_severity_when_nothing_is_high():
    low_severity_task = GeneratedTask(
        source="crawl", category="redirect_inlink_update", title="x", description="d",
        affected_urls=["https://example.com/a"], severity="low",
    )
    another = GeneratedTask(
        source="crawl", category="404_fix", title="y", description="d",
        affected_urls=["https://example.com/b"], severity="medium",
    )
    merged = consolidate_technical_tasks([low_severity_task, another])
    assert merged[0].severity == "medium"
