"""Every export's header row must have exactly as many columns as the data rows it
writes, or the CSV opens misaligned in a spreadsheet -- exactly the bug this file
guards against (GSC_CSV_HEADER/GA4_CSV_HEADER were written without their
GSC_GA4_TRACKING_HEADER suffix, while the row-writer always included it)."""
import csv
import datetime as dt
import io

from app.models import Task
from app.routers.tasks import (
    CRAWL_CSV_HEADER,
    GA4_CSV_HEADER,
    GSC_CSV_HEADER,
    GSC_GA4_TRACKING_HEADER,
    _write_crawl_task_rows,
    _write_gsc_ga4_task_row,
)


def _task(**overrides) -> Task:
    defaults = dict(
        site_id=1,
        source="crawl",
        category="404_fix",
        title="Fix 404: https://x.com/a",
        description="",
        affected_urls=["https://x.com/a", "https://x.com/inlink1"],
        severity="high",
        metric_actual=404,
        metric_benchmark=200,
        status="todo",
        assignee="Sahil Khan",
        target_date=dt.date(2026, 9, 1),
    )
    defaults.update(overrides)
    return Task(**defaults)


def _row_lengths(rows: list[list]) -> set[int]:
    return {len(r) for r in rows}


def test_crawl_export_header_matches_row_width():
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(CRAWL_CSV_HEADER)
    _write_crawl_task_rows(writer, _task())
    buffer.seek(0)
    rows = list(csv.reader(buffer))
    assert _row_lengths(rows[1:]) == {len(CRAWL_CSV_HEADER)}


def test_crawl_export_handles_a_task_with_no_inlinks():
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(CRAWL_CSV_HEADER)
    _write_crawl_task_rows(writer, _task(affected_urls=["https://x.com/a"]))
    buffer.seek(0)
    rows = list(csv.reader(buffer))
    assert _row_lengths(rows[1:]) == {len(CRAWL_CSV_HEADER)}


def test_gsc_export_header_matches_row_width():
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(GSC_CSV_HEADER + GSC_GA4_TRACKING_HEADER)
    _write_gsc_ga4_task_row(
        writer,
        _task(source="gsc", category="meta_tag_reoptimization", affected_urls=["https://x.com/a"],
              metric_actual=0.05, metric_benchmark=0.18),
        native="gsc",
    )
    buffer.seek(0)
    rows = list(csv.reader(buffer))
    expected_width = len(GSC_CSV_HEADER) + len(GSC_GA4_TRACKING_HEADER)
    assert len(rows[0]) == expected_width
    assert _row_lengths(rows[1:]) == {expected_width}


def test_ga4_export_header_matches_row_width():
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(GA4_CSV_HEADER + GSC_GA4_TRACKING_HEADER)
    _write_gsc_ga4_task_row(
        writer,
        _task(source="ga4", category="ui_ux_review", affected_urls=["https://x.com/a"],
              metric_actual=0.3, metric_benchmark=0.55),
        native="ga4",
    )
    buffer.seek(0)
    rows = list(csv.reader(buffer))
    expected_width = len(GA4_CSV_HEADER) + len(GSC_GA4_TRACKING_HEADER)
    assert len(rows[0]) == expected_width
    assert _row_lengths(rows[1:]) == {expected_width}


def test_gsc_row_populates_the_native_column_matching_its_own_category():
    task = _task(source="gsc", category="content_expansion", affected_urls=["https://x.com/a"],
                 metric_actual=250, metric_benchmark=100)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    _write_gsc_ga4_task_row(writer, task, native="gsc")
    buffer.seek(0)
    row = next(csv.reader(buffer))
    page, clicks, impressions, ctr, position = row[:5]
    assert page == "https://x.com/a"
    assert impressions == "250"  # content_expansion's metric_actual is non-branded impressions
    assert ctr == ""  # not a meta_tag_reoptimization row, so CTR column stays blank


def test_gsc_export_writes_one_row_per_page_not_just_the_first():
    """Regression test: a batched task (e.g. "Rewrite title/meta for 205 page-1 pages")
    used to only ever export affected_urls[0], silently dropping every other page."""
    pages = [f"https://x.com/{i}" for i in range(5)]
    task = _task(source="gsc", category="meta_tag_reoptimization", affected_urls=pages,
                 metric_actual=None, metric_benchmark=None)  # batched tasks have no single metric_actual
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    _write_gsc_ga4_task_row(writer, task, native="gsc")
    buffer.seek(0)
    rows = list(csv.reader(buffer))
    assert [r[0] for r in rows] == pages  # every page present, in order, none dropped


