import datetime as dt
import os

from app.models import Campaign, CrawlImport, CrawlIssue, Site, Task
from app.services import run_crawl_import

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def test_end_to_end_crawl_import_produces_scheduled_tasks(db_session):
    site = Site(domain="example.com")
    db_session.add(site)
    db_session.flush()

    # Opts out of the default "one consolidated Technical Audit task" workflow (see
    # Campaign.consolidate_technical_tasks) specifically to exercise per-category tier
    # ordering below -- that behavior still needs to work correctly for analysts who
    # choose separate tasks per category, even though it's no longer the default.
    campaign = Campaign(
        site_id=site.id, start_date=dt.date(2026, 9, 1), duration_months=6, capacity_per_week=5,
        consolidate_technical_tasks=False,
    )
    db_session.add(campaign)
    db_session.commit()

    run_crawl_import(db_session, site.id, FIXTURES, dt.date(2026, 8, 15))

    issues = db_session.query(CrawlIssue).filter(CrawlIssue.site_id == site.id).all()
    assert len(issues) == 3  # 404, 301, 5xx from fixtures

    # every import also seeds the one-off standing tasks (Benchmarking, Schema
    # Recommendations, Anchor Optimization -- see ensure_benchmarking_task/
    # ensure_schema_recommendations_task/ensure_anchor_optimization_task), excluded
    # here since this test is specifically about crawl-side tier ordering, covered on
    # its own in test_optimization_levels.py / test_content_rules.py.
    standing = {"prompt_keyword_benchmarking", "schema_recommendations", "anchor_optimization", "url_structure_optimization"}
    tasks = (
        db_session.query(Task)
        .filter(Task.site_id == site.id, Task.category.not_in(standing))
        .order_by(Task.severity)
        .all()
    )
    assert len(tasks) == 3
    # every task should be scheduled (month_index/target_date set) since a campaign exists
    assert all(t.month_index is not None for t in tasks)
    assert all(t.target_date is not None and t.target_date >= campaign.start_date for t in tasks)

    # Per the unified phase scheduler's fixed Key Fix order (see services.py's
    # _KEY_FIX_ORDER): server_error, then 404_fix, then redirect_inlink_update,
    # regardless of each issue's individual severity -- the old per-severity tier
    # nuance (redirect cleanup could outrank a non-high-impact 404) was superseded by
    # the analyst's explicit phase ordering ("technical must be done, then the rest").
    # Never-decreasing (<=), not strictly increasing (<): each is a cheap 1-hour task
    # (see app/rules/task_hours.py), so several legitimately share one 8-hour day now
    # under the capacity-based scheduler -- same day is fine, out-of-order isn't.
    by_category = {t.category: t.target_date for t in tasks}
    assert by_category["server_error"] <= by_category["404_fix"] <= by_category["redirect_inlink_update"]


def test_import_without_campaign_still_schedules_from_today(db_session):
    site = Site(domain="example.com")
    db_session.add(site)
    db_session.commit()

    run_crawl_import(db_session, site.id, FIXTURES, dt.date(2026, 8, 15))

    # No campaign configured yet -- still defaults to one consolidated "Technical Audit"
    # task (see Campaign.consolidate_technical_tasks's default), not the old per-category
    # behavior, so a fresh site gets the analyst-friendly default from day one. Also gets
    # the one-off standing tasks every import seeds: Benchmarking, Schema
    # Recommendations, Anchor Optimization.
    tasks = db_session.query(Task).filter(Task.site_id == site.id).all()
    categories = {t.category for t in tasks}
    assert categories == {
        "technical_audit", "prompt_keyword_benchmarking", "schema_recommendations", "anchor_optimization",
        "url_structure_optimization",
    }
    # schema_recommendations and anchor_optimization are both seeded with no date of
    # their own -- reschedule_all_tasks assigns them one (schema_recommendations via
    # its technical_audit-week post-pass, anchor_optimization via the normal
    # sequential queue), and that function no-ops entirely without a campaign to
    # anchor to. So without a campaign, those two legitimately stay unscheduled;
    # technical_audit and prompt_keyword_benchmarking get real dates independently
    # (assign_schedule/ensure_benchmarking_task both fall back to "today" on their own).
    scheduled = [t for t in tasks if t.category in ("technical_audit", "prompt_keyword_benchmarking")]
    assert all(t.target_date is not None for t in scheduled)


