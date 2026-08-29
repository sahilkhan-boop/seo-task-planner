"""ORM models.

Covers the full data model from the plan. Phase 1 (this build) actively uses
Site, Campaign, Benchmark, CrawlImport, CrawlIssue, and Task. Connection and
MetricSnapshot are defined now so the GSC/GA4 phases (3-4) plug in without a
schema migration.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import JSON, Boolean, Date, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def _utcnow() -> dt.datetime:
    """Naive UTC now -- these columns aren't timezone-aware, so strip tzinfo after generating it."""
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


class Site(Base):
    __tablename__ = "sites"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    domain: Mapped[str] = mapped_column(String, unique=True)
    gsc_site_url: Mapped[str | None] = mapped_column(String, nullable=True)
    ga4_property_id: Mapped[str | None] = mapped_column(String, nullable=True)
    brand_terms: Mapped[str | None] = mapped_column(String, nullable=True)  # comma-separated, for non-branded query detection
    # The site's one query-level regex -- an optional regex alternative to
    # brand_terms' plain substring matching, for a brand name with real-world
    # spelling variants/misspellings a comma-separated term list can't cover well
    # (apostrophe/hyphen variants, common misspellings, sub-brand names, the bare
    # domain). When set, this takes priority over brand_terms for branded-query
    # classification (see gsc_rules.py's is_branded_query); invalid regex is
    # ignored the same way the GSC/GA4 page-filter regexes already are (see
    # apply_gsc_filters), never crashes a sync.
    #
    # Used to be two separate fields -- this one, plus a gsc_query_filter_regex
    # that instead REMOVED matching queries from the data before the rule engine
    # ever saw them (an include/exclude scoping filter, not a classification).
    # Merged into just this one (2026-08-29): the two purposes actively conflicted
    # for any site using both (an "exclude" filter on this same pattern would have
    # deleted every branded query before classification could ever see them), and
    # in practice no real site had ever used the filtering behavior at all --
    # brand_regex is the one every real site actually depends on.
    brand_regex: Mapped[str | None] = mapped_column(String, nullable=True)
    # GSC-report-style regex filter (same idea as Search Console's own Performance
    # report page filter) -- optional, applied before the GSC rule engine sees the
    # data at all, so an analyst can scope task generation to (or away from) a
    # specific URL path without touching code. Invalid regex is ignored rather
    # than erroring the whole sync -- see app/services.py's apply_gsc_filters.
    # Query-level filtering doesn't have an equivalent -- see brand_regex's own
    # comment for why that got merged away instead of kept alongside this one.
    gsc_page_filter_regex: Mapped[str | None] = mapped_column(String, nullable=True)
    gsc_page_filter_mode: Mapped[str] = mapped_column(String, default="include")  # "include" | "exclude"
    # GA4 has no query dimension (that's a GSC/search concept) -- page-only filter, same
    # "scope to (or away from) a specific URL path" idea, useful when a campaign only
    # covers one folder/section of the site.
    ga4_page_filter_regex: Mapped[str | None] = mapped_column(String, nullable=True)
    ga4_page_filter_mode: Mapped[str] = mapped_column(String, default="include")  # "include" | "exclude"
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)

    campaigns: Mapped[list["Campaign"]] = relationship(back_populates="site", cascade="all, delete-orphan")
    benchmarks: Mapped[list["Benchmark"]] = relationship(back_populates="site", cascade="all, delete-orphan")
    crawl_imports: Mapped[list["CrawlImport"]] = relationship(back_populates="site", cascade="all, delete-orphan")
    tasks: Mapped[list["Task"]] = relationship(back_populates="site", cascade="all, delete-orphan")


