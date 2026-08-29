import os

from app.ingestion.screaming_frog import _hostname, classify_site_scale, import_crawl_folder, preview_crawl_folder

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")

# Real Screaming Frog CLI output (verified via `--export-tabs "Response Codes:All"`
# `--bulk-export "Links:All Inlinks"` `--save-report "Redirects:Redirect Chains"`
# against a live crawl) confirmed the Redirect Chains report only lists multi-hop
# chains -- a single-hop 301, the common case, never appears there. The fixture's
# old-product row exercises exactly that: its Response Codes "Redirect URL" points
# to an intermediate hop, while Redirect Chains resolves the true final destination.


def test_import_crawl_folder_classifies_issues():
    issues = {i.url: i for i in import_crawl_folder(FIXTURES).issues}

    assert set(issues.keys()) == {
        "https://example.com/old-guide",
        "https://example.com/old-product",
        "https://example.com/broken-api",
    }
    # 200s are not issues and must not appear
    assert "https://example.com/" not in issues
    assert "https://example.com/new-product" not in issues


def test_404_gets_its_inlinks():
    issues = {i.url: i for i in import_crawl_folder(FIXTURES).issues}
    guide = issues["https://example.com/old-guide"]
    assert guide.issue_type == "404"
    assert guide.status_code == 404
    assert sorted(guide.inlinking_urls) == [
        "https://example.com/blog/index",
        "https://example.com/resources",
    ]


def test_redirect_resolves_final_target_and_inlinks():
    issues = {i.url: i for i in import_crawl_folder(FIXTURES).issues}
    old_product = issues["https://example.com/old-product"]
    assert old_product.issue_type == "301"
    assert old_product.redirects_to == "https://example.com/new-product"
    assert len(old_product.inlinking_urls) == 5


def test_server_error_classified_as_5xx():
    issues = {i.url: i for i in import_crawl_folder(FIXTURES).issues}
    api = issues["https://example.com/broken-api"]
    assert api.issue_type == "5xx"
    assert api.status_code == 500


def test_multihop_chain_final_destination_overrides_immediate_redirect_url():
    """The chain's Final Address (2 hops) must win over the immediate hop from Response Codes."""
    issues = {i.url: i for i in import_crawl_folder(FIXTURES).issues}
    old_product = issues["https://example.com/old-product"]
    assert old_product.redirects_to == "https://example.com/new-product"
    assert old_product.redirects_to != "https://example.com/intermediate-product"


def test_single_hop_redirect_falls_back_to_response_codes_redirect_url(tmp_path):
    """With no Redirect Chains export at all, a single-hop 301 still resolves via
    the Response Codes export's own 'Redirect URL' column."""
    (tmp_path / "response_codes_all.csv").write_text(
        "Address,Status Code,Redirect URL\n"
        "https://example.com/old-page,301,https://example.com/new-page\n"
        "https://example.com/new-page,200,\n"
    )
    issues = {i.url: i for i in import_crawl_folder(str(tmp_path)).issues}
    assert issues["https://example.com/old-page"].redirects_to == "https://example.com/new-page"


def test_site_scale_classification():
    assert classify_site_scale(50) == "small"
    assert classify_site_scale(999) == "small"
    assert classify_site_scale(1_000) == "medium"
    assert classify_site_scale(49_999) == "medium"
    assert classify_site_scale(50_000) == "large"
    assert classify_site_scale(3_000_000) == "large"


def test_fixture_folder_reports_total_urls_and_small_scale():
    result = import_crawl_folder(FIXTURES)
    assert result.total_urls == 5  # every row in response_codes.csv, issues and 200s alike
    assert result.site_scale == "small"


def test_indexation_blocking_grouped_by_reason_and_excludes_issue_urls(tmp_path):
    (tmp_path / "response_codes_all.csv").write_text(
        "Address,Status Code,Indexability Status\n"
        "https://example.com/a,200,Noindex\n"
        "https://example.com/b,200,Noindex\n"
        "https://example.com/c,200,Blocked by Robots.txt\n"
        "https://example.com/d,200,\n"  # indexable, no issue
        "https://example.com/broken,404,\n"  # already a 404 issue -- must not double-count here
    )
    result = import_crawl_folder(str(tmp_path))
    assert sorted(result.indexation_blocking["Noindex"]) == [
        "https://example.com/a",
        "https://example.com/b",
    ]
    assert result.indexation_blocking["Blocked by Robots.txt"] == ["https://example.com/c"]
    assert all(u != "https://example.com/broken" for urls in result.indexation_blocking.values() for u in urls)
    assert len(result.issues) == 1  # just the 404


