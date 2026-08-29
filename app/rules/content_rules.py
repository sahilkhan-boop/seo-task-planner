"""Recurring content-creation + page-optimization plan.

Crawl-issue tasks schedule sequentially from the campaign start date,
earliest-first by priority -- with only a handful of issues, they all land in
month 1. Content/growth work is different: it's recurring. Every calendar
month of the campaign gets its own quota of "create a new page" and
"optimize an existing page" tasks (sized from the package the client bought),
spread evenly across that month's business days. This is what fills months
2-6 with real, actionable work instead of leaving them empty until GSC/GA4
sync exists to drive data-based tasks.

No system-enforced cap on pieces/month -- package size is the analyst/project
owner's call to make per engagement, not a fixed platform rule. (An earlier
version of this file capped both at 2/month; removed since that judgment
belongs to the person planning the campaign, not the code.)
"""
from __future__ import annotations

import calendar
import datetime as dt
from dataclasses import dataclass, field

from app.scheduling.month_utils import add_months

# Prompt Analysis & Keyword Research (the one-off Benchmarking task -- see
# generate_benchmarking_task) is deliberately manual: no rule engine ingests its
# findings, so it can't automatically hand specific pages/topics/platforms to the
# tasks below. Instead, every task whose selection SHOULD be informed by it carries
# this instruction in its own description, as a standing reminder to check back
# against whatever the analyst found there before defaulting to generic research.
def prompt_analysis_cross_reference(subject: str = "pages/topics") -> str:
    return (
        f"Cross-check against the Prompt Analysis & Keyword Research (Benchmarking) findings first -- "
        f"prioritize the specific {subject} surfaced there before falling back to generic research."
    )


# A handful of spare pages/topics on top of the configured monthly quota, in case
# the client passes on one or two of the picks -- so the analyst always has a
# ready backup in the same export instead of needing a follow-up request mid-
# month. Applied to BOTH page_optimization and wasted_impressions the same way:
# the month's slice is the base quota PLUS this buffer, and the ranking pointer
# advances by that same padded amount -- so the "never repeat a url" guarantee
# holds exactly as before, just consuming the ranked backlog a bit faster
# (fewer months of backlog headroom before hitting the generic placeholder).
# Deliberately not a per-campaign setting -- one more dropdown here risks going
# over people's heads for a small, low-stakes cushion.
PAGE_OPTIMIZATION_BUFFER = 2


@dataclass
class ContentPlanTask:
    category: str  # "content_creation" | "page_optimization" | "llm_optimization"
    title: str
    description: str
    month_index: int
    target_date: dt.date
    severity: str = "low"  # planned growth work, not an urgent fix -- kept visually distinct from issues
    effort_tier: str = "medium"
    source: str = "content_plan"
    affected_urls: list = field(default_factory=list)
    url_details: dict = field(default_factory=dict)


def _business_days_in_month(year: int, month: int) -> list[dt.date]:
    _, last_day = calendar.monthrange(year, month)
    return [d for d in (dt.date(year, month, day) for day in range(1, last_day + 1)) if d.weekday() < 5]