class Campaign(Base):
    __tablename__ = "campaigns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id"))
    start_date: Mapped[dt.date] = mapped_column(Date)
    duration_months: Mapped[int] = mapped_column(Integer, default=6)
    capacity_per_week: Mapped[int] = mapped_column(Integer, default=5)  # max tasks/week an analyst can pick up
    content_pieces_per_month: Mapped[int | None] = mapped_column(Integer, nullable=True)  # package size, e.g. "8 blogs/mo"
    pages_to_optimize_per_month: Mapped[int | None] = mapped_column(Integer, nullable=True)  # existing pages to improve/mo
    # Some campaigns have no existing content worth optimizing at all -- lets the
    # analyst redirect that same monthly count toward creating brand-new pages
    # instead (see content_rules.py's generate_content_plan), rather than the
    # quota just describing "existing pages" unconditionally. "optimize_existing"
    # (default) keeps today's behavior.
    page_work_mode: Mapped[str] = mapped_column(String, default="optimize_existing")  # "optimize_existing" | "create_new"
    notes: Mapped[str | None] = mapped_column(String, nullable=True)  # anything else about the package/scope
    default_assignee: Mapped[str | None] = mapped_column(String, nullable=True)  # pre-fills new tasks; reassignable per-task
    # How the analyst wants to work the crawl-side technical backlog (404s, redirects,
    # indexation-blocking, server errors): one consolidated "Technical Audit" task
    # covering everything, or the existing per-category tasks. Defaults to consolidated
    # -- most analysts would rather do one focused technical pass than juggle several
    # separate crawl tickets, and it's the same "don't fragment into look-alike tasks"
    # reasoning already applied to campaign-wide batching (see crawl_rules.py) -- just
    # user-controlled instead of a fixed platform rule, since package/workflow shape is
    # the analyst's call, not the platform's (see setup_campaign.html).
    consolidate_technical_tasks: Mapped[bool] = mapped_column(Boolean, default=True)

    site: Mapped[Site] = relationship(back_populates="campaigns")


class Connection(Base):
    """Google OAuth connection for GSC/GA4 sync (phase 3-4).

    site_id is nullable -- None means this is the one shared, desktop-wide
    connection for that provider, reused across every site rather than requiring
    a fresh trip through Google's consent screen (and whatever internal
    verification/approval that needs) for every new client project. A real
    per-site row (site_id set) still wins over the shared one when both exist,
    for the rare client whose data genuinely needs a different Google account --
    see services.find_connection for the actual lookup order, and
    routers/google_auth.py's oauth_callback for where new connections default to
    being saved as the shared one."""

    __tablename__ = "connections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    site_id: Mapped[int | None] = mapped_column(ForeignKey("sites.id"), nullable=True)
    provider: Mapped[str] = mapped_column(String)  # "gsc" | "ga4"
    access_token: Mapped[str] = mapped_column(String)
    refresh_token: Mapped[str] = mapped_column(String)
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime)


class Benchmark(Base):
    """A configurable threshold for a metric, optionally segmented.

    comparator: "lt" (flag when actual < target -> underperforming) or
                "gt" (flag when actual > target -> overperforming/bad, e.g. exit rate)
    segment: optional key like a CTR position-bucket ("1-3", "4-10", "11-20")
             or a device type ("mobile"). Null/"" = applies site-wide.
    """

    __tablename__ = "benchmarks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id"))
    metric_key: Mapped[str] = mapped_column(String)  # e.g. "engagement_rate", "exit_rate", "ctr", "mobile_share"
    segment: Mapped[str | None] = mapped_column(String, nullable=True)
    comparator: Mapped[str] = mapped_column(String)  # "lt" | "gt"
    target_value: Mapped[float] = mapped_column(Float)

    site: Mapped[Site] = relationship(back_populates="benchmarks")


class MetricSnapshot(Base):
    """Monthly rollup of a GSC/GA4 metric per URL (or site-wide when url is null)."""

    __tablename__ = "metric_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id"))
    source: Mapped[str] = mapped_column(String)  # "gsc" | "ga4"
    url: Mapped[str | None] = mapped_column(String, nullable=True)
    month: Mapped[dt.date] = mapped_column(Date)  # first-of-month
    metric_key: Mapped[str] = mapped_column(String)
    value: Mapped[float] = mapped_column(Float)
    extra: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # e.g. {"position": 4.2} for CTR bucketing


class CrawlImport(Base):
    """One Screaming Frog export batch that's been ingested."""

    __tablename__ = "crawl_imports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id"))
    crawl_date: Mapped[dt.date] = mapped_column(Date)
    source_folder: Mapped[str] = mapped_column(String)
    imported_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)
    issue_count: Mapped[int] = mapped_column(Integer, default=0)
    total_urls: Mapped[int] = mapped_column(Integer, default=0)  # every crawled URL, not just issues
    site_scale: Mapped[str] = mapped_column(String, default="small")  # "small" | "medium" | "large"

    site: Mapped[Site] = relationship(back_populates="crawl_imports")
    issues: Mapped[list["CrawlIssue"]] = relationship(back_populates="crawl_import", cascade="all, delete-orphan")


