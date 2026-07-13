import importlib.util, os, pytest
_S = os.path.join(os.path.dirname(__file__), "..", "01_fetch_and_shortlist.py")
_spec = importlib.util.spec_from_file_location("fetch", _S)
mod = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(mod)


@pytest.fixture
def fetch_mod():
    p = os.path.join(os.path.dirname(__file__), "..", "01_fetch_and_shortlist.py")
    spec = importlib.util.spec_from_file_location("fetch01", p)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m

def test_pca_terms_query_filters_on_pca_when_true():
    sql = mod.build_pca_terms_query("DB.V1.CONTENT_FRAME_SPEC", True)
    assert "DB.V1.CONTENT_FRAME_SPEC" in sql
    assert "PCA" in sql.upper() and "'X'" in sql.upper()

def test_pca_terms_query_no_pca_filter_when_false():
    sql = mod.build_pca_terms_query("DB.V1.CONTENT_FRAME_SPEC", False)
    assert "PCA" not in sql.upper().split("WHERE")[-1] if "WHERE" in sql.upper() else True

def test_term_ilike_predicate_ors_each_term_on_both_cols():
    pred = mod.term_ilike_predicate(["obesity", "glp-1"])
    assert "COL_KEYWORDS_ORIG ILIKE '%obesity%'" in pred
    assert "COL_KEYWORDS_EN ILIKE '%glp-1%'" in pred
    assert " OR " in pred

def test_web_candidates_query_has_gate_and_in_relation():
    sql = mod.build_web_candidates_query("DB.F.LLM_VALIDATION", "x ILIKE '%a%'", 29)
    assert "DB.F.LLM_VALIDATION" in sql
    assert "NEAR_BY = 1" in sql and "IS_OLD = 0" in sql and "IS_DOCTOR = 1" in sql
    assert "IN_RELATION > 29" in sql
    assert "S_CUSTOMER_ID" in sql and "WEBSITE_ID" in sql

def test_pubmed_candidates_query_verified_author_and_window():
    sql = mod.build_pubmed_candidates_query(
        "DB.T.PUBMED_ARTICLE_MAPPING", "DB.T.PUBMED_CF", ["CF_OBESITY","CF_GLP1"], 5, 2026)
    assert "MERGE_RESULT > 1" in sql
    assert "DB.T.PUBMED_CF" in sql
    assert "2021" in sql            # current_year - window
    assert "CF_OBESITY" in sql and "CF_GLP1" in sql

def test_hcp_meta_query_joins_and_filters_rating():
    sql = mod.build_hcp_meta_query("DB.V1.CUSTOMER_SOURCE", "DB.F.RATING_RESULT_FINAL")
    assert "DB.V1.CUSTOMER_SOURCE" in sql and "DB.F.RATING_RESULT_FINAL" in sql
    assert "IN ('A','B','C','D')" in sql or "IN ('A', 'B', 'C', 'D')" in sql

def test_matches_keywords_whole_token_only():
    assert mod.matches_keywords("obesity therapy", ["obesity"]) is True
    assert mod.matches_keywords("(SELECT)", ["ele"]) is False

def test_normalise_meta_row_builds_name():
    row = {"S_CUSTOMER_ID":"9","S_FIRSTNAME":"Anna","S_LASTNAME":"Berg",
           "S_CITY":"Berlin","S_HCP_GROUP":"Innere Medizin","RATING":"A"}
    r = mod.normalise_meta_row(row)
    assert r["name"] == "Anna Berg" and r["specialty"] == "Innere Medizin" and r["rating"] == "A"

def test_aggregate_counts_web_and_pubmed_per_hcp():
    web_id_map = {"10": ["w1", "w2"]}
    pub = [{"S_CUSTOMER_ID":"10","PMID":"p1","YEAR_VAL":2024,"CF_TREFFER":3}]
    meta = {"10": {"s_customer_id":"10","name":"A B","firstname":"A","lastname":"B",
                   "city":"X","specialty":"Y","rating":"A"}}
    out = mod.aggregate_candidates(web_id_map, pub, meta)
    h = out["10"]
    assert h["web_candidate_count"] == 2
    assert h["pubmed_candidate_count"] == 1
    assert h["pubmed_cf_treffer"] == 3
    assert h["candidate_score"] == 3   # 2 web + 1 pubmed
    assert h["pub_by_year"] == {"2024": 1}

def test_aggregate_drops_hcp_with_no_candidate_sources():
    web_id_map = {"10": []}
    meta = {"10":{"s_customer_id":"10","name":"A B","firstname":"A","lastname":"B","city":"X","specialty":"Y","rating":"A"}}
    out = mod.aggregate_candidates(web_id_map, [], meta)
    assert "10" not in out   # no candidate sources at all

def test_aggregate_excludes_hcp_without_meta():
    web_id_map = {"99": ["w1"]}
    out = mod.aggregate_candidates(web_id_map, [], {})
    assert out == {}

def test_aggregate_candidates_counts_union(fetch_mod):
    web_id_map = {"1": ["a", "b"]}
    meta = {"1": {"s_customer_id": "1", "name": "X", "city": "", "specialty": "", "rating": "A"}}
    out = fetch_mod.aggregate_candidates(web_id_map, [], meta)
    assert out["1"]["web_candidate_count"] == 2
    assert out["1"]["candidate_score"] == 2

