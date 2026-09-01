# SEO Task-Plan Platform

Turns crawl data (and, in later phases, GSC/GA4 performance data) into a
**prioritized, calendar-mapped task plan** for an SEO analyst — not just a
list of errors. See `docs/plan.md`-equivalent context in the original design
doc for the full architecture; this README covers what's built and how to run it.

## Status: Phases 1-4 complete (crawl pipeline + GSC integration + GA4 integration)

Working end-to-end right now:
- Import a Screaming Frog crawl export folder (Response Codes, All Inlinks, Redirect Chains CSVs)
- Auto-generate tasks for 404s, 3xx redirects, and 5xx errors — each task names the
  **exact internal pages linking to the broken/redirected URL**, so the analyst knows
  precisely which inlinks to update
- Auto-schedule every task onto the campaign calendar (month + business day), technical
  fixes first, based on campaign start date and analyst weekly capacity
- **Live GSC integration**: Google OAuth connect flow (`app/google_oauth.py`,
  `app/routers/google_auth.py`), a real Search Analytics API sync — both per-page
  and per-(page, query) — (`app/ingestion/gsc_sync.py`), and a three-tier existing-page-
  optimization rule engine (`app/rules/gsc_rules.py`, see "Content task prioritization"
  below) — triggered from `POST /sites/{id}/gsc/sync`
- **Live GA4 integration**: same OAuth pattern, a real Analytics Data API sync for
  per-page sessions/users/engagement/bounce/key-events plus a device-category
  breakdown for mobile share (`app/ingestion/ga4_sync.py`), and a rule engine
  (`app/rules/ga4_rules.py`) covering UI/UX review (gated on real traffic), exit
  rate, mobile share, and key events — triggered from `POST /sites/{id}/ga4/sync`
- **Plan chat**: an agentic Claude loop (`app/ai/chat_agent.py`, `chat_tools.py`)
  that can list/create/update/delete tasks from plain-English instructions,
  scoped server-side to the current site
- **Calendar exports**: PDF (`app/exports/pdf_calendar.py`) and Excel
  (`app/exports/excel_calendar.py`) calendar-grid exports, severity color-coded
- Dashboard: guided setup wizard (Connect → Campaign & package), benchmark config,
  crawl import trigger + history, task board (grouped by month, filterable, CSV export),
  calendar view

## Campaign workflow (approved — see workflow.pdf, regenerate with `python scripts/generate_workflow_pdf.py`)

Month 1 = technical, Month 2 = content, Month 3+ = customizable — but "Month 1 = fix
everything" only holds for small sites. Every crawl import classifies **site scale**
from total crawled URLs (`small` <1,000, `medium` <50,000, `large` beyond that) and
the scheduler adapts:

- **Priority tiers** (`app/scheduling/timeline.py`'s `CATEGORY_TIER`; workflow.pdf p3
  covers the technical tiers specifically): indexation-blocking issues at scale →
  server errors → high-impact 404s → redirect/inlink cleanup → low-impact 404s →
  **meta tag reoptimization → content expansion → UI/UX review → CTR/ranking catch-all
  → remaining GA4 checks** (the last five are the content-task prioritization below).
  Indexation-blocking (pages noindexed/blocked by robots.txt at volume) is detected
  from the Response Codes export's Indexability Status column and always summarized
  as **one investigative task per reason**, never one task per URL — at real scale
  that's the only way it stays actionable instead of generating thousands of rows.
- **Large-site gating**: redirect cleanup and everything after it (tier 3+, which
  includes every content/GA4 tier below) is held back to month index 1+ so month 1
  stays focused on indexation-blockers/server-errors/high-impact-404s only, even
  with spare capacity. Small/medium sites aren't gated — medium sites' overflow
  naturally spills into month 2 through normal day-by-day scheduling; small sites
  fit everything in month 1 as-is.

## Content task prioritization (which existing page to optimize first)

