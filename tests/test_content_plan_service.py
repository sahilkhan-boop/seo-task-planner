import datetime as dt

import pytest

from app.models import Benchmark, Campaign, MetricSnapshot, Site, Task
from app.rules.reporting_rules import generate_reporting_tasks
from app.services import NoCampaignError, regenerate_content_plan


def test_raises_without_a_campaign(db_session):
    site = Site(domain="no-campaign.com")
    db_session.add(site)
    db_session.commit()

    with pytest.raises(NoCampaignError):
        regenerate_content_plan(db_session, site.id)


def test_generates_and_is_idempotent_on_regeneration(db_session):
    site = Site(domain="example.com")
    db_session.add(site)
    db_session.flush()
    campaign = Campaign(
        site_id=site.id, start_date=dt.date(2026, 8, 15), duration_months=6,
        content_pieces_per_month=4, pages_to_optimize_per_month=2, default_assignee="Priya",
    )
    db_session.add(campaign)
    db_session.commit()

    # 3 (content pipeline: research/brief/article, fixed regardless of package size)
    # + 1 (page_optimization, one consolidated task covering all pages regardless of
    # how many are in the package) + 1 (wasted_impressions, same monthly ranked
    # cadence as page_optimization) + 1 (llm_optimization) per month, plus the
    # reporting cadence (1 performance_dashboard + a weekly_report or
    # monthly_report_mbr for every Wednesday from month 2 onward) -- computed via the
    # real generator rather than hardcoded, since it depends on how many Wednesdays
    # each calendar month actually has.
    expected_pipeline_count = 6 * (3 + 1 + 1 + 1)
    expected_reporting_count = len(generate_reporting_tasks(campaign.start_date, campaign.duration_months))

    count = regenerate_content_plan(db_session, site.id)
    assert count == expected_pipeline_count + expected_reporting_count

    tasks = db_session.query(Task).filter(Task.site_id == site.id, Task.source == "content_plan").all()
    assert len(tasks) == count
    assert all(t.assignee == "Priya" for t in tasks)
    # reschedule_all_tasks lays the content pipeline out at exactly one task/week,
    # bounded to the campaign's configured duration -- reporting tasks are calendar-
    # anchored instead (generate_reporting_tasks) and always fully scheduled, but
    # the pipeline itself (36 tasks competing for ~26 weeks here) can genuinely have
    # some left unscheduled (month_index=None) rather than compressed to fit, or
    # spilled past the deadline. See reschedule_all_tasks's docstring.
    reporting_tasks = [t for t in tasks if t.category in ("performance_dashboard", "weekly_report", "monthly_report_mbr")]
    pipeline_tasks = [t for t in tasks if t not in reporting_tasks]
    assert all(t.month_index is not None and t.month_index >= 0 for t in reporting_tasks)
    assert any(t.month_index is not None for t in pipeline_tasks)  # at least some fit
    assert all(t.month_index is None or t.month_index >= 0 for t in pipeline_tasks)

    # regenerating after a package change shouldn't leave stale duplicates behind
    campaign.content_pieces_per_month = 2
    db_session.commit()
    new_count = regenerate_content_plan(db_session, site.id)
    assert new_count == expected_pipeline_count + expected_reporting_count  # unaffected by package size dropping to 2
    tasks_after = db_session.query(Task).filter(Task.site_id == site.id, Task.source == "content_plan").all()
    assert len(tasks_after) == new_count


def test_regenerating_preserves_the_seeded_benchmarking_task(db_session):
    """The one-off Benchmarking task also uses source='content_plan' (see
    ensure_benchmarking_task) but must survive regenerate_content_plan's cleanup --
    it's create-once, not part of the monthly content pipeline this function
    regenerates."""
    from app.services import ensure_benchmarking_task

    site = Site(domain="example.com")
    db_session.add(site)
    db_session.flush()
    campaign = Campaign(
        site_id=site.id, start_date=dt.date(2026, 9, 1), duration_months=3, content_pieces_per_month=2,
    )
    db_session.add(campaign)
    db_session.commit()

    ensure_benchmarking_task(db_session, site.id, campaign.start_date)
    regenerate_content_plan(db_session, site.id)

    benchmarking = db_session.query(Task).filter(
        Task.site_id == site.id, Task.category == "prompt_keyword_benchmarking"
    ).all()
    assert len(benchmarking) == 1


def test_leaves_crawl_tasks_untouched(db_session):
    site = Site(domain="example.com")
    db_session.add(site)
    db_session.flush()
    campaign = Campaign(
        site_id=site.id, start_date=dt.date(2026, 8, 15), duration_months=3,
        content_pieces_per_month=2, pages_to_optimize_per_month=1,
    )
    db_session.add(campaign)
    db_session.add(
        Task(
            site_id=site.id, source="crawl", category="404_fix", title="Fix 404",
            description="d", severity="high", status="todo",
        )
    )
    db_session.commit()

    regenerate_content_plan(db_session, site.id)

    crawl_tasks = db_session.query(Task).filter(Task.site_id == site.id, Task.source == "crawl").all()
    assert len(crawl_tasks) == 1