def test_anchor_year_query_reads_max_year_from_cf_table():
    sql = mod.build_anchor_year_query("DB.F.PUBMED_CF")
    assert "MAX(YEAR_VAL)" in sql.upper()
    assert "DB.F.PUBMED_CF" in sql

def test_pubmed_history_query_windows_20y_back_from_anchor_and_counts_per_year():
    sql = mod.build_pubmed_history_query(
        "DB.T.PUBMED_ARTICLE_MAPPING", "DB.T.PUBMED_CF", ["CF_OBESITY", "CF_GLP1"], 20, 2023)
    assert "MERGE_RESULT > 1" in sql
    assert "CF_OBESITY" in sql and "CF_GLP1" in sql
    assert "2003" in sql                       # anchor(2023) - history(20)
    assert "GROUP BY" in sql.upper()
    assert "COUNT(" in sql.upper()

def test_build_pub_history_map_counts_per_hcp_per_year():
    rows = [{"S_CUSTOMER_ID": "10", "YEAR_VAL": 2011, "N": 2},
            {"S_CUSTOMER_ID": "10", "YEAR_VAL": 2023, "N": 5},
            {"S_CUSTOMER_ID": "11", "YEAR_VAL": 2020, "N": 1}]
    m = mod.build_pub_history_map(rows)
    assert m["10"] == {"2011": 2, "2023": 5}
    assert m["11"] == {"2020": 1}

def test_apply_pub_history_overrides_pub_by_year_display_field():
    hcps = [{"s_customer_id": "10", "pub_by_year": {"2023": 1}, "candidate_score": 3},
            {"s_customer_id": "99", "pub_by_year": {"2022": 9}, "candidate_score": 1}]
    out = mod.apply_pub_history(hcps, {"10": {"2011": 2, "2023": 5}})
    assert out[0]["pub_by_year"] == {"2011": 2, "2023": 5}   # replaced from history
    assert out[1]["pub_by_year"] == {}                       # no history -> empty
    assert out[0]["candidate_score"] == 3                    # scoring untouched

def test_shortlist_flags_top_n_by_score():
    hcps = [{"s_customer_id":str(i),"candidate_score":i,"pubmed_cf_treffer":0,"rating":"C"} for i in range(5)]
    out = mod.shortlist(hcps, top_n=2)
    flagged = [h for h in out if h["shortlisted"]]
    assert len(flagged) == 2
    assert {h["s_customer_id"] for h in flagged} == {"4","3"}


def test_build_vector_web_query_uses_vertical_only(fetch_mod):
    sql = fetch_mod.build_vector_web_query(
        "DB.F.LLM_VALIDATION", "DB.F.WEBSITES_VERTICAL_EMBEDDINGS_512",
        "[0.1, 0.2]::VECTOR(FLOAT, 768)", 0.55)
    assert "WEBSITES_VERTICAL_EMBEDDINGS_512" in sql
    assert "WEBSITES_EMBEDDINGS_512" not in sql           # public table excluded
    assert "VECTOR_COSINE_SIMILARITY" in sql
    assert "0.55" in sql and "IS_DOCTOR = 1" in sql

def test_merge_web_ids_unions_and_dedupes(fetch_mod):
    kw = [{"S_CUSTOMER_ID": "1", "WEBSITE_ID": "a"},
          {"S_CUSTOMER_ID": "1", "WEBSITE_ID": "b"}]
    vec = [{"S_CUSTOMER_ID": "1", "WEBSITE_ID": "b"},   # dup, ignored
           {"S_CUSTOMER_ID": "1", "WEBSITE_ID": "c"},   # new from vector arm
           {"S_CUSTOMER_ID": "2", "WEBSITE_ID": "z"}]
    out = fetch_mod.merge_web_ids(kw, vec)
    assert sorted(out["1"]) == ["a", "b", "c"]
    assert out["2"] == ["z"]


def test_resolve_anchor_year_explicit(fetch_mod):
    assert fetch_mod.resolve_anchor_year("2021", 2025) == 2021

def test_resolve_anchor_year_latest_uses_db_max(fetch_mod):
    assert fetch_mod.resolve_anchor_year("latest", 2025) == 2025

def test_resolve_anchor_year_latest_no_db(fetch_mod):
    from datetime import datetime
    assert fetch_mod.resolve_anchor_year("latest", None) == datetime.now().year


def test_build_total_pubmed_query_caps_year(fetch_mod):
    sql = fetch_mod.build_total_pubmed_query(
        "DB.T.PUBMED_ARTICLE_MAPPING", "CORE.PUBMED.ARTICLE", 2021)
    assert "MERGE_RESULT > 1" in sql and "YEAR_VAL <= 2021" in sql

def test_build_total_web_query_doctor_only(fetch_mod):
    sql = fetch_mod.build_total_web_query("DB.F.LLM_VALIDATION")
    assert "IS_DOCTOR = 1" in sql and "COUNT(DISTINCT" in sql.upper()

def test_build_totals_map_merges(fetch_mod):
    web = [{"S_CUSTOMER_ID": "1", "N": 10}]
    pub = [{"S_CUSTOMER_ID": "1", "N": 4}, {"S_CUSTOMER_ID": "2", "N": 2}]
    out = fetch_mod.build_totals_map(web, pub)
    assert out["1"] == {"total_web": 10, "total_pubmed": 4}
    assert out["2"] == {"total_web": 0, "total_pubmed": 2}
