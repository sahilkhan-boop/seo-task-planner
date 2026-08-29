"""Maps generated tasks onto the campaign calendar.

Priority tiers (lower number = scheduled earlier) -- per the approved workflow
(workflow.pdf, page 3), ranked by how much each issue type blocks indexation
or traffic at scale, not just by category:
  0 - indexation-blocking issues at scale (noindex/robots-blocked at volume) --
      affects whether pages can rank AT ALL, regardless of everything else
  1 - server errors (5xx) -- site-health/uptime risk, always urgent
  2 - high-impact 404s (severity=high: 5+ internal links pointing at them)
  3 - redirect/internal-link cleanup -- real leverage, rarely urgent
  4 - low/medium-impact 404s
  5 - meta_tag_reoptimization (GSC tier 1) -- page already ranks 1-5, CTR is
      the only thing wrong, fix is copy-only. Easiest/fastest content-side
      win, so it leads the content tiers.
  6 - content_expansion (GSC tier 2) -- page ranks 5-15 with real non-branded
      query demand it isn't capturing. More effort than tier 5 (actual
      content work), still targeted at an existing page.
  7 - ui_ux_review (GA4) -- high-traffic, high-user pages with poor
      engagement. Wants technical debt cleared first so the data isn't
      confounded by 404s/redirects, and wants the "is the content itself
      the problem" tiers (5-6) checked before assuming it's a UX problem.
  8 - ctr_optimization (GSC tier 3, gradual catch-all) -- pages beyond
      position 15 with real impressions but no single quick fix. Lowest
      priority of the content tiers: broad, opportunistic, worked gradually.
  9 - remaining GA4 checks (exit rate, mobile share, key events) -- same
      "technical/content first" reasoning as tier 7, kept after it since
      ui_ux_review is the highest-signal GA4 check (gated on real traffic).

Tiers 0-4 (technical/crawl work) are laid out as fast as capacity allows,
starting from the campaign's start date -- a real technical backlog should be
cleared urgently, not paced out. Tiers 5+ (GSC/GA4 content and growth work)
are different: once CAMPAIGN_TASK_BUDGET batching (see crawl_rules.py) brings
a category down to a manageable total, greedily filling those tasks right
after the technical work finishes would empty the whole campaign's content
work into the first few weeks and leave months 2-6 with nothing -- exactly
the opposite of "the client keeps getting some reports" throughout the
engagement. So tiers 5+ are instead spread evenly across every remaining
business day of the campaign (still highest-priority-first: meta_tag_reopt
gets the earliest of those evenly-spread slots, then content_expansion, and
so on), the same "spread N items across available days" logic content_rules.py
already uses for the recurring content package.

Site-scale gating (workflow.pdf, page 2): a SMALL site can fit all technical
work in month 1, so no gating is applied. A LARGE site cannot -- tier 3+
technical work (redirect cleanup, low-impact 404s) is held back to no earlier
than month index 1, so month 1 stays focused on indexation-blockers/
server-errors/high-impact-404s only, even with spare capacity. MEDIUM sites
need no special gating: if there's more technical work than a month's
capacity allows, it naturally spills into month 2 through the normal
sequential day-by-day layout. This gating only applies within the technical
tiers (0-4) -- content/growth tiers are always spread across the full
campaign regardless of site scale, per the reasoning above.

Total-campaign volume control lives one layer up from here, in the rule
engines (app/rules/crawl_rules.py's CAMPAIGN_TASK_BUDGET, applied in
crawl_rules.py/gsc_rules.py/ga4_rules.py): each data-driven category is
capped at a fixed number of tasks for the *whole* campaign, batching multiple
issues/pages into one task once the real count exceeds that budget. This
module just lays out however many tasks it's handed, in priority order --
it doesn't need to know that batching happened upstream.
"""
from __future__ import annotations

import datetime as dt

from app.rules.content_rules import _spread
from app.rules.crawl_rules import GeneratedTask
from app.scheduling.month_utils import add_months

# Categories whose tier doesn't depend on severity (404_fix is special-cased below).
CATEGORY_TIER = {
    # Analyst opted into one consolidated crawl pass (Campaign.consolidate_technical_tasks)
    # -- still technical/crawl work, so it stays in the fast-fill tiers (<CONTENT_TIER_START),
    # not the default tier-9 fallback below, which would wrongly spread it across the whole
    # campaign like GSC/GA4 content work.
    "technical_audit": 0,
    "indexation_blocking": 0,
    "server_error": 1,
    "redirect_inlink_update": 3,
    "wasted_impressions": 5,
    "meta_tag_reoptimization": 5,
    "content_expansion": 6,
    "ui_ux_review": 7,
    "ctr_optimization": 8,
    "high_exit_rate": 9,
    "low_mobile_share": 9,
    "low_key_events": 9,
}

# Tier at/above which work is gated to month index 1+ on a LARGE site (technical tiers only).
GATE_TIER = 3

# Tier at/above which work is GSC/GA4 content-and-growth (spread across the whole
# campaign) rather than technical/crawl (laid out as fast as capacity allows).
CONTENT_TIER_START = 5

SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2}


def _tier(task: GeneratedTask) -> int:
    if task.category == "404_fix":
        return 2 if task.severity == "high" else 4
    return CATEGORY_TIER.get(task.category, 9)


