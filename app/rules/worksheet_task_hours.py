"""Task/best-practice worksheet Sahil filled in (2026-09-06 through 09-15,
originally `task_hours_worksheet_v2.csv`) -- a much broader vocabulary of
possible SEO/GEO work than the ~27 categories the current rule engines
(crawl_rules/gsc_rules/ga4_rules) actually generate tasks for. Feeds the chat
agent's `plan_tasks_for_urls` tool (app/ai/chat_tools.py): an analyst can hand
it real URLs + which of these checks each one needs, and it creates real Task
rows the existing 8-hour-day capacity scheduler (services.reschedule_all_tasks)
then places on the calendar -- same engine, same priority phases, just fed by
this table instead of by an automated GSC/GA4/crawl sync.

Distinct from app/rules/task_hours.py's HOURS_BY_CATEGORY: that one is keyed by
internal category slug and covers only what the rule engines already generate;
this one is keyed by the worksheet's own human task name (slugified) and covers
everything Sahil brainstormed as "could this go wrong in 6 months."

IMPORTANT -- these hours are SITE-WIDE, ONE-TIME totals (confirmed with Sahil
2026-09-15), not a per-URL figure: "Canonical tag audit" = 45 means 45 hours to
audit canonical tags across the WHOLE site once, covering however many URLs that
takes -- not 45h multiplied per URL. This is why plan_tasks_for_urls groups every
URL that needs the same check into ONE task (affected_urls = all of them) instead
of creating one task per (url, check) pair -- multiplying this value per URL would
be wildly wrong (10 URLs needing a 45h audit is still one 45h audit, not 450h).

WORKSHEET_TASK_HOURS values are None only for a task Sahil hasn't supplied a real
number for yet (none remain as of the last update, kept for forward-compat --
see missing_worksheet_hours()). None falls back to DEFAULT_TASK_HOURS at lookup
time, same fallback pattern as task_hours.py.
"""
from __future__ import annotations

from app.rules.task_hours import DEFAULT_TASK_HOURS

# discipline -> default optimization_level for a task created from that
# discipline's rows (same analyst-facing phase framing as optimization_levels.py).
DISCIPLINE_OPTIMIZATION_LEVEL = {
    "Technical - Crawl & Index": "key_fix",
    "On-Page SEO": "quick_win",
    "Content Strategy": "ongoing_content",
    "Performance": "key_fix",
    "Mobile & UX": "key_fix",
    "Off-Page / Authority": "ongoing_content",
    "Local SEO (if applicable)": "ongoing_content",
    "Analytics & Tracking": "key_fix",
    "Security & Compliance": "key_fix",
    "AI Search / GEO": "ongoing_content",
    "Strategic / Reporting": "reporting",
}

