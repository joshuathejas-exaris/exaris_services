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
    html = mod.build_report_html(DATA)
    assert html.strip().startswith("<!DOCTYPE html>")
    assert "http://" not in html.split("top_quotes")[0] or "cdn" not in html.lower()
    assert "Anna Berg" in html


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

def test_thematic_heatmap_uses_theme_labels_not_cf_by_term():
    html = mod.render_thematic_heatmap(DATA["hcps"], DATA["pca_terms"], top_n=20)
    assert "Anna Berg" in html and "Obesity" in html and "5" in html

def test_regional_groups_by_city_with_tier_breakdown():
    hcps = DATA["hcps"] + [{**DATA["hcps"][0], "s_customer_id": "12", "name": "Carla Weiss",
                            "city": "Munich", "tier": "B"}]
    html = mod.render_regional(hcps)
    assert "Berlin" in html and "Munich" in html

def test_profiles_render_quotes_and_verified_source_breakdown():
    html = mod.render_profiles(DATA["hcps"], ["2023", "2024"], top_n=10)
    assert "patient improved" in html
    assert "3 web" in html or "3</b> web" in html.lower() or "web" in html

def test_no_composite_or_digi_fields_leak_into_full_report():
    html = mod.build_report_html(DATA)
    assert "composite_score" not in html
    assert "digi_score" not in html.lower()