class CrawlIssue(Base):
    __tablename__ = "crawl_issues"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id"))
    crawl_import_id: Mapped[int] = mapped_column(ForeignKey("crawl_imports.id"))
    issue_type: Mapped[str] = mapped_column(String)  # "404" | "301" | "302" | "5xx"
    url: Mapped[str] = mapped_column(String)
    status_code: Mapped[int] = mapped_column(Integer)
    redirects_to: Mapped[str | None] = mapped_column(String, nullable=True)
    inlinking_urls: Mapped[list] = mapped_column(JSON, default=list)  # pages that link to `url`

    crawl_import: Mapped[CrawlImport] = relationship(back_populates="issues")


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id"))
    source: Mapped[str] = mapped_column(String)  # "crawl" | "gsc" | "ga4"
    category: Mapped[str] = mapped_column(String)  # "404_fix" | "redirect_inlink_update" | "ctr_optimization" | ...
    title: Mapped[str] = mapped_column(String)
    description: Mapped[str] = mapped_column(String)
    affected_urls: Mapped[list] = mapped_column(JSON, default=list)
    # Per-URL extras the export needs but affected_urls (a flat list) can't carry:
    # consolidated technical_audit tasks key each url to {"category":, "severity":}
    # (its original sub-issue, lost when consolidate_technical_tasks flattens them
    # into one list); batched GSC/GA4 tasks key each url to {"metric": <actual value>}
    # (its own per-page number, lost when several per-page tasks collapse into one).
    # Empty {} for every task that isn't consolidated/batched -- exports fall back to
    # the task-level category/severity/metric_actual exactly as before.
    url_details: Mapped[dict] = mapped_column(JSON, default=dict)
    severity: Mapped[str] = mapped_column(String)  # "high" | "medium" | "low"
    metric_actual: Mapped[float | None] = mapped_column(Float, nullable=True)
    metric_benchmark: Mapped[float | None] = mapped_column(Float, nullable=True)
    month_index: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 0-based month within campaign
    target_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    # How much of a person's 8-hour day this task actually takes -- see
    # app/rules/task_hours.py's HOURS_BY_CATEGORY (real, analyst-supplied numbers, not a
    # guess) -- what services.reschedule_all_tasks packs each day's capacity against.
    estimated_hours: Mapped[float] = mapped_column(Float, default=1.0)
    # True once a human has moved this task's date by hand (see routers/tasks.py's
    # due-date route) -- reschedule_all_tasks then leaves target_date/month_index alone
    # on its next run instead of re-deriving and silently overwriting a deliberate
    # choice, while still counting its hours against that day's capacity so autoscheduled
    # work doesn't get packed on top of it past 8 hours.
    manually_scheduled: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String, default="todo")  # "todo" | "in_progress" | "done"
    effort_tier: Mapped[str] = mapped_column(String, default="medium")  # "low" | "medium" | "high"
    assignee: Mapped[str | None] = mapped_column(String, nullable=True)  # who performs this task
    # Analyst-facing priority framing -- "benchmarking" | "key_fix" | "quick_win" |
    # "ongoing_content" (see app/rules/optimization_levels.py). Set to a sensible
    # default when the task is generated, but always freely editable afterward -- this
    # is a classification the analyst owns, not a fixed platform rule (same "package
    # shape is the analyst's call" reasoning as Campaign.consolidate_technical_tasks).
    optimization_level: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)

    site: Mapped[Site] = relationship(back_populates="tasks")


class ChatMessage(Base):
    """One turn of the plan-editing chat, per site. `role` is "user" or "assistant";
    tool calls/results the assistant made along the way are logged in `actions_summary`
    (plain-English, e.g. "moved 2 tasks to March") so the analyst can see what changed
    without reading raw tool-call JSON.
    """

    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id"))
    role: Mapped[str] = mapped_column(String)  # "user" | "assistant"
    content: Mapped[str] = mapped_column(String)
    actions_summary: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)