Once technical debt is cleared, the question becomes *which page to work on next*
— not just "some CTR is low somewhere." Existing-page-optimization tasks are
picked in three tiers, ordered by effort vs. impact so the analyst always has an
unambiguous next action:

1. **Meta tag reoptimization** (easiest, first) — the page already ranks position
   1-5 (the hard part — reaching page 1 — is done) but CTR is below the position-1-5
   benchmark. Fix is copy-only: rewrite the title tag/meta description. No content
   work, fastest lift.
2. **Content expansion** — the page ranks position 5-15 *and* has real non-branded
   query demand it isn't capturing yet (pulled from GSC's page+query dimension,
   split branded/non-branded against the site's configured **Brand terms**, Setup →
   Connect → GSC). Fix is adding on-page content/sections that address those
   specific queries — more effort than tier 1, but still targeted at an existing,
   already-ranking page rather than a new one.
3. **CTR/ranking catch-all** (gradual) — pages beyond position 15 with meaningful
   impressions. Real search demand exists but ranking is too far back for a CTR fix
   to matter yet; broader on-page SEO, worked gradually once tiers 1-2 are handled.

Separately, **UI/UX review** (GA4-driven, not GSC) fires only for pages that are
*already working* traffic-wise — high sessions **and** a real number of active
users, not just noise — but still failing to engage once people land. That
combination is what actually points at a UI/UX or page-speed problem rather than
a content-fit problem, so it's gated tighter than the other three GA4 checks
(exit rate, mobile share, key events), which just use the base noise floor.

All of this is pure-function rule logic (`app/rules/gsc_rules.py`,
`app/rules/ga4_rules.py`) with its own test coverage — see `tests/test_gsc_rules.py`
and `tests/test_ga4_rules.py` for the exact tier boundaries and gating thresholds.

## Plan chat (AI-editable)