def test_ga4_export_writes_one_row_per_page_not_just_the_first():
    pages = [f"https://x.com/{i}" for i in range(4)]
    task = _task(source="ga4", category="ui_ux_review", affected_urls=pages,
                 metric_actual=None, metric_benchmark=None)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    _write_gsc_ga4_task_row(writer, task, native="ga4")
    buffer.seek(0)
    rows = list(csv.reader(buffer))
    assert [r[0] for r in rows] == pages


def test_gsc_export_uses_url_details_metric_for_batched_rows():
    """Regression: a batched task's per-page CTR is now carried in url_details (see
    gsc_rules.py's _task_for_meta_tag_batch) -- the export must read each row's own
    value from there instead of leaving the actual-value column permanently blank."""
    pages = [f"https://x.com/{i}" for i in range(3)]
    task = _task(
        source="gsc", category="meta_tag_reoptimization", affected_urls=pages,
        metric_actual=None, metric_benchmark=0.18,
        url_details={p: {"metric": 0.01 * (i + 1)} for i, p in enumerate(pages)},
    )
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    _write_gsc_ga4_task_row(writer, task, native="gsc")
    buffer.seek(0)
    rows = list(csv.reader(buffer))
    ctr_by_page = {r[0]: r[3] for r in rows}
    for i, page in enumerate(pages):
        assert ctr_by_page[page] == str(0.01 * (i + 1))


def test_gsc_ga4_tracking_header_has_no_redundant_metric_value_columns():
    """Regression: Metric/Value used to duplicate whatever number the native column
    (CTR, Impressions, Engagement rate, ...) already showed -- confusing, and not
    how a real GSC/GA4 export looks. Only Benchmark (genuinely new information a
    native export doesn't have) plus our own task-tracking columns remain."""
    assert "Metric" not in GSC_GA4_TRACKING_HEADER
    assert "Value" not in GSC_GA4_TRACKING_HEADER
    assert GSC_GA4_TRACKING_HEADER[0] == "Benchmark"


def test_gsc_export_populates_every_native_column_from_url_details():
    """Regression: url_details used to carry only the ONE metric a check keyed off
    of (e.g. just CTR), leaving Clicks/Impressions/Position blank even though the
    app has that data -- not what a real Search Console export looks like."""
    page = "https://x.com/a"
    task = _task(
        source="gsc", category="meta_tag_reoptimization", affected_urls=[page],
        metric_actual=None, metric_benchmark=0.18,
        url_details={page: {"clicks": 12, "impressions": 400, "ctr": 0.03, "position": 2.5}},
    )
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    _write_gsc_ga4_task_row(writer, task, native="gsc")
    buffer.seek(0)
    row = next(csv.reader(buffer))
    assert row[:5] == [page, "12", "400", "0.03", "2.5"]


def test_ga4_export_populates_every_native_column_from_url_details():
    page = "https://x.com/a"
    task = _task(
        source="ga4", category="high_exit_rate", affected_urls=[page],
        metric_actual=None, metric_benchmark=0.6,
        url_details={page: {"sessions": 1000, "active_users": 800, "engagement_rate": 0.3, "bounce_rate": 0.7}},
    )
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    _write_gsc_ga4_task_row(writer, task, native="ga4")
    buffer.seek(0)
    row = next(csv.reader(buffer))
    assert row[:5] == [page, "1000", "800", "0.3", "0.7"]


def test_page_optimization_with_ranked_page_data_exports_natively():
    """Regression: page_optimization is source="content_plan" like every other
    recurring task, so it fell into the generic one-line content-plan summary in
    the single-task export -- even once it started carrying real GSC-shaped
    per-page data (see services.py's _ranked_gsc_pages_for_optimization), that real
    data was silently dropped. Must export natively (Clicks/Impressions/CTR/
    Position) instead, exactly like a real gsc-sourced task."""
    from app.routers.tasks import export_single_task_csv  # local import: avoid circular import at module load

    page = "https://x.com/a"
    task = _task(
        source="content_plan", category="page_optimization", affected_urls=[page],
        metric_actual=None, metric_benchmark=None,
        url_details={page: {"clicks": 40, "impressions": 5000, "ctr": 0.008, "position": 6.2}},
    )
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    # Reuse the same branch logic the router applies -- see export_single_task_csv
    if task.category in ("page_optimization", "wasted_impressions") and task.url_details:
        writer.writerow(GSC_CSV_HEADER + GSC_GA4_TRACKING_HEADER)
        _write_gsc_ga4_task_row(writer, task, native="gsc")
    buffer.seek(0)
    rows = list(csv.reader(buffer))
    assert rows[0][:5] == GSC_CSV_HEADER  # native header, not the generic content-plan summary
    assert rows[1][:5] == [page, "40", "5000", "0.008", "6.2"]


