import importlib.util, os
_S = os.path.join(os.path.dirname(__file__), "..", "05_generate_report.py")
_spec = importlib.util.spec_from_file_location("rep", _S)
mod = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(mod)

DATA = {"indication":"Obesity","client_drug":"Ozempic","generated_at":"2026-07-09T10:00:00",
  "pca_terms":[{"term_key":"CF_OBESITY","term_en":"Obesity"}],
  "hcps":[{"s_customer_id":"10","name":"Anna Berg","city":"Berlin","specialty":"Innere Medizin",
           "rating":"A","verified_web_count":3,"verified_pubmed_count":4,"kol_score":7,"latest_year":2024,
           "tier":"A","rising_star":False,"pub_by_year":{"2023":2,"2024":2},
           "theme_labels":[{"term_key":"CF_OBESITY","term_en":"Obesity","count":5}],
           "top_quotes":[{"quote":"patient improved","url":"http://x","sentiment":"positive"}]}],
  "coauthor_edges":[{"hcp_a":"10","hcp_b":"ext1","shared_pmids":2,"a_name":"Anna Berg","b_name":"Ext P","b_external":True}],
  "comention_edges":[]}


def test_stat_cards_show_kol_count_and_no_digiscore():
    html = mod.render_stat_cards(DATA)
    assert "1" in html
    assert "digi" not in html.lower()

def test_kol_table_shows_verified_source_counts():
    html = mod.render_kol_table(DATA["hcps"], top_n=25)
    assert "Anna Berg" in html and "7" in html   # kol_score
    assert "Obesity" in html                       # theme

def test_network_lists_external_collaborator():
    html = mod.render_network(DATA["coauthor_edges"], DATA["comention_edges"], DATA["hcps"])
    assert "Ext P" in html

def test_build_report_html_is_selfcontained():
    import re
    html = mod.build_report_html(DATA)
    assert html.strip().startswith("<!DOCTYPE html>")
    assert "Anna Berg" in html
    # No external resources the browser would auto-fetch.
    assert "<link " not in html          # no stylesheet/font links
    assert 'src="http' not in html       # no external <script>/<img> sources
    assert "@import" not in html         # no CSS @import of remote sheets
    # No external href inside a <link> or <script> tag (a plain quote link is allowed).
    for tag in re.findall(r"<(?:link|script)\b[^>]*>", html):
        assert 'href="http' not in tag and 'src="http' not in tag


# ── Supplementary tests for the ported renderers (v2 field names) ──────────────

def test_rising_stars_only_lists_flagged_hcps():
    hcps = [dict(DATA["hcps"][0]),
            {**DATA["hcps"][0], "s_customer_id": "11", "name": "Boris Klein",
             "rising_star": True, "pub_by_year": {"2023": 0, "2024": 3}}]
    html = mod.render_rising_stars(hcps, ["2023", "2024"])
    assert "Boris Klein" in html
    assert "Anna Berg" not in html  # not flagged as rising

def test_rising_stars_empty_when_none_flagged():
    assert mod.render_rising_stars(DATA["hcps"], ["2023", "2024"]) == ""

def test_rising_stars_numbers_come_from_verified_pubmed_years_not_pub_by_year():
    # The Rising badge (Stage 04) is computed from verified_pubmed_years, so the card's
    # recent/prior/ratio text must be computed from that field too -- not from the
    # unverified/candidate pub_by_year -- or the displayed numbers won't justify the badge.
    h = {**DATA["hcps"][0], "rising_star": True,
         "pub_by_year": {"2020": 100, "2021": 100},
         "verified_pubmed_years": {"2024": 2, "2025": 1}}
    html = mod.render_rising_stars([h], ["2020", "2021", "2024", "2025"])
    assert "<b>3</b> recent" in html and "<b>0</b> prior" in html
    assert "100" not in html

def test_thematic_heatmap_uses_theme_labels_not_cf_by_term():
    html = mod.render_thematic_heatmap(DATA["hcps"], DATA["pca_terms"], top_n=20)
    assert "Anna Berg" in html and "Obesity" in html and "5" in html

def test_thematic_heatmap_cell_is_populated_not_blank():
    # theme_labels[].term_key ("CF_OBESITY") must match pca_terms[].term_key so the
    # HCP's count actually lands in a heatmap cell instead of every lookup missing.
    html = mod.render_thematic_heatmap(DATA["hcps"], DATA["pca_terms"], top_n=20)
    assert "background:rgba(" in html      # a real (non-"transparent") cell was rendered
    assert ">5</td>" in html               # the count itself is visible in that cell
    assert 'background:transparent">5</td>' not in html

def test_regional_groups_by_city_with_tier_breakdown():
    hcps = DATA["hcps"] + [{**DATA["hcps"][0], "s_customer_id": "12", "name": "Carla Weiss",
                            "city": "Munich", "tier": "B"}]
    html = mod.render_regional(hcps)
    assert "Berlin" in html and "Munich" in html