# {slug: {name, discipline, hours}} -- hours is a SITE-WIDE ONE-TIME total (see
# module docstring), None only for a task with no real number supplied yet.
WORKSHEET_TASKS: dict[str, dict] = {
    "broken_internal_links_404s": {"name": "Broken internal links (404s)", "discipline": "Technical - Crawl & Index", "hours": 40.0},
    "redirect_chains_loops": {"name": "Redirect chains & loops", "discipline": "Technical - Crawl & Index", "hours": 45.0},
    "robots_txt_audit": {"name": "Robots.txt audit", "discipline": "Technical - Crawl & Index", "hours": 30.0},
    "xml_sitemap_audit": {"name": "XML sitemap audit", "discipline": "Technical - Crawl & Index", "hours": 30.0},
    "orphan_page_discovery": {"name": "Orphan page discovery", "discipline": "Technical - Crawl & Index", "hours": 45.0},
    "crawl_budget_waste_audit": {"name": "Crawl budget waste audit", "discipline": "Technical - Crawl & Index", "hours": 45.0},
    "canonical_tag_audit": {"name": "Canonical tag audit", "discipline": "Technical - Crawl & Index", "hours": 45.0},
    "google_chosen_canonical_mismatches": {"name": "Google-chosen canonical mismatches", "discipline": "Technical - Crawl & Index", "hours": 45.0},
    "pagination_handling": {"name": "Pagination handling", "discipline": "Technical - Crawl & Index", "hours": 45.0},
    "js_rendering_content_check": {"name": "JS-rendering content check", "discipline": "Technical - Crawl & Index", "hours": 60.0},
    "noindex_tag_audit": {"name": "Noindex tag audit", "discipline": "Technical - Crawl & Index", "hours": 60.0},
    "soft_404_audit": {"name": "Soft-404 audit", "discipline": "Technical - Crawl & Index", "hours": 60.0},
    "401_403_blocking_audit": {"name": "401/403 blocking audit", "discipline": "Technical - Crawl & Index", "hours": 60.0},
    "http_https_www_consistency": {"name": "HTTP/HTTPS & www consistency", "discipline": "Technical - Crawl & Index", "hours": 60.0},
    "trailing_slash_consistency": {"name": "Trailing-slash consistency", "discipline": "Technical - Crawl & Index", "hours": 60.0},
    "hreflang_audit_if_multi_region_language": {"name": "Hreflang audit (if multi-region/language)", "discipline": "Technical - Crawl & Index", "hours": 60.0},
    "log_file_crawl_analysis": {"name": "Log-file crawl analysis", "discipline": "Technical - Crawl & Index", "hours": 60.0},
    "removals_tool_audit": {"name": "Removals-tool audit", "discipline": "Technical - Crawl & Index", "hours": 60.0},
    "title_tag_audit": {"name": "Title tag audit", "discipline": "On-Page SEO", "hours": 45.0},
    "meta_description_audit": {"name": "Meta description audit", "discipline": "On-Page SEO", "hours": 45.0},
    "heading_hierarchy_audit": {"name": "Heading hierarchy audit", "discipline": "On-Page SEO", "hours": 45.0},
    "url_structure_optimization": {"name": "URL structure optimization", "discipline": "On-Page SEO", "hours": 45.0},
    "image_alt_text_audit": {"name": "Image alt text audit", "discipline": "On-Page SEO", "hours": 45.0},
    "image_file_optimization": {"name": "Image file optimization", "discipline": "On-Page SEO", "hours": 45.0},
    "keyword_cannibalization_audit": {"name": "Keyword cannibalization audit", "discipline": "On-Page SEO", "hours": 45.0},
    "duplicate_content_audit": {"name": "Duplicate content audit", "discipline": "On-Page SEO", "hours": 45.0},
    "thin_content_audit": {"name": "Thin content audit", "discipline": "On-Page SEO", "hours": 60.0},
    "content_readability_review": {"name": "Content readability review", "discipline": "On-Page SEO", "hours": 60.0},
    "e_e_a_t_signal_review": {"name": "E-E-A-T signal review", "discipline": "On-Page SEO", "hours": 60.0},
    "content_gap_analysis": {"name": "Content gap analysis", "discipline": "Content Strategy", "hours": 60.0},
    "content_pruning": {"name": "Content pruning", "discipline": "Content Strategy", "hours": 60.0},
    "content_freshness_refresh_cadence": {"name": "Content freshness / refresh cadence", "discipline": "Content Strategy", "hours": 60.0},
    "topic_cluster_pillar_mapping": {"name": "Topic cluster / pillar mapping", "discipline": "Content Strategy", "hours": 60.0},
    "featured_snippet_paa_targeting": {"name": "Featured-snippet / PAA targeting", "discipline": "Content Strategy", "hours": 60.0},
    "largest_contentful_paint_lcp": {"name": "Largest Contentful Paint (LCP)", "discipline": "Performance", "hours": 60.0},
    "cumulative_layout_shift_cls": {"name": "Cumulative Layout Shift (CLS)", "discipline": "Performance", "hours": 60.0},
    "interaction_to_next_paint_inp": {"name": "Interaction to Next Paint (INP)", "discipline": "Performance", "hours": 60.0},
    "render_blocking_resource_audit": {"name": "Render-blocking resource audit", "discipline": "Performance", "hours": 60.0},
    "third_party_script_audit": {"name": "Third-party script audit", "discipline": "Performance", "hours": 60.0},
    "caching_cdn_configuration_review": {"name": "Caching & CDN configuration review", "discipline": "Performance", "hours": 60.0},
    "mobile_usability_audit": {"name": "Mobile usability audit", "discipline": "Mobile & UX", "hours": 60.0},
    "intrusive_interstitial_audit": {"name": "Intrusive interstitial audit", "discipline": "Mobile & UX", "hours": 60.0},
    "core_ux_friction_review": {"name": "Core UX friction review", "discipline": "Mobile & UX", "hours": 60.0},
    "toxic_backlink_audit": {"name": "Toxic backlink audit", "discipline": "Off-Page / Authority", "hours": 60.0},
    "broken_backlink_reclamation": {"name": "Broken backlink reclamation", "discipline": "Off-Page / Authority", "hours": 60.0},
    "competitor_backlink_gap_analysis": {"name": "Competitor backlink gap analysis", "discipline": "Off-Page / Authority", "hours": 60.0},
    "unlinked_brand_mention_audit": {"name": "Unlinked brand mention audit", "discipline": "Off-Page / Authority", "hours": 60.0},
    "digital_pr_link_building_campaign": {"name": "Digital PR / link-building campaign", "discipline": "Off-Page / Authority", "hours": 60.0},
    "google_business_profile_optimization": {"name": "Google Business Profile optimization", "discipline": "Local SEO (if applicable)", "hours": 60.0},
    "nap_consistency_audit": {"name": "NAP consistency audit", "discipline": "Local SEO (if applicable)", "hours": 60.0},
    "local_citation_building": {"name": "Local citation building", "discipline": "Local SEO (if applicable)", "hours": 60.0},
    "review_management_strategy": {"name": "Review management strategy", "discipline": "Local SEO (if applicable)", "hours": 60.0},
    "ga4_event_conversion_audit": {"name": "GA4 event/conversion audit", "discipline": "Analytics & Tracking", "hours": 60.0},
    "gsc_property_verification_audit": {"name": "GSC property verification audit", "discipline": "Analytics & Tracking", "hours": 60.0},
    "utm_parameter_consistency": {"name": "UTM parameter consistency", "discipline": "Analytics & Tracking", "hours": 60.0},
    "cross_domain_tracking_setup": {"name": "Cross-domain tracking setup", "discipline": "Analytics & Tracking", "hours": 60.0},
    "consent_mode_cookie_banner_impact_review": {"name": "Consent-mode / cookie-banner impact review", "discipline": "Analytics & Tracking", "hours": 60.0},
    "https_ssl_audit": {"name": "HTTPS/SSL audit", "discipline": "Security & Compliance", "hours": 60.0},
    "accessibility_ada_wcag_review": {"name": "Accessibility (ADA/WCAG) review", "discipline": "Security & Compliance", "hours": 60.0},
    "llm_citation_tracking": {"name": "LLM citation tracking", "discipline": "AI Search / GEO", "hours": 60.0},
    "structured_quotable_answer_formatting": {"name": "Structured quotable-answer formatting", "discipline": "AI Search / GEO", "hours": 60.0},
    "entity_knowledge_graph_alignment": {"name": "Entity / knowledge-graph alignment", "discipline": "AI Search / GEO", "hours": 60.0},
    "algorithm_update_impact_analysis": {"name": "Algorithm-update impact analysis", "discipline": "Strategic / Reporting", "hours": 60.0},
    "competitive_positioning_report": {"name": "Competitive positioning report", "discipline": "Strategic / Reporting", "hours": 60.0},
    "roi_conversion_value_reporting": {"name": "ROI / conversion-value reporting", "discipline": "Strategic / Reporting", "hours": 60.0},
}


def hours_for_worksheet_task(slug: str) -> float:
    entry = WORKSHEET_TASKS.get(slug)
    if entry is None or entry["hours"] is None:
        return DEFAULT_TASK_HOURS
    return entry["hours"]


def optimization_level_for_worksheet_task(slug: str) -> str | None:
    entry = WORKSHEET_TASKS.get(slug)
    if entry is None:
        return None
    return DISCIPLINE_OPTIMIZATION_LEVEL.get(entry["discipline"])


def missing_worksheet_hours() -> list[dict]:
    """Every worksheet task that still needs a real hours number from Sahil."""
    return [
        {"slug": slug, **entry}
        for slug, entry in WORKSHEET_TASKS.items()
        if entry["hours"] is None
    ]