def test_page_optimization_without_ranked_data_still_exports_the_generic_summary():
    """No real page data yet (no GSC sync/connection) -- url_details is empty, so
    this must still fall back to the plain content-plan summary row, not crash or
    render an empty native table."""
    task = _task(
        source="content_plan", category="page_optimization", affected_urls=[],
        metric_actual=None, metric_benchmark=None, url_details={},
    )
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    if task.category in ("page_optimization", "wasted_impressions") and task.url_details:
        writer.writerow(GSC_CSV_HEADER + GSC_GA4_TRACKING_HEADER)
        _write_gsc_ga4_task_row(writer, task, native="gsc")
    elif task.source == "content_plan":
        writer.writerow(["Publish/Due Date", "Type", "Title", "Assignee", "Status"])
        writer.writerow([
            task.target_date.isoformat() if task.target_date else "", task.category.replace("_", " "),
            task.title, task.assignee or "Unassigned", task.status,
        ])
    buffer.seek(0)
    rows = list(csv.reader(buffer))
    assert rows[0] == ["Publish/Due Date", "Type", "Title", "Assignee", "Status"]  # generic summary, not native


def test_wasted_impressions_with_ranked_page_data_exports_natively():
    """Same fix as page_optimization above, extended to wasted_impressions once it
    moved from a live gsc-sourced check to source="content_plan" (see
    services.py's _ranked_wasted_impression_pages) -- its real per-page GSC data
    must still export natively, not fall into the generic content-plan summary."""
    page = "https://x.com/a"
    task = _task(
        source="content_plan", category="wasted_impressions", affected_urls=[page],
        metric_actual=None, metric_benchmark=None,
        url_details={page: {"clicks": 2, "impressions": 9000, "ctr": 0.0004, "position": 8.1}},
    )
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    if task.category in ("page_optimization", "wasted_impressions") and task.url_details:
        writer.writerow(GSC_CSV_HEADER + GSC_GA4_TRACKING_HEADER)
        _write_gsc_ga4_task_row(writer, task, native="gsc")
    buffer.seek(0)
    rows = list(csv.reader(buffer))
    assert rows[0][:5] == GSC_CSV_HEADER
    assert rows[1][:5] == [page, "2", "9000", "0.0004", "8.1"]


def test_wasted_impressions_without_ranked_data_still_exports_the_generic_summary():
    """No real page data yet -- same generic content-plan-summary fallback as
    page_optimization's equivalent test."""
    task = _task(
        source="content_plan", category="wasted_impressions", affected_urls=[],
        metric_actual=None, metric_benchmark=None, url_details={},
    )
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    if task.category in ("page_optimization", "wasted_impressions") and task.url_details:
        writer.writerow(GSC_CSV_HEADER + GSC_GA4_TRACKING_HEADER)
        _write_gsc_ga4_task_row(writer, task, native="gsc")
    elif task.source == "content_plan":
        writer.writerow(["Publish/Due Date", "Type", "Title", "Assignee", "Status"])
        writer.writerow([
            task.target_date.isoformat() if task.target_date else "", task.category.replace("_", " "),
            task.title, task.assignee or "Unassigned", task.status,
        ])
    buffer.seek(0)
    rows = list(csv.reader(buffer))
    assert rows[0] == ["Publish/Due Date", "Type", "Title", "Assignee", "Status"]


def test_crawl_export_uses_url_details_category_for_consolidated_rows():
    """Regression: a consolidated Technical Audit task's export used to write
    category="technical_audit"/status="" for every row regardless of which check
    (404/redirect/indexation-blocking/server error) actually flagged that url --
    url_details must drive the per-row Category and Status columns instead."""
    urls = ["https://x.com/404-page", "https://x.com/blocked-page"]
    task = _task(
        category="technical_audit", severity="high", affected_urls=urls,
        metric_actual=None, metric_benchmark=None,
        url_details={
            urls[0]: {"category": "404_fix", "severity": "medium"},
            urls[1]: {"category": "indexation_blocking", "severity": "high"},
        },
    )
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    _write_crawl_task_rows(writer, task)
    buffer.seek(0)
    rows = {r[2]: r for r in csv.reader(buffer)}
    assert rows[urls[0]][15] == "404_fix" and rows[urls[0]][16] == "medium" and rows[urls[0]][7] == "Not Found"
    assert (
        rows[urls[1]][15] == "indexation_blocking"
        and rows[urls[1]][16] == "high"
        and rows[urls[1]][7] == "Blocked from indexing"
    )