def test_profiles_render_quotes_and_verified_source_breakdown():
    html = mod.render_profiles(DATA["hcps"], ["2023", "2024"], top_n=10)
    h = DATA["hcps"][0]
    # The actual verified quote text must render.
    assert h["top_quotes"][0]["quote"] in html
    # The actual verified source counts must render (kol_score + web/pubmed split).
    assert f'{h["kol_score"]} verified sources' in html
    assert f'{h["verified_web_count"]} web' in html
    assert f'{h["verified_pubmed_count"]} pubmed' in html

def test_no_composite_or_digi_fields_leak_into_full_report():
    html = mod.build_report_html(DATA)
    assert "composite_score" not in html
    assert "digi_score" not in html.lower()


def test_report_has_grouped_sidebar_nav():
    html = mod.build_report_html(DATA)
    assert 'class="sidebar"' in html
    for group in ("OVERVIEW", "ANALYSIS", "PROFILES"):
        assert group in html
    for item in ("Executive Dashboard", "KOL Ranking", "Rising Stars",
                 "Thematic Distribution", "Regional Distribution",
                 "Collaboration Network", "KOL Profiles"):
        assert item in html

def test_report_has_exactly_one_active_panel_and_tab_script():
    html = mod.build_report_html(DATA)
    assert html.count('class="panel active"') == 1   # first panel active on load
    assert "function showTab(" in html
    assert "js-tabs" in html

def test_tab_id_slugifies_label():
    assert mod.tab_id("Rising Stars") == "tab-rising-stars"


def test_year_axis_is_fixed_span_ending_at_anchor():
    data = {"anchor_year": 2023, "pub_history_years": 20, "hcps": []}
    axis = mod.build_year_axis(data)
    assert axis[0] == "2004" and axis[-1] == "2023"
    assert len(axis) == 20
    assert all(isinstance(y, str) for y in axis)

def test_year_axis_falls_back_to_present_years_without_anchor():
    data = {"hcps": [{"pub_by_year": {"2019": 1, "2021": 2}},
                     {"pub_by_year": {"2020": 1}}]}
    assert mod.build_year_axis(data) == ["2019", "2020", "2021"]

def test_report_uses_20y_axis_in_profile_label():
    data = {**DATA, "anchor_year": 2023, "pub_history_years": 20}
    html = mod.build_report_html(data)
    assert "2004" in html and "2023" in html   # full span rendered in a spark label


def test_write_excel_creates_one_row_per_kol(tmp_path):
    out = tmp_path / "k.xlsx"
    mod.write_excel(DATA, str(out))
    import openpyxl
    wb = openpyxl.load_workbook(out); ws = wb.active
    headers = [c.value for c in ws[1]]
    assert "Name" in headers and "Verified sources" in headers and "Tier" in headers
    assert ws.max_row == 1 + len(DATA["hcps"])


# ── Task 7: network graph, score drill-down, per-section explainers ────────────

def test_network_svg_is_selfcontained():
    svg = mod.render_network_svg(
        [{"a_name": "A B", "b_name": "C D", "shared_pmids": 2, "b_external": False}],
        [{"name": "A B", "reach": 3, "affiliation": "Uni A"}, {"name": "C D", "reach": 1, "affiliation": "Uni B"}])
    assert "<svg" in svg and "http://" not in svg and "https://" not in svg
    assert "Uni A" in svg   # affiliation surfaced (label/title)

def test_score_breakdown_shows_three_factors():
    hcp = {"name": "X", "kol_score": 0.72,
           "factor_contributions": {"relevance": 0.5, "reach": 0.15, "ratio": 0.07},
           "norm_relevance": 0.83, "norm_reach": 0.6, "norm_ratio": 0.47,
           "reach": {"distinct_coauthors": 6, "distinct_affiliations": 3},
           "ratio": {"ratio": 0.47, "denominator": 17},
           "top_quotes": [{"quote": "q", "url": "u", "sentiment": "positive"}]}
    html = mod.render_score_breakdown(hcp, {"relevance": 0.6, "reach": 0.25, "ratio": 0.15})
    for token in ("Relevance", "Reach", "Ratio", "0.72", "6", "q"):
        assert token in html

def test_as_of_banner_only_when_backtesting():
    assert mod.as_of_banner(2021, "2021") != ""
    assert mod.as_of_banner(2025, "latest") == ""


def test_network_node_prefers_real_affiliation_over_city():
    # Spec 9: the network graph should show real co-author affiliations on hover,
    # not the HCP's practice city (same city != same institution).
    data = {**DATA, "hcps": [{**DATA["hcps"][0], "affiliations": ["Uni Klinikum X"]}]}
    html = mod.build_report_html(data)
    assert "Uni Klinikum X" in html


def test_network_node_falls_back_to_city_without_affiliations():
    # Spec 9: when a KOL has no recorded co-author affiliations, the network
    # graph's hover title must fall back to their practice city instead of
    # leaving it blank.
    data = {**DATA, "hcps": [{**DATA["hcps"][0], "affiliations": []}]}
    html = mod.build_report_html(data)
    assert "Anna Berg — Berlin (reach 0)</title>" in html
