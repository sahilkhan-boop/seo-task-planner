"""GSC-report-style regex filters (Site.gsc_page_filter_regex/gsc_query_filter_regex/
ga4_page_filter_regex) -- optional, analyst-set scoping applied before the rule
engines ever see the data. Tested as pure functions against a plain Site instance,
no DB session needed."""
from app.models import Site
from app.services import apply_ga4_filters, apply_gsc_filters


def _site(**overrides) -> Site:
    defaults = dict(domain="example.com")
    defaults.update(overrides)
    return Site(**defaults)


PAGE_ROWS = [
    {"page": "https://example.com/products/a", "clicks": 1, "impressions": 1, "ctr": 0.1, "position": 1},
    {"page": "https://example.com/blog/b", "clicks": 1, "impressions": 1, "ctr": 0.1, "position": 1},
]
QUERY_ROWS = [
    {"page": "https://example.com/products/a", "query": "how to buy", "clicks": 1, "impressions": 1, "ctr": 0.1, "position": 1},
    {"page": "https://example.com/blog/b", "query": "what is x", "clicks": 1, "impressions": 1, "ctr": 0.1, "position": 1},
]


def test_no_filters_configured_returns_everything_unchanged():
    site = _site()
    pages, queries = apply_gsc_filters(site, PAGE_ROWS, QUERY_ROWS)
    assert pages == PAGE_ROWS
    assert queries == QUERY_ROWS


def test_page_include_filter_scopes_to_matching_pages_only():
    site = _site(gsc_page_filter_regex=r"^https://example\.com/products/", gsc_page_filter_mode="include")
    pages, queries = apply_gsc_filters(site, PAGE_ROWS, QUERY_ROWS)
    assert [p["page"] for p in pages] == ["https://example.com/products/a"]
    # query rows for the excluded page are dropped too -- a page-filter excludes its queries
    assert [q["page"] for q in queries] == ["https://example.com/products/a"]


def test_page_exclude_filter_drops_matching_pages():
    site = _site(gsc_page_filter_regex=r"/blog/", gsc_page_filter_mode="exclude")
    pages, _ = apply_gsc_filters(site, PAGE_ROWS, QUERY_ROWS)
    assert [p["page"] for p in pages] == ["https://example.com/products/a"]


def test_query_include_filter_scopes_queries_without_touching_pages():
    site = _site(gsc_query_filter_regex=r"^how to", gsc_query_filter_mode="include")
    pages, queries = apply_gsc_filters(site, PAGE_ROWS, QUERY_ROWS)
    assert pages == PAGE_ROWS  # page rows untouched by a query-only filter
    assert [q["query"] for q in queries] == ["how to buy"]


def test_query_exclude_filter():
    site = _site(gsc_query_filter_regex=r"^how to", gsc_query_filter_mode="exclude")
    _, queries = apply_gsc_filters(site, PAGE_ROWS, QUERY_ROWS)
    assert [q["query"] for q in queries] == ["what is x"]


def test_invalid_regex_is_ignored_not_a_crash():
    site = _site(gsc_page_filter_regex="(unclosed[", gsc_page_filter_mode="include")
    pages, queries = apply_gsc_filters(site, PAGE_ROWS, QUERY_ROWS)
    assert pages == PAGE_ROWS  # invalid pattern -> no filtering applied
    assert queries == QUERY_ROWS


def test_blank_regex_is_treated_as_no_filter():
    site = _site(gsc_page_filter_regex="   ", gsc_page_filter_mode="include")
    pages, _ = apply_gsc_filters(site, PAGE_ROWS, QUERY_ROWS)
    assert pages == PAGE_ROWS


def test_ga4_page_filter_scopes_pages_and_mobile_share_together():
    site = _site(ga4_page_filter_regex=r"/products/", ga4_page_filter_mode="include")
    mobile_share = {"https://example.com/products/a": 0.4, "https://example.com/blog/b": 0.6}
    pages, mobile = apply_ga4_filters(site, PAGE_ROWS, mobile_share)
    assert [p["page"] for p in pages] == ["https://example.com/products/a"]
    assert mobile == {"https://example.com/products/a": 0.4}


def test_ga4_no_filter_returns_everything_unchanged():
    site = _site()
    mobile_share = {"https://example.com/products/a": 0.4}
    pages, mobile = apply_ga4_filters(site, PAGE_ROWS, mobile_share)
    assert pages == PAGE_ROWS
    assert mobile == mobile_share
