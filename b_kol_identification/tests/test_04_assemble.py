import importlib.util, os
_S = os.path.join(os.path.dirname(__file__), "..", "04_assemble_kols.py")
_spec = importlib.util.spec_from_file_location("asm", _S)
mod = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(mod)

def test_score_is_sum_of_verified_counts_sorted():
    hcps = [{"s_customer_id":"1","verified_web_count":1,"verified_pubmed_count":1,"verified_pubmed_years":{"2020":1}},
            {"s_customer_id":"2","verified_web_count":3,"verified_pubmed_count":4,"verified_pubmed_years":{"2024":4}}]
    out = mod.score_hcps(hcps)
    assert out[0]["s_customer_id"] == "2" and out[0]["kol_score"] == 7
    assert out[0]["latest_year"] == 2024

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

def test_drop_zero_score_removes_unverified():
    hcps = [{"s_customer_id":"1","kol_score":0},
            {"s_customer_id":"2","kol_score":3},
            {"s_customer_id":"3"}]  # missing kol_score treated as 0
    out = mod.drop_zero_score(hcps)
    assert [h["s_customer_id"] for h in out] == ["2"]

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
