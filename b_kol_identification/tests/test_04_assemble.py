import importlib.util, os
_S = os.path.join(os.path.dirname(__file__), "..", "04_assemble_kols.py")
_spec = importlib.util.spec_from_file_location("asm", _S)
mod = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(mod)

def test_composite_replaces_raw_sum_as_score():
    hcps = [{"verified_web_count": 1, "verified_pubmed_count": 1, "reach": {"distinct_coauthors": 0}, "ratio": {"ratio": 0.0}},
            {"verified_web_count": 3, "verified_pubmed_count": 4, "reach": {"distinct_coauthors": 5}, "ratio": {"ratio": 1.0}}]
    out = mod.apply_composite(hcps, {"relevance": 0.6, "reach": 0.25, "ratio": 0.15}, "minmax")
    assert out[1]["kol_score"] > out[0]["kol_score"]

def test_normalize_percentile_rank():
    out = mod.normalize_values([10, 20, 30, 30], "percentile")
    assert out[0] < out[1] < out[2] and out[2] == out[3]      # ties share a rank
    assert 0.0 <= min(out) and max(out) <= 1.0

def test_normalize_minmax():
    assert mod.normalize_values([0, 5, 10], "minmax") == [0.0, 0.5, 1.0]

def test_normalize_degenerate_pool_is_zero():
    assert mod.normalize_values([7, 7, 7], "minmax") == [0.0, 0.0, 0.0]

def test_apply_composite_weights_and_contributions():
    hcps = [
        {"verified_web_count": 4, "verified_pubmed_count": 0, "reach": {"distinct_coauthors": 0},
         "ratio": {"ratio": 0.0}},
        {"verified_web_count": 0, "verified_pubmed_count": 0, "reach": {"distinct_coauthors": 10},
         "ratio": {"ratio": 1.0}}]
    w = {"relevance": 0.6, "reach": 0.25, "ratio": 0.15}
    out = mod.apply_composite(hcps, w, "minmax")
    # HCP0 maxes relevance (norm 1 * .6), HCP1 maxes reach+ratio (.25 + .15 = .4)
    assert abs(out[0]["kol_score"] - 0.6) < 1e-9
    assert abs(out[1]["kol_score"] - 0.4) < 1e-9
    assert "factor_contributions" in out[0]

def test_rising_star_new_voice_on_verified_years():
    hcps = [{"verified_pubmed_years":{"2024":4,"2025":0}}]
    out = mod.flag_rising_stars(hcps, min_pubs=3, growth=3.0)
    assert out[0]["rising_star"] is True

def test_aggregate_themes_counts_from_claims():
    # Production's ingest prompt is built from term_en labels (03_wiki_build.py), so
    # claims' "themes" come back as term_en STRINGS (e.g. "Obesity"), never term_key codes.
    hcp = {"claims":[{"themes":["Obesity","GLP-1"]},{"themes":["Obesity"]}]}
    terms = [{"term_key":"CF_OBESITY","term_en":"Obesity"},{"term_key":"CF_GLP1","term_en":"GLP-1"}]
    out = mod.aggregate_themes(hcp, terms, top_n=5)
    assert out[0]["term_key"] == "CF_OBESITY" and out[0]["count"] == 2

def test_drop_zero_score_keeps_pool_minimum_composite_with_real_verified_sources():
    # kol_score is now the normalized composite -- the pool minimum normalizes to 0
    # even when the HCP has real verified sources (a degenerate pool could otherwise
    # empty the whole report). The drop criterion must be raw verified counts, not
    # the normalized composite.
    hcps = [{"s_customer_id":"1","verified_web_count":0,"verified_pubmed_count":0,"kol_score":0},
            {"s_customer_id":"2","verified_web_count":1,"verified_pubmed_count":0,"kol_score":0},
            {"s_customer_id":"3","verified_web_count":2,"verified_pubmed_count":1,"kol_score":0.5},
            {"s_customer_id":"4"}]  # missing counts treated as 0 -> dropped
    out = mod.drop_zero_score(hcps)
    assert [h["s_customer_id"] for h in out] == ["2", "3"]

def test_coauthor_query_has_pmid_in_list():
    sql = mod.build_coauthor_query("CORE.PUBMED.AUTHOR", ["39000001"])
    assert "CORE.PUBMED.AUTHOR" in sql and "'39000001'" in sql

def test_coauthor_edges_marks_external():
    author_rows = [{"PMID":"p1","FIRSTNAME":"Anna","LASTNAME":"Berg"},
                   {"PMID":"p1","FIRSTNAME":"Ext","LASTNAME":"Person"}]
    verified_by_pmid = {"p1":["10"]}   # only HCP 10 authored p1 among our KOLs
    roster = [{"s_customer_id":"10","firstname":"Anna","lastname":"Berg","name":"Anna Berg"}]
    edges = mod.build_coauthor_edges(author_rows, verified_by_pmid, roster)
    ext = [e for e in edges if e["b_external"]]
    assert any(e["b_name"] == "Ext Person" for e in ext)

def test_comention_edges_from_mentions():
    hcps = [{"s_customer_id":"10","name":"Anna Berg",
             "mentioned":[{"name":"Karl Neu","s_customer_id":"20"},{"name":"X","s_customer_id":""}]}]
    edges = mod.build_comention_edges(hcps)
    assert {"from":"10","to":"20","from_name":"Anna Berg","to_name":"Karl Neu","count":1} in edges

