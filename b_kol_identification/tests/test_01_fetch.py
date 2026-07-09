import importlib.util, os
_S = os.path.join(os.path.dirname(__file__), "..", "01_fetch_and_shortlist.py")
_spec = importlib.util.spec_from_file_location("fetch", _S)
mod = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(mod)

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
