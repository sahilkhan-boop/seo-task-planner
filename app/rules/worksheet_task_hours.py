"""Task/best-practice worksheet Sahil brainstormed (2026-09-06/07,
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
everything Sahil brainstormed as "could this go wrong in 6 months," most of
which nothing in the codebase evaluates automatically yet -- that gap is real
and unrelated to hours; see ONBOARDING.md's SEO Brain section.

WORKSHEET_TASK_HOURS values are None until Sahil supplies a real number -- see
missing_worksheet_hours() below. None falls back to DEFAULT_TASK_HOURS at
lookup time (hours_for_worksheet_task), same fallback pattern as task_hours.py,
so an unset task never blocks scheduling, just gets a conservative 1-hour guess
until corrected.
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

# {slug: (display name, discipline, hours or None)} -- hours is None until Sahil
# fills it in on the worksheet; see missing_worksheet_hours().
WORKSHEET_TASKS: dict[str, dict] = {
    "broken_internal_links_404s": {"name": "Broken internal links (404s)", "discipline": "Technical - Crawl & Index", "hours": 40.0},
    "redirect_chains_loops": {"name": "Redirect chains & loops", "discipline": "Technical - Crawl & Index", "hours": None},
    "robots_txt_audit": {"name": "Robots.txt audit", "discipline": "Technical - Crawl & Index", "hours": None},
    "xml_sitemap_audit": {"name": "XML sitemap audit", "discipline": "Technical - Crawl & Index", "hours": None},
    "orphan_page_discovery": {"name": "Orphan page discovery", "discipline": "Technical - Crawl & Index", "hours": None},
    "crawl_budget_waste_audit": {"name": "Crawl budget waste audit", "discipline": "Technical - Crawl & Index", "hours": None},
    "canonical_tag_audit": {"name": "Canonical tag audit", "discipline": "Technical - Crawl & Index", "hours": None},
    "google_chosen_canonical_mismatches": {"name": "Google-chosen canonical mismatches", "discipline": "Technical - Crawl & Index", "hours": None},
    "pagination_handling": {"name": "Pagination handling", "discipline": "Technical - Crawl & Index", "hours": None},
    "js_rendering_content_check": {"name": "JS-rendering content check", "discipline": "Technical - Crawl & Index", "hours": None},
    "noindex_tag_audit": {"name": "Noindex tag audit", "discipline": "Technical - Crawl & Index", "hours": None},
    "soft_404_audit": {"name": "Soft-404 audit", "discipline": "Technical - Crawl & Index", "hours": None},
    "401_403_blocking_audit": {"name": "401/403 blocking audit", "discipline": "Technical - Crawl & Index", "hours": None},
    "http_https_www_consistency": {"name": "HTTP/HTTPS & www consistency", "discipline": "Technical - Crawl & Index", "hours": None},
    "trailing_slash_consistency": {"name": "Trailing-slash consistency", "discipline": "Technical - Crawl & Index", "hours": None},
    "hreflang_audit_if_multi_region_language": {"name": "Hreflang audit (if multi-region/language)", "discipline": "Technical - Crawl & Index", "hours": None},
    "log_file_crawl_analysis": {"name": "Log-file crawl analysis", "discipline": "Technical - Crawl & Index", "hours": None},
    "removals_tool_audit": {"name": "Removals-tool audit", "discipline": "Technical - Crawl & Index", "hours": None},
    "title_tag_audit": {"name": "Title tag audit", "discipline": "On-Page SEO", "hours": None},
    "meta_description_audit": {"name": "Meta description audit", "discipline": "On-Page SEO", "hours": None},
    "heading_hierarchy_audit": {"name": "Heading hierarchy audit", "discipline": "On-Page SEO", "hours": None},
    "url_structure_optimization": {"name": "URL structure optimization", "discipline": "On-Page SEO", "hours": None},
    "image_alt_text_audit": {"name": "Image alt text audit", "discipline": "On-Page SEO", "hours": None},
    "image_file_optimization": {"name": "Image file optimization", "discipline": "On-Page SEO", "hours": None},
    "keyword_cannibalization_audit": {"name": "Keyword cannibalization audit", "discipline": "On-Page SEO", "hours": None},
    "duplicate_content_audit": {"name": "Duplicate content audit", "discipline": "On-Page SEO", "hours": None},
    "thin_content_audit": {"name": "Thin content audit", "discipline": "On-Page SEO", "hours": None},
    "content_readability_review": {"name": "Content readability review", "discipline": "On-Page SEO", "hours": None},
    "e_e_a_t_signal_review": {"name": "E-E-A-T signal review", "discipline": "On-Page SEO", "hours": None},
    "content_gap_analysis": {"name": "Content gap analysis", "discipline": "Content Strategy", "hours": None},
    "content_pruning": {"name": "Content pruning", "discipline": "Content Strategy", "hours": None},
    "content_freshness_refresh_cadence": {"name": "Content freshness / refresh cadence", "discipline": "Content Strategy", "hours": None},
    "topic_cluster_pillar_mapping": {"name": "Topic cluster / pillar mapping", "discipline": "Content Strategy", "hours": None},
    "featured_snippet_paa_targeting": {"name": "Featured-snippet / PAA targeting", "discipline": "Content Strategy", "hours": None},
    "largest_contentful_paint_lcp": {"name": "Largest Contentful Paint (LCP)", "discipline": "Performance", "hours": None},
    "cumulative_layout_shift_cls": {"name": "Cumulative Layout Shift (CLS)", "discipline": "Performance", "hours": None},
    "interaction_to_next_paint_inp": {"name": "Interaction to Next Paint (INP)", "discipline": "Performance", "hours": None},
    "render_blocking_resource_audit": {"name": "Render-blocking resource audit", "discipline": "Performance", "hours": None},
    "third_party_script_audit": {"name": "Third-party script audit", "discipline": "Performance", "hours": None},
    "caching_cdn_configuration_review": {"name": "Caching & CDN configuration review", "discipline": "Performance", "hours": None},
    "mobile_usability_audit": {"name": "Mobile usability audit", "discipline": "Mobile & UX", "hours": None},
    "intrusive_interstitial_audit": {"name": "Intrusive interstitial audit", "discipline": "Mobile & UX", "hours": None},
    "core_ux_friction_review": {"name": "Core UX friction review", "discipline": "Mobile & UX", "hours": None},
    "toxic_backlink_audit": {"name": "Toxic backlink audit", "discipline": "Off-Page / Authority", "hours": None},
    "broken_backlink_reclamation": {"name": "Broken backlink reclamation", "discipline": "Off-Page / Authority", "hours": None},
    "competitor_backlink_gap_analysis": {"name": "Competitor backlink gap analysis", "discipline": "Off-Page / Authority", "hours": None},
    "unlinked_brand_mention_audit": {"name": "Unlinked brand mention audit", "discipline": "Off-Page / Authority", "hours": None},
    "digital_pr_link_building_campaign": {"name": "Digital PR / link-building campaign", "discipline": "Off-Page / Authority", "hours": None},
    "google_business_profile_optimization": {"name": "Google Business Profile optimization", "discipline": "Local SEO (if applicable)", "hours": None},
    "nap_consistency_audit": {"name": "NAP consistency audit", "discipline": "Local SEO (if applicable)", "hours": None},
    "local_citation_building": {"name": "Local citation building", "discipline": "Local SEO (if applicable)", "hours": None},
    "review_management_strategy": {"name": "Review management strategy", "discipline": "Local SEO (if applicable)", "hours": None},
    "ga4_event_conversion_audit": {"name": "GA4 event/conversion audit", "discipline": "Analytics & Tracking", "hours": None},
    "gsc_property_verification_audit": {"name": "GSC property verification audit", "discipline": "Analytics & Tracking", "hours": None},
    "utm_parameter_consistency": {"name": "UTM parameter consistency", "discipline": "Analytics & Tracking", "hours": None},
    "cross_domain_tracking_setup": {"name": "Cross-domain tracking setup", "discipline": "Analytics & Tracking", "hours": None},
    "consent_mode_cookie_banner_impact_review": {"name": "Consent-mode / cookie-banner impact review", "discipline": "Analytics & Tracking", "hours": None},
    "https_ssl_audit": {"name": "HTTPS/SSL audit", "discipline": "Security & Compliance", "hours": None},
    "accessibility_ada_wcag_review": {"name": "Accessibility (ADA/WCAG) review", "discipline": "Security & Compliance", "hours": None},
    "llm_citation_tracking": {"name": "LLM citation tracking", "discipline": "AI Search / GEO", "hours": None},
    "structured_quotable_answer_formatting": {"name": "Structured quotable-answer formatting", "discipline": "AI Search / GEO", "hours": None},
    "entity_knowledge_graph_alignment": {"name": "Entity / knowledge-graph alignment", "discipline": "AI Search / GEO", "hours": None},
    "algorithm_update_impact_analysis": {"name": "Algorithm-update impact analysis", "discipline": "Strategic / Reporting", "hours": None},
    "competitive_positioning_report": {"name": "Competitive positioning report", "discipline": "Strategic / Reporting", "hours": None},
    "roi_conversion_value_reporting": {"name": "ROI / conversion-value reporting", "discipline": "Strategic / Reporting", "hours": None},
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
    """Every worksheet task that still needs a real hours number from Sahil,
    grouped implicitly by discipline (dict insertion order matches the CSV's
    own discipline grouping). Used to print the outstanding list -- see
    scripts/ or the chat agent's own reference to this.
    """
    return [
        {"slug": slug, **entry}
        for slug, entry in WORKSHEET_TASKS.items()
        if entry["hours"] is None
    ]