def test_coauthor_edges_no_double_count_for_internal_pair():
    author_rows = [{"PMID":"p1","FIRSTNAME":"Anna","LASTNAME":"Berg"},
                   {"PMID":"p1","FIRSTNAME":"Karl","LASTNAME":"Neu"}]
    verified_by_pmid = {"p1": ["1", "2"]}   # ONE shared pmid
    roster = [{"s_customer_id":"1","firstname":"Anna","lastname":"Berg","name":"Anna Berg"},
              {"s_customer_id":"2","firstname":"Karl","lastname":"Neu","name":"Karl Neu"}]
    edges = mod.build_coauthor_edges(author_rows, verified_by_pmid, roster)
    internal = [e for e in edges if not e["b_external"]]
    assert len(internal) == 1 and internal[0]["shared_pmids"] == 1

def test_rising_star_not_flagged_for_established_author():
    hcps = [{"verified_pubmed_years": {"2023": 1, "2025": 3}}]  # max 2025; a prior 2023 pub
    out = mod.flag_rising_stars(hcps, min_pubs=3, growth=1000.0)
    assert out[0]["rising_star"] is False

def test_compute_reach_dedupes_by_orcid_and_excludes_self():
    authors_by_pmid = {"p1": [
        {"ORCID": "0000-1", "FIRSTNAME": "Anna", "LASTNAME": "Berg", "AFFILIATION": "Uni A"},
        {"ORCID": "0000-2", "FIRSTNAME": "Carl", "LASTNAME": "Ott",  "AFFILIATION": "Uni B"},
        {"ORCID": "0000-9", "FIRSTNAME": "Self", "LASTNAME": "Hcp",  "AFFILIATION": "Uni A"}],
        "p2": [
        {"ORCID": "0000-1", "FIRSTNAME": "Anna", "LASTNAME": "Berg", "AFFILIATION": "Uni A"}]}  # dup coauthor
    r = mod.compute_reach(["p1", "p2"], authors_by_pmid, "Self", "Hcp")
    assert r["distinct_coauthors"] == 2         # Anna + Carl, self excluded, Anna deduped
    assert r["distinct_affiliations"] == 2      # Uni A + Uni B

def test_compute_ratio_normal():
    r = mod.compute_ratio(3, 2, 5, 5, min_denominator=5)
    assert r["denominator"] == 10 and abs(r["ratio"] - 0.5) < 1e-9 and r["neutral"] is False

def test_compute_ratio_low_denominator_is_neutral():
    r = mod.compute_ratio(1, 0, 1, 0, min_denominator=5)
    assert r["neutral"] is True and r["ratio"] == 0.0

def test_top_affiliations_ranked_by_frequency_self_excluded():
    authors_by_pmid = {
        "p1": [{"FIRSTNAME": "Self", "LASTNAME": "Hcp", "AFFILIATION": "Self Clinic"},
               {"FIRSTNAME": "Anna", "LASTNAME": "Berg", "AFFILIATION": "Uni A"}],
        "p2": [{"FIRSTNAME": "Carl", "LASTNAME": "Ott", "AFFILIATION": "Uni B"},
               {"FIRSTNAME": "Anna", "LASTNAME": "Berg", "AFFILIATION": "Uni A"}]}
    out = mod.top_affiliations(["p1", "p2"], authors_by_pmid, "Self", "Hcp")
    assert out == ["Uni A", "Uni B"]

def test_top_affiliations_dedupes_case_insensitively_keeps_first_seen_casing():
    authors_by_pmid = {
        "p1": [{"FIRSTNAME": "Anna", "LASTNAME": "Berg", "AFFILIATION": "Uni A Hospital"}],
        "p2": [{"FIRSTNAME": "Carl", "LASTNAME": "Ott", "AFFILIATION": "UNI A HOSPITAL"}]}
    out = mod.top_affiliations(["p1", "p2"], authors_by_pmid, "Self", "Hcp")
    assert out == ["Uni A Hospital"]

def test_top_affiliations_caps_at_default_n_of_three():
    # 4 distinct affiliations across verified pmids, with unambiguous frequencies
    # (4, 3, 2, 1 mentions) -- only the top 3 by frequency should be returned.
    authors_by_pmid = {
        "p1": [{"FIRSTNAME": "A1", "LASTNAME": "X", "AFFILIATION": "Uni A"}],
        "p2": [{"FIRSTNAME": "A2", "LASTNAME": "X", "AFFILIATION": "Uni A"}],
        "p3": [{"FIRSTNAME": "A3", "LASTNAME": "X", "AFFILIATION": "Uni A"}],
        "p4": [{"FIRSTNAME": "A4", "LASTNAME": "X", "AFFILIATION": "Uni A"}],
        "p5": [{"FIRSTNAME": "B1", "LASTNAME": "X", "AFFILIATION": "Uni B"}],
        "p6": [{"FIRSTNAME": "B2", "LASTNAME": "X", "AFFILIATION": "Uni B"}],
        "p7": [{"FIRSTNAME": "B3", "LASTNAME": "X", "AFFILIATION": "Uni B"}],
        "p8": [{"FIRSTNAME": "C1", "LASTNAME": "X", "AFFILIATION": "Uni C"}],
        "p9": [{"FIRSTNAME": "C2", "LASTNAME": "X", "AFFILIATION": "Uni C"}],
        "p10": [{"FIRSTNAME": "D1", "LASTNAME": "X", "AFFILIATION": "Uni D"}]}
    out = mod.top_affiliations(list(authors_by_pmid.keys()), authors_by_pmid, "Self", "Hcp")
    assert len(out) == 3
    assert out == ["Uni A", "Uni B", "Uni C"]