def _priority_key(task: GeneratedTask):
    return (_tier(task), SEVERITY_RANK.get(task.severity, 1), -len(task.affected_urls))


class ScheduledTask(GeneratedTask):
    """GeneratedTask plus the calendar placement the scheduler assigns."""

    def __init__(self, generated: GeneratedTask, month_index: int, target_date: dt.date):
        super().__init__(**generated.__dict__)
        self.month_index = month_index
        self.target_date = target_date


def _nth_business_day(start_date: dt.date, n: int, excluded: frozenset[dt.date] = frozenset()) -> dt.date:
    """The n-th (0-indexed) weekday on/after start_date, skipping Sat/Sun and any date
    in `excluded` -- used to keep this source's tasks off days another source (crawl/
    GSC/GA4, each scheduled independently) already placed a task on for this site."""
    d = start_date
    count = 0
    while True:
        if d.weekday() < 5 and d not in excluded:  # Mon=0 .. Fri=4
            if count == n:
                return d
            count += 1
        d += dt.timedelta(days=1)


def _month_index(start_date: dt.date, target_date: dt.date) -> int:
    return (target_date.year - start_date.year) * 12 + (target_date.month - start_date.month)


def nth_week_business_day(start_date: dt.date, week_index: int) -> dt.date:
    """The business day exactly `week_index` weeks after start_date, snapped forward
    off a weekend if the arithmetic lands on one. Since every week_index is exactly 7
    days apart before snapping, and snapping is consistent (always the same
    day-of-week), two different week_index values can never collide on the same
    date -- this is what assign_phased_schedule/reschedule_all_tasks rely on for the
    "one task, one week, guaranteed no same-day collisions across sources" behavior."""
    d = start_date + dt.timedelta(weeks=week_index)
    while d.weekday() >= 5:
        d += dt.timedelta(days=1)
    return d


def _business_days_in_range(start_date: dt.date, end_date: dt.date) -> list[dt.date]:
    """Every weekday from start_date through end_date, inclusive."""
    days = []
    d = start_date
    while d <= end_date:
        if d.weekday() < 5:
            days.append(d)
        d += dt.timedelta(days=1)
    return days


def assign_schedule(
    tasks: list[GeneratedTask],
    campaign_start_date: dt.date,
    capacity_per_week: int = 5,
    site_scale: str = "small",
    duration_months: int = 6,
    excluded_dates: frozenset[dt.date] | None = None,
) -> list[ScheduledTask]:
    """Technical tiers (0-4) are laid out as fast as capacity allows, starting from
    campaign_start_date -- on a LARGE site, tier>=GATE_TIER technical tasks are held
    back to month index 1+ (see module docstring; since the technical list is sorted
    by tier first, once the first gated task is reached every remaining technical
    task is also gated, so the business-day counter only ever needs to fast-forward
    once). Content/growth tiers (5+) are spread evenly across every remaining
    business day of the campaign instead, so there's steady work in every month
    rather than the whole batched-down total finishing in the first few weeks.

    excluded_dates: calendar dates to skip entirely when placing tasks -- crawl, GSC,
    and GA4 tasks are each generated and scheduled independently (separate imports/syncs,
    often run on different days), so without this every source's own day-1 slot lands
    on the same calendar date (whichever is the next business day from wherever that
    source starts counting), stacking several tasks from different sources onto one day
    even though each source's own schedule looks properly spaced in isolation. Callers
    (app/services.py) pass in every date the site already has a task on from OTHER
    sources, so a newly (re)scheduled source's tasks land on genuinely free days instead.
    """
    excluded = excluded_dates or frozenset()
    ordered = sorted(tasks, key=_priority_key)
    technical = [t for t in ordered if _tier(t) < CONTENT_TIER_START]
    content_growth = [t for t in ordered if _tier(t) >= CONTENT_TIER_START]

    slots_per_day = max(1, round(capacity_per_week / 5))
    gate_date = add_months(campaign_start_date.replace(day=1), 1) if site_scale == "large" else None

    scheduled: list[ScheduledTask] = []
    day_index = 0
    for task in technical:
        target_date = _nth_business_day(campaign_start_date, day_index // slots_per_day, excluded)
        if gate_date and _tier(task) >= GATE_TIER and target_date < gate_date:
            while _nth_business_day(campaign_start_date, day_index // slots_per_day, excluded) < gate_date:
                day_index += 1
            target_date = _nth_business_day(campaign_start_date, day_index // slots_per_day, excluded)
        scheduled.append(ScheduledTask(task, _month_index(campaign_start_date, target_date), target_date))
        day_index += 1

    if content_growth:
        last_technical_date = scheduled[-1].target_date if scheduled else campaign_start_date - dt.timedelta(days=1)
        spread_start = max(campaign_start_date, last_technical_date + dt.timedelta(days=1))
        campaign_last_day = add_months(campaign_start_date.replace(day=1), duration_months) - dt.timedelta(days=1)
        available_days = [d for d in _business_days_in_range(spread_start, campaign_last_day) if d not in excluded]
        for task, target_date in zip(content_growth, _spread(len(content_growth), available_days)):
            scheduled.append(ScheduledTask(task, _month_index(campaign_start_date, target_date), target_date))

    return scheduled