def _stage_dates(days: list[dt.date], n: int) -> list[dt.date]:
    """n distinct, increasing dates roughly evenly spread through `days` -- one per
    week of a real month's business days -- for that month's content-plan stages
    (research / brief / article / page-optimization), each covering the WHOLE
    month's package as one deliverable rather than one task per piece/page (see
    generate_content_plan). Falls back gracefully (never returns a date earlier
    than the previous stage) when a month has very few business days."""
    if n <= 0 or not days:
        return []
    picks: list[dt.date] = []
    last_idx = -1
    for i in range(n):
        idx = min(len(days) - 1, max(last_idx + 1, (i * len(days)) // n))
        picks.append(days[idx])
        last_idx = idx
    return picks


def _spread(n: int, days: list[dt.date]) -> list[dt.date]:
    """Pick n dates evenly spread across `days`. If n exceeds the days available, stack the overflow onto the tail."""
    if n <= 0 or not days:
        return []
    if n >= len(days):
        picks = list(days)
        i = 0
        while len(picks) < n:
            picks.append(days[i % len(days)])
            i += 1
        return picks[:n]
    step = len(days) / n
    return [days[int(i * step)] for i in range(n)]


def generate_content_plan(
    start_date: dt.date,
    duration_months: int,
    content_pieces_per_month: int | None,
    pages_to_optimize_per_month: int | None,
    page_work_mode: str = "optimize_existing",
    ranked_pages: list[dict] | None = None,
    ranked_wasted_pages: list[dict] | None = None,
) -> list[ContentPlanTask]:
    tasks: list[ContentPlanTask] = []
    first_of_start_month = start_date.replace(day=1)
    content_n = content_pieces_per_month or 0
    optimize_n = pages_to_optimize_per_month or 0
    # Some campaigns have no existing content worth optimizing at all -- lets the
    # analyst redirect the same monthly count toward creating brand-new pages
    # instead (see Campaign.page_work_mode). Same count field, same one-task/month
    # consolidation, just a different category/title/description.
    creating_new_pages = page_work_mode == "create_new"

    for i in range(duration_months):
        month_start = add_months(first_of_start_month, i)
        days = _business_days_in_month(month_start.year, month_start.month)
        if i == 0:
            # The campaign can start mid-month (e.g. the 24th) -- only month 0 needs
            # this filter, since every later month is already entirely in the future
            # relative to start_date. Without it, content work for the start month
            # could land on days before the campaign (or the analyst's other work)
            # actually begins.
            days = [d for d in days if d >= start_date]
        month_label = month_start.strftime("%B %Y")

        # A real content team doesn't get a topic-to-published-article in one step --
        # it's topic research/approval, then a brief to approve, then the actual
        # writing -- and page optimization/wasted-impressions cleanup are their own
        # distinct deliverables alongside that pipeline, not instead of it. Modeled
        # as up to 5 tasks per month covering the WHOLE package each (not N x
        # content_n / N x optimize_n), one per week, so the client sees a distinct,
        # meaningfully different deliverable every week of the month regardless of
        # how many pieces/pages are in the package -- instead of one lump "do
        # everything" task, or N identical-looking "write piece 1/8, 2/8, ..." /
        # "optimize page 1/8, 2/8, ..." tasks, which reads exactly like the same
        # look-alike-task problem CAMPAIGN_TASK_BUDGET batching solves elsewhere
        # (see crawl_rules.py's batch_items).
        # wasted_impressions only makes sense alongside existing-page work -- no
        # existing-page backlog to draw from in "create_new" mode (see the
        # page_optimization/new_page_creation branch below).
        has_wasted_stage = optimize_n > 0 and not creating_new_pages
        stage_count = (3 if content_n > 0 else 0) + (1 if optimize_n > 0 else 0) + (1 if has_wasted_stage else 0)
        stage_dates = _stage_dates(days, stage_count)
        stage = iter(stage_dates)

        if content_n > 0 and stage_dates:
            tasks.append(
                ContentPlanTask(
                    category="content_topic_research",
                    title=f"Topic Research & Approval — {content_n} pieces — {month_label}",
                    description=(
                        f"{prompt_analysis_cross_reference()}\n\n"
                        f"Research and shortlist {content_n} topics for this month's content package "
                        "(keyword research, content-gap analysis, GSC query data once connected). Share "
                        "the shortlist for approval before briefs start."
                    ),
                    month_index=i,
                    target_date=next(stage),
                )
            )
            tasks.append(
                ContentPlanTask(
                    category="content_brief_finalization",
                    title=f"Content Brief Finalization & Approval — {content_n} pieces — {month_label}",
                    description=(
                        f"Turn this month's {content_n} approved topics into full briefs (target keyword, "
                        "search intent, outline, internal-linking plan) and get them approved before "
                        "writing starts."
                    ),
                    month_index=i,
                    target_date=next(stage),
                )
            )
            tasks.append(
                ContentPlanTask(
                    category="content_creation",
                    title=f"Article Creation & Publish — {content_n} pieces — {month_label}",
                    description=(
                        f"Write, publish, and internally link all {content_n} approved briefs from this "
                        "month's content package."
                    ),
                    month_index=i,
                    target_date=next(stage),
                )
            )

        # One consolidated task covering all N pages, not N separate look-alike
        # tickets -- same reasoning as the pipeline above (see the module-level
        # comment on this function).
        if optimize_n > 0 and stage_dates:
            if creating_new_pages:
                task = ContentPlanTask(
                    category="new_page_creation",
                    title=f"Create {optimize_n} New Pages — {month_label}",
                    description=(
                        f"Identify and create {optimize_n} new pages this site doesn't have yet -- keyword-gap "
                        "and competitor-gap opportunities, industry topics with real search demand, or "
                        "whatever custom sourcing logic fits this site (no existing-page backlog to draw from "
                        "here). Write, publish, and internally link each."
                    ),
                    month_index=i,
                    target_date=next(stage),
                )
            else:
                # ranked_pages (from the site's own real GSC data, highest-impressions
                # first -- see services.py) is sliced into consecutive, non-overlapping
                # chunks, one chunk per month: month 0 gets the single biggest-
                # opportunity pages, month 1 gets the next tier down, and so on --
                # gradually working down the ranking instead of re-suggesting the
                # same top pages every month. Each chunk is optimize_n PLUS
                # PAGE_OPTIMIZATION_BUFFER spares (see its own comment) -- the
                # pointer below advances by that same padded size, so there's still
                # zero overlap between months. No exclusion of pages already claimed
                # by a different check (meta_tag_reoptimization, wasted_impressions,
                # ...) -- that's a different kind of work (title/meta copy vs.
                # content depth/on-page SEO/internal linking) on the same URL, not a
                # duplicate.
                batch_size = optimize_n + PAGE_OPTIMIZATION_BUFFER
                month_pages = ranked_pages[i * batch_size:(i + 1) * batch_size] if ranked_pages else []
                if month_pages:
                    task = ContentPlanTask(
                        category="page_optimization",
                        title=f"Optimize {len(month_pages)} Existing Pages — {month_label}",
                        description=(
                            f"This month's {len(month_pages)} highest-opportunity existing pages ({optimize_n} "
                            f"planned + up to {PAGE_OPTIMIZATION_BUFFER} backups in case the client passes on "
                            "any), ranked by impressions (largest first, picking up where last month's batch "
                            "left off) -- improve content depth, on-page SEO, and internal linking on each to "
                            "grow their organic traffic. Export this task for the full list with each page's "
                            "real impressions/CTR/position."
                        ),
                        month_index=i,
                        target_date=next(stage),
                        affected_urls=[p["page"] for p in month_pages],
                        url_details={
                            p["page"]: {
                                "clicks": p["clicks"], "impressions": p["impressions"],
                                "ctr": p["ctr"], "position": p["position"],
                            }
                            for p in month_pages
                        },
                    )
                else:
                    # No real GSC data to rank by (not connected yet, or this month's
                    # slice ran past the end of the ranked list) -- same generic
                    # placeholder as always, not a fabricated page list.
                    task = ContentPlanTask(
                        category="page_optimization",
                        title=f"Optimize {optimize_n} Existing Pages — {month_label}",
                        description=(
                            f"Pick the {optimize_n} highest-opportunity existing pages (impressions but weak "
                            "CTR/rankings, from GSC once connected, or current analytics) and improve content "
                            "depth, on-page SEO, and internal linking on each to grow their organic traffic."
                        ),
                        month_index=i,
                        target_date=next(stage),
                    )
            tasks.append(task)

        # Same monthly, ranked, non-repeating pacing as page_optimization just above
        # -- see services.py's _ranked_wasted_impression_pages -- but a distinct
        # category/problem: pages with real search visibility converting to
        # essentially no clicks (title/meta mismatch, wrong intent, a rich
        # result/AI Overview intercepting the click), not a general "make this page
        # better" pass. Used to be a live-per-sync GSC check (gsc_rules.py); moved
        # here so it can be paced out gradually across months instead of dumping
        # every qualifying page into one task the moment a sync runs. No exclusion
        # against page_optimization's own picks -- same URL, different kind of fix,
        # both worth doing (see the comment on the branch above).
        if has_wasted_stage and stage_dates:
            batch_size = optimize_n + PAGE_OPTIMIZATION_BUFFER
            month_wasted = ranked_wasted_pages[i * batch_size:(i + 1) * batch_size] if ranked_wasted_pages else []
            if month_wasted:
                wasted_task = ContentPlanTask(
                    category="wasted_impressions",
                    title=f"Fix {len(month_wasted)} High-Impression, Low-CTR Pages — {month_label}",
                    description=(
                        f"This month's {len(month_wasted)} pages ({optimize_n} planned + up to "
                        f"{PAGE_OPTIMIZATION_BUFFER} backups in case the client passes on any) getting real "
                        "search impressions but almost no clicks -- rewrite titles/meta descriptions and check "
                        "for intent mismatch on each to recover the wasted visibility. Ranked by impressions "
                        "(largest first, picking up where last month's batch left off). Export this task for "
                        "the full list with each page's real impressions/CTR/position."
                    ),
                    month_index=i,
                    target_date=next(stage),
                    affected_urls=[p["page"] for p in month_wasted],
                    url_details={
                        p["page"]: {
                            "clicks": p["clicks"], "impressions": p["impressions"],
                            "ctr": p["ctr"], "position": p["position"],
                        }
                        for p in month_wasted
                    },
                )
            else:
                # No real GSC data to filter by (not connected yet, no Benchmarks-page
                # "high_impression_wasted" segment configured, or this month's slice
                # ran past the end of the list) -- generic placeholder, not a
                # fabricated page list.
                wasted_task = ContentPlanTask(
                    category="wasted_impressions",
                    title=f"Fix High-Impression, Low-CTR Pages — {month_label}",
                    description=(
                        "Identify pages getting real search impressions but almost no clicks (from GSC once "
                        "connected) and rewrite titles/meta descriptions, checking for intent mismatch, to "
                        "recover the wasted visibility."
                    ),
                    month_index=i,
                    target_date=next(stage),
                )
            tasks.append(wasted_task)

        # Alongside content work, not instead of it -- only generated in a month that
        # actually has content_creation/page_optimization work happening, and placed on
        # the last business day of the month so it doesn't collide with either of those
        # picks (both drawn from the same `days` list via _spread, which can otherwise
        # pick the same day this would).
        if (content_n or optimize_n) and days:
            tasks.append(
                ContentPlanTask(
                    category="llm_optimization",
                    title=f"Optimize for LLM/AI-search discovery — {month_label}",
                    description=(
                        "Adapt this month's content work for how LLMs/AI search surface answers, not just "
                        "how Google ranks pages: structured, directly-quotable answers on the site itself, "
                        "plus the same topics adapted for third-party/social platforms. "
                        f"{prompt_analysis_cross_reference('platforms')} If it identified "
                        "specific platforms real prompts/answers are actually being sourced from (LinkedIn, "
                        "Reddit, YouTube, forums, etc.), name them here explicitly and do the platform-specific "
                        "work -- don't default to a generic \"social\" placeholder. Same topics as this month's "
                        "content plan, different format/destination."
                    ),
                    month_index=i,
                    target_date=days[-1],
                )
            )

    return tasks


def generate_benchmarking_task(start_date: dt.date) -> ContentPlanTask:
    """A single, one-off kickoff task -- not per-month like the rest of this file.
    Prompt analysis and keyword research are the analyst's foundational research step,
    done once at the start of a campaign (week 1) before any fix work begins, not
    something with a monthly quota the way content pieces do. No rule engine drives
    this -- there's no data source for prompt/LLM query volume yet -- so it's a plain
    task the analyst fills in themselves, seeded so it's never forgotten. See
    services.py's ensure_benchmarking_task for why this is create-once, not
    regenerated on every re-import (an analyst's progress on it shouldn't reset)."""
    return ContentPlanTask(
        category="prompt_keyword_benchmarking",
        title="Prompt Analysis & Keyword Research (Benchmarking)",
        description=(
            "Foundational research before any fix work starts: keyword research (search volume, intent, "
            "gaps) plus prompt analysis (what questions/prompts real users and LLMs are actually asking in "
            "this space, and how the site currently shows up -- or doesn't -- in those answers). This sets "
            "the benchmark the rest of the plan's Key Fix / Quick Win / Ongoing Content work gets judged "
            "against."
        ),
        month_index=0,
        target_date=start_date,
        effort_tier="high",
    )