def _snapshot(site_id, url, impressions, month=dt.date(2026, 8, 1)):
    return MetricSnapshot(
        site_id=site_id, source="gsc", url=url, month=month, metric_key="ctr", value=0.02,
        extra={"position": 5.0, "clicks": 10, "impressions": impressions},
    )


def test_page_optimization_ranks_real_synced_pages_by_impressions(db_session):
    """Integration: once a real GSC sync has happened (MetricSnapshot rows exist),
    page_optimization tasks should carry real pages, highest-impressions first --
    not the generic placeholder. pages_to_optimize_per_month=2 + PAGE_OPTIMIZATION_BUFFER
    (2) = a batch of 4, so all 3 synced pages fit in one partial batch."""
    site = Site(domain="example.com")
    db_session.add(site)
    db_session.flush()
    db_session.add(Campaign(
        site_id=site.id, start_date=dt.date(2026, 8, 15), duration_months=1,
        content_pieces_per_month=0, pages_to_optimize_per_month=2,
    ))
    db_session.add_all([
        _snapshot(site.id, "https://example.com/low", impressions=100),
        _snapshot(site.id, "https://example.com/high", impressions=9000),
        _snapshot(site.id, "https://example.com/mid", impressions=500),
    ])
    db_session.commit()

    regenerate_content_plan(db_session, site.id)

    task = db_session.query(Task).filter(Task.site_id == site.id, Task.category == "page_optimization").one()
    # all 3 fit in the buffered batch of 4, descending by impressions
    assert task.affected_urls == ["https://example.com/high", "https://example.com/mid", "https://example.com/low"]
    assert task.url_details["https://example.com/high"]["impressions"] == 9000


def test_page_optimization_deduplicates_urls_synced_multiple_times(db_session):
    """Regression, found against real O'Reilly data: MetricSnapshot rows accumulate
    as a historical log (a re-sync adds new rows, it doesn't overwrite the old
    ones), so the same url can have several snapshot rows within the same month
    bucket after multiple sync runs. Ranking on the raw rows let the same
    high-impression url appear several times in a row at the top of the list
    instead of once."""
    site = Site(domain="example.com")
    db_session.add(site)
    db_session.flush()
    db_session.add(Campaign(
        site_id=site.id, start_date=dt.date(2026, 8, 15), duration_months=1,
        content_pieces_per_month=0, pages_to_optimize_per_month=2,
    ))
    # "popular" synced 3 times (3 sync runs landing in the same month bucket),
    # "other" synced once -- without dedup, "popular" would fill both slots.
    db_session.add_all([
        _snapshot(site.id, "https://example.com/popular", impressions=9000),
        _snapshot(site.id, "https://example.com/popular", impressions=9010),
        _snapshot(site.id, "https://example.com/popular", impressions=9020),
        _snapshot(site.id, "https://example.com/other", impressions=500),
    ])
    db_session.commit()

    regenerate_content_plan(db_session, site.id)

    task = db_session.query(Task).filter(Task.site_id == site.id, Task.category == "page_optimization").one()
    assert task.affected_urls == ["https://example.com/popular", "https://example.com/other"]  # not 2x popular


def test_page_optimization_ignores_ranking_in_create_new_mode(db_session):
    """create_new mode has no existing-page backlog to rank at all -- real
    MetricSnapshot data existing shouldn't leak into a "create new pages" task."""
    site = Site(domain="example.com")
    db_session.add(site)
    db_session.flush()
    db_session.add(Campaign(
        site_id=site.id, start_date=dt.date(2026, 8, 15), duration_months=1,
        content_pieces_per_month=0, pages_to_optimize_per_month=2, page_work_mode="create_new",
    ))
    db_session.add(_snapshot(site.id, "https://example.com/high", impressions=9000))
    db_session.commit()

    regenerate_content_plan(db_session, site.id)

    task = db_session.query(Task).filter(Task.site_id == site.id, Task.category == "new_page_creation").one()
    assert task.affected_urls == []  # no ranking applied in this mode


# ---------- wasted_impressions ranked by real GSC data (same monthly, ranked,
# non-repeating cadence as page_optimization -- see
# services.py's _ranked_wasted_impression_pages) ----------


def _wasted_snapshot(site_id, url, impressions, ctr=0.001, month=dt.date(2026, 8, 1)):
    return MetricSnapshot(
        site_id=site_id, source="gsc", url=url, month=month, metric_key="ctr", value=ctr,
        extra={"position": 8.0, "clicks": 1, "impressions": impressions},
    )