# ---------- status codes formatted as floats (observed from the desktop GUI export) ----------


def test_status_code_formatted_as_a_float_string_still_parses(tmp_path):
    (tmp_path / "response_codes_all.csv").write_text(
        "Address,Status Code\n"
        "https://example.com/a,200.0\n"
        "https://example.com/b,404.0\n"
    )
    result = import_crawl_folder(str(tmp_path))
    assert result.total_urls == 2
    assert result.issues[0].status_code == 404
    assert result.issues[0].url == "https://example.com/b"


# ---------- same-domain filtering (external URLs a crawl merely checked the status of) ----------


def test_hostname_normalizes_scheme_path_and_www():
    assert _hostname("https://www.mews.com/en") == "mews.com"
    assert _hostname("http://mews.com/some/path") == "mews.com"
    assert _hostname("mews.com") == "mews.com"
    assert _hostname("cluballiance.aaa.com") == "cluballiance.aaa.com"


def test_site_domain_filters_out_external_urls(tmp_path):
    (tmp_path / "response_codes_all.csv").write_text(
        "Address,Status Code\n"
        "https://www.mews.com/en,200\n"
        "https://www.mews.com/pricing,404\n"
        "https://www.linkedin.com/in/someone,999\n"
    )
    result = import_crawl_folder(str(tmp_path), site_domain="https://www.mews.com/en")
    assert result.total_urls == 2  # linkedin.com excluded entirely, not counted as a crawled page
    assert len(result.issues) == 1
    assert result.issues[0].url == "https://www.mews.com/pricing"


def test_no_site_domain_keeps_old_behavior_for_existing_callers():
    result = import_crawl_folder(FIXTURES)  # no site_domain passed -- must behave exactly as before
    assert result.total_urls == 5


# ---------- column-signature detection (any tool's export, matched by header not filename) ----------


def test_response_codes_detected_regardless_of_filename(tmp_path):
    # real-world case: O'Reilly's own export was named "response_codes_internal_all.csv" --
    # detection must key off the header (Address + Status Code), not a filename keyword.
    (tmp_path / "totally_unrelated_name.csv").write_text(
        "Address,Status Code\nhttps://example.com/a,200\nhttps://example.com/b,404\n"
    )
    result = import_crawl_folder(str(tmp_path))
    assert result.total_urls == 2
    assert len(result.issues) == 1


def test_gsc_coverage_export_detected_and_merged_by_reason(tmp_path):
    (tmp_path / "response_codes.csv").write_text("Address,Status Code\nhttps://example.com/a,200\n")
    (tmp_path / "Blocked by robots.txt.csv").write_text(
        "URL,Last crawled\n"
        + "".join(f"https://example.com/blocked-{i},2026-08-18\n" for i in range(3))
    )
    result = import_crawl_folder(str(tmp_path))
    assert "GSC: Blocked by robots.txt" in result.indexation_blocking
    assert len(result.indexation_blocking["GSC: Blocked by robots.txt"]) == 3


def test_gsc_coverage_export_flags_when_it_hits_the_1000_row_export_cap(tmp_path):
    (tmp_path / "response_codes.csv").write_text("Address,Status Code\nhttps://example.com/a,200\n")
    (tmp_path / "Excluded by noindex tag.csv").write_text(
        "URL,Last crawled\n" + "".join(f"https://example.com/n-{i},2026-08-18\n" for i in range(1000))
    )
    result = import_crawl_folder(str(tmp_path))
    [reason] = [r for r in result.indexation_blocking if "noindex tag" in r]
    assert "capped at 1000" in reason


def test_gsc_coverage_kept_separate_from_a_same_named_sf_indexability_bucket(tmp_path):
    # SF's own Response Codes export can report "Blocked by robots.txt" too (via
    # Indexability Status) -- the GSC-sourced bucket for the same reason must NOT be
    # merged into it, since the two are different measurements that can legitimately
    # disagree (see import_crawl_folder's docstring).
    (tmp_path / "response_codes.csv").write_text(
        "Address,Status Code,Indexability Status\n"
        "https://example.com/a,200,\n"
        "https://example.com/blocked-sf,200,Blocked by robots.txt\n"
    )
    (tmp_path / "Blocked by robots.txt.csv").write_text(
        "URL,Last crawled\nhttps://example.com/blocked-gsc,2026-08-18\n"
    )
    result = import_crawl_folder(str(tmp_path))
    assert result.indexation_blocking["Blocked by robots.txt"] == ["https://example.com/blocked-sf"]
    assert result.indexation_blocking["GSC: Blocked by robots.txt"] == ["https://example.com/blocked-gsc"]