Every site has a **Chat** tab where you can talk to the plan in plain English —
"move the March content tasks to April", "mark the redirect fix as done", "add
a task to check the new product page." It's a real agentic loop (Claude, via
the Anthropic SDK) with tools to actually list/create/update/delete tasks —
not just a Q&A box. Every tool call is scoped to the current site_id
server-side (the model's own claims are never trusted for that), and every
reply that changed something shows a plain-English "✓ what changed" line.

Requires `ANTHROPIC_API_KEY` in `.env` (from console.anthropic.com → API Keys)
and a funded account (Plans & Billing → add credits) — the chat shows a clear
in-UI message rather than crashing if the key's missing or the account has no
credit balance.

## Using it

Adding a site now walks through a guided setup: **Connect platforms** (Screaming
Frog, GSC, GA4 — each with an honest "connected / not connected yet" status)
→ **Campaign & package details** (start date, duration, content pieces/month,
capacity, free-text notes). After that, the nav is just three items — Overview
(at-a-glance connections/package/stats), Task Plan, and Settings (everything
else: connections, campaign, benchmarks, crawl imports, one page each).

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run

```bash
source .venv/bin/activate
uvicorn app.main:app --reload
```

Then open http://localhost:8000 — add a site, set a campaign start date, seed
benchmark defaults, and import a crawl folder.

## Importing a Screaming Frog crawl

Run a headless crawl (requires a paid Screaming Frog license for sites over
500 URLs or for CLI/scheduled automation). This exact command was verified
against a real crawl (`--help`, `--help export-tabs`, `--help bulk-export`,
`--help save-report` all checked against the actual installed CLI) — the
three report flags are genuinely different CLI mechanisms, not all
`--bulk-export` as an earlier draft of this doc assumed:

```bash
# macOS default install path
"/Applications/Screaming Frog SEO Spider.app/Contents/MacOS/ScreamingFrogSEOSpiderLauncher" \
  --crawl https://yoursite.com --headless \
  --output-folder imports/yoursite.com/2026-08-15 \
  --export-tabs "Response Codes:All" \
  --bulk-export "Links:All Inlinks" \
  --save-report "Redirects:Redirect Chains" \
  --overwrite
```

Then, in the app's Crawl Imports page, point it at `yoursite.com/2026-08-15`
(path relative to `imports/`) and the crawl date. It'll parse the CSVs,
generate tasks, and schedule them automatically.

**Note on redirects:** Screaming Frog's Redirect Chains report only lists
*multi-hop* chains (2+ redirects) — a normal single-hop 301/302, the common
case, never appears there. The parser reads the immediate redirect target
from the Response Codes export's own "Redirect URL" column instead, and only
prefers the Redirect Chains report's Final Address when a real multi-hop
chain exists.

## Recurring content & growth plan (fills every month, not just month 1)

Crawl-issue tasks (404s, redirects) schedule earliest-first from the campaign
start date, so with only a handful of issues they all land in month 1 — by
design, technical debt comes first. To fill the *rest* of the campaign, set
**content pieces/month** and **existing pages to optimize/month** in Campaign
& Package (Settings), then click **(Re)generate content & growth plan** from
Task Plan or Calendar view. It creates that many "create new content" and
"optimize an existing page for traffic" tasks for *every* calendar month of
the campaign, spread evenly across business days. Regenerating after you
change the package size clears the old plan first — no duplicates.

## Calendar view + exports

From Task Plan, click **Calendar view** for a month-by-month grid (like a
desktop calendar app) spanning the full campaign duration — every task shown
on its scheduled date with project name and assignee. From there:
- **Download PDF** — one landscape page per month
- **Download Excel** — one sheet per month, same calendar-grid layout, cells
  color-coded by severity

Both are generated from the same `app/scheduling/calendar_grid.py` data so
the HTML view, PDF, and Excel always agree. Set a **default assignee** in
Campaign & Package (Settings) to pre-fill new tasks, and reassign any
individual task inline from the task list.

## Tests

```bash
source .venv/bin/activate
python -m pytest -q
```

98 tests cover the CSV parser, the rule engines (severity/category logic per
crawl issue type, per GSC content-optimization tier, and per GA4 check), the
scheduler (tier ordering, business-day placement, capacity), GSC + GA4 OAuth
and sync, the plan-chat tool layer, and a full pipeline integration test.

## Next phases

5. Refine the scheduling mapper and the content-tier thresholds (position bands,
   opportunity-impression cutoffs, UI/UX traffic gates) once GSC/GA4 task volume
   is real — everything in "Content task prioritization" above is tuned against
   plausible-but-synthetic data, not a live campaign yet
6. Non-branded query classification is a simple substring match against
   **Brand terms** (Setup → Connect → GSC) — fine to start, but worth revisiting
   with real query data; a site with no brand terms configured treats every
   query as non-branded, which is a reasonable default but not a substitute for
   actually setting them

## Project layout

```
app/
  models.py            # Site, Campaign, Connection, Benchmark, VolumeBenchmark,
                        # MetricSnapshot, SiteMetricDaily, CrawlImport, CrawlIssue, Task
  ingestion/screaming_frog.py   # CSV parsing
  ingestion/gsc_sync.py         # GSC Search Analytics REST calls (page, page/query, site-wide by date)
  ingestion/ga4_sync.py         # GA4 Analytics Data API REST calls (page metrics, device split, site-wide by date)
  rules/crawl_rules.py          # issue -> Task rule engine
  rules/gsc_rules.py            # 3-tier existing-page-optimization rule engine
  rules/ga4_rules.py            # UI/UX review, exit rate, mobile share, key events
  rules/volume_rules.py         # site-wide daily/weekly/monthly volume-trend benchmark checks
  scheduling/timeline.py        # Task -> month/day placement + priority tiers
  services.py                   # glue: import/sync -> rules -> schedule -> persist
  routers/                      # FastAPI routes
  templates/, static/           # Jinja2 dashboard UI
tests/                          # pytest + fixture Screaming Frog CSVs
imports/                        # drop Screaming Frog export folders here
```