def _wasted_benchmark(site_id, target_value=0.005):
    return Benchmark(site_id=site_id, metric_key="ctr", segment="high_impression_wasted", target_value=target_value, comparator="lt")


def test_wasted_impressions_ranks_real_synced_pages_meeting_the_configured_ctr_floor(db_session):
    """Integration: once a real GSC sync AND a "high_impression_wasted" Benchmark
    exist, wasted_impressions tasks should carry real qualifying pages,
    highest-impressions first -- pages with a healthy CTR (not wasted) are
    excluded even though they're also synced."""
    site = Site(domain="example.com")
    db_session.add(site)
    db_session.flush()
    db_session.add(Campaign(
        site_id=site.id, start_date=dt.date(2026, 8, 15), duration_months=1,
        content_pieces_per_month=0, pages_to_optimize_per_month=2,
    ))
    db_session.add(_wasted_benchmark(site.id))
    db_session.add_all([
        _wasted_snapshot(site.id, "https://example.com/wasted-big", impressions=9000, ctr=0.001),
        _wasted_snapshot(site.id, "https://example.com/wasted-small", impressions=1500, ctr=0.002),
        # healthy CTR -- real visibility being used well, not "wasted"
        _wasted_snapshot(site.id, "https://example.com/healthy", impressions=5000, ctr=0.15),
        # below WASTED_IMPRESSION_MIN_IMPRESSIONS (1000) -- too low-volume to be a signal
        _wasted_snapshot(site.id, "https://example.com/too-small", impressions=200, ctr=0.001),
    ])
    db_session.commit()

    regenerate_content_plan(db_session, site.id)

    task = db_session.query(Task).filter(Task.site_id == site.id, Task.category == "wasted_impressions").one()
    assert task.affected_urls == ["https://example.com/wasted-big", "https://example.com/wasted-small"]
    assert task.url_details["https://example.com/wasted-big"]["impressions"] == 9000


def test_wasted_impressions_skipped_without_a_configured_benchmark(db_session):
    """No "high_impression_wasted" Benchmark row for this site -- same "don't
    fabricate a threshold" rule the old live check followed. Falls back to the
    generic placeholder rather than silently picking an arbitrary floor."""
    site = Site(domain="example.com")
    db_session.add(site)
    db_session.flush()
    db_session.add(Campaign(
        site_id=site.id, start_date=dt.date(2026, 8, 15), duration_months=1,
        content_pieces_per_month=0, pages_to_optimize_per_month=2,
    ))
    db_session.add(_wasted_snapshot(site.id, "https://example.com/wasted-big", impressions=9000))
    db_session.commit()

    regenerate_content_plan(db_session, site.id)

    task = db_session.query(Task).filter(Task.site_id == site.id, Task.category == "wasted_impressions").one()
    assert task.affected_urls == []


def test_wasted_impressions_deduplicates_urls_synced_multiple_times(db_session):
    """Same real bug class as page_optimization's dedup regression -- a url synced
    several times in the same month bucket shouldn't fill more than one slot."""
    site = Site(domain="example.com")
    db_session.add(site)
    db_session.flush()
    db_session.add(Campaign(
        site_id=site.id, start_date=dt.date(2026, 8, 15), duration_months=1,
        content_pieces_per_month=0, pages_to_optimize_per_month=2,
    ))
    db_session.add(_wasted_benchmark(site.id))
    db_session.add_all([
        _wasted_snapshot(site.id, "https://example.com/popular", impressions=9000),
        _wasted_snapshot(site.id, "https://example.com/popular", impressions=9010),
        _wasted_snapshot(site.id, "https://example.com/other", impressions=1500),
    ])
    db_session.commit()

    regenerate_content_plan(db_session, site.id)

    task = db_session.query(Task).filter(Task.site_id == site.id, Task.category == "wasted_impressions").one()
    assert task.affected_urls == ["https://example.com/popular", "https://example.com/other"]  # not 2x popular


def test_wasted_impressions_ignores_ranking_in_create_new_mode(db_session):
    site = Site(domain="example.com")
    db_session.add(site)
    db_session.flush()
    db_session.add(Campaign(
        site_id=site.id, start_date=dt.date(2026, 8, 15), duration_months=1,
        content_pieces_per_month=0, pages_to_optimize_per_month=2, page_work_mode="create_new",
    ))
    db_session.add(_wasted_benchmark(site.id))
    db_session.add(_wasted_snapshot(site.id, "https://example.com/wasted-big", impressions=9000))
    db_session.commit()

    regenerate_content_plan(db_session, site.id)

    assert not db_session.query(Task).filter(Task.site_id == site.id, Task.category == "wasted_impressions").all()