def test_unrecognized_csv_is_silently_skipped(tmp_path):
    (tmp_path / "response_codes.csv").write_text("Address,Status Code\nhttps://example.com/a,200\n")
    (tmp_path / "some_other_report.csv").write_text("Widget,Count\nfoo,1\n")
    result = import_crawl_folder(str(tmp_path))  # must not raise
    assert result.total_urls == 1


# ---------- response_codes classification aliases ("URL"/"HTTP Status Code" etc,
# not just Screaming Frog's own "Address"/"Status Code") ----------


def test_response_codes_recognizes_url_and_http_status_code_headers(tmp_path):
    """Not Screaming Frog's own column names, but the same shape -- parse_response_codes
    already treated "url" as an alias for "address" (see its own _col candidate list);
    classification used to be stricter than parsing, so a file shaped exactly like this
    just silently fell through as unrecognized until now."""
    (tmp_path / "crawl_export.csv").write_text("URL,HTTP Status Code\nhttps://example.com/a,404\n")
    result = import_crawl_folder(str(tmp_path))
    assert result.total_urls == 1
    assert result.issues[0].url == "https://example.com/a"
    assert result.issues[0].issue_type == "404"


def test_response_codes_recognizes_response_code_alias(tmp_path):
    (tmp_path / "crawl_export.csv").write_text("Address,Response Code\nhttps://example.com/a,500\n")
    result = import_crawl_folder(str(tmp_path))
    assert result.issues[0].issue_type == "5xx"


def test_all_inlinks_still_wins_over_the_broadened_response_codes_aliases(tmp_path):
    """Regression: a real All Inlinks export also has a Status Code column (see the
    module docstring) -- broadening response_codes' aliases must not break the existing
    "All Inlinks is checked first" priority that already handles this ambiguity."""
    (tmp_path / "response_codes.csv").write_text("Address,Status Code\nhttps://example.com/a,200\n")
    (tmp_path / "all_inlinks.csv").write_text(
        "Source,Destination,Status Code\nhttps://example.com/x,https://example.com/a,200\n"
    )
    preview = preview_crawl_folder(str(tmp_path))
    kinds = {f.filename: f.kind for f in preview.files}
    assert kinds["all_inlinks.csv"] == "all_inlinks"
    assert kinds["response_codes.csv"] == "response_codes"


# ---------- preview_crawl_folder (see app/routers/imports.py's preview-then-confirm flow) ----------


def test_preview_reports_each_files_detected_kind_and_row_count(tmp_path):
    (tmp_path / "response_codes.csv").write_text(
        "Address,Status Code\nhttps://example.com/a,200\nhttps://example.com/b,404\n"
    )
    preview = preview_crawl_folder(str(tmp_path))
    [f] = preview.files
    assert f.filename == "response_codes.csv"
    assert f.kind == "response_codes"
    assert f.kind_label == "Response Codes"
    assert f.row_count == 2
    assert f.columns == ["Address", "Status Code"]


def test_preview_flags_unrecognized_files_instead_of_hiding_them(tmp_path):
    (tmp_path / "response_codes.csv").write_text("Address,Status Code\nhttps://example.com/a,200\n")
    (tmp_path / "mystery_export.csv").write_text("Widget,Count\nfoo,1\nbar,2\n")
    preview = preview_crawl_folder(str(tmp_path))
    mystery = next(f for f in preview.files if f.filename == "mystery_export.csv")
    assert mystery.kind is None
    assert mystery.kind_label == "Not recognized"
    assert mystery.row_count == 2
    assert preview.unrecognized_count == 1
    assert preview.has_response_codes is True


def test_preview_has_response_codes_false_when_the_required_file_is_missing(tmp_path):
    (tmp_path / "all_inlinks.csv").write_text("Source,Destination\nhttps://a.com,https://b.com\n")
    preview = preview_crawl_folder(str(tmp_path))
    assert preview.has_response_codes is False