def test_reimporting_replaces_old_crawl_tasks_instead_of_duplicating_them():
    """A re-crawl (fixing a corrupted export, a monthly re-crawl, etc.) should refresh
    the crawl backlog, not pile duplicate tasks on top of the previous import's -- same
    idempotency guarantee sync_gsc_and_generate_tasks/sync_ga4_and_generate_tasks already
    give their own "source" tasks."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.db import Base

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()

    site = Site(domain="example.com")
    db.add(site)
    db.commit()

    run_crawl_import(db, site.id, FIXTURES, dt.date(2026, 8, 15))
    run_crawl_import(db, site.id, FIXTURES, dt.date(2026, 9, 1))  # same fixtures, re-imported

    tasks = db.query(Task).filter(Task.site_id == site.id, Task.source == "crawl").all()
    assert len(tasks) == 1  # not 2 -- the first import's Technical Audit task was cleared, not duplicated

    # CrawlImport/CrawlIssue history is untouched -- both imports still on record
    imports = db.query(CrawlImport).filter(CrawlImport.site_id == site.id).all()
    assert len(imports) == 2
    issues = db.query(CrawlIssue).filter(CrawlIssue.site_id == site.id).all()
    assert len(issues) == 6  # 3 per import, both kept as a log


def test_benchmarking_task_is_seeded_once_and_never_duplicated_on_reimport(db_session):
    site = Site(domain="example.com")
    db_session.add(site)
    db_session.commit()

    run_crawl_import(db_session, site.id, FIXTURES, dt.date(2026, 8, 15))
    benchmarking = db_session.query(Task).filter(
        Task.site_id == site.id, Task.category == "prompt_keyword_benchmarking"
    ).all()
    assert len(benchmarking) == 1
    original_id = benchmarking[0].id

    # analyst has started working it -- re-importing must not reset or duplicate it
    benchmarking[0].status = "in_progress"
    db_session.commit()

    run_crawl_import(db_session, site.id, FIXTURES, dt.date(2026, 9, 1))
    benchmarking_after = db_session.query(Task).filter(
        Task.site_id == site.id, Task.category == "prompt_keyword_benchmarking"
    ).all()
    assert len(benchmarking_after) == 1
    assert benchmarking_after[0].id == original_id
    assert benchmarking_after[0].status == "in_progress"  # untouched by the reimport


def test_benchmarking_task_never_lands_after_the_crawl_tasks_it_precedes(db_session):
    """Regression name changed from "...does_not_collide..." -- under the capacity-
    based scheduler, benchmarking legitimately CAN share a day with technical_audit
    now (3h + 3h = 6h, comfortably under the 8-hour cap -- see app/rules/task_hours.py),
    which isn't a bug, it's the whole point of packing by hours instead of one task
    per week. What must still hold: benchmarking (phase rank 0) never lands on a
    LATER day than anything that comes after it in the hierarchy, and no day's total
    hours exceed the daily cap."""
    site = Site(domain="example.com")
    db_session.add(site)
    db_session.flush()
    campaign = Campaign(site_id=site.id, start_date=dt.date(2026, 9, 1), duration_months=6, capacity_per_week=5)
    db_session.add(campaign)
    db_session.commit()

    run_crawl_import(db_session, site.id, FIXTURES, dt.date(2026, 8, 15))
    tasks = db_session.query(Task).filter(Task.site_id == site.id).all()

    benchmarking = next(t for t in tasks if t.category == "prompt_keyword_benchmarking")
    others = [t for t in tasks if t.category != "prompt_keyword_benchmarking" and t.target_date]
    assert all(benchmarking.target_date <= t.target_date for t in others)

    hours_by_day: dict = {}
    for t in tasks:
        if t.target_date:
            hours_by_day[t.target_date] = hours_by_day.get(t.target_date, 0.0) + t.estimated_hours
    assert all(total <= 8.0 for total in hours_by_day.values())


def test_optimization_level_is_set_from_the_default_mapping(db_session):
    site = Site(domain="example.com")
    db_session.add(site)
    db_session.commit()

    run_crawl_import(db_session, site.id, FIXTURES, dt.date(2026, 8, 15))
    by_category = {t.category: t.optimization_level for t in db_session.query(Task).filter(Task.site_id == site.id)}
    assert by_category["technical_audit"] == "key_fix"
    assert by_category["prompt_keyword_benchmarking"] == "benchmarking"
