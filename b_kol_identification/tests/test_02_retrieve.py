import importlib.util, os
_S = os.path.join(os.path.dirname(__file__), "..", "02_retrieve_sources.py")
_spec = importlib.util.spec_from_file_location("retr", _S)
mod = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(mod)

def test_web_content_query_has_in_list():
    sql = mod.build_web_content_query("DB.F.LLM_VALIDATION", ["w1","w2"], "10")
    assert "DB.F.LLM_VALIDATION" in sql
    assert "'w1'" in sql and "'w2'" in sql
    assert "CONTENT" in sql and "WEBSITE_ID" in sql

def test_web_content_query_filters_by_customer_id():
    # LLM_VALIDATION has one row per (customer, website) -- without this filter, a page
    # naming several doctors returns duplicate CONTENT rows for every HCP on that page.
    sql = mod.build_web_content_query("DB.F.LLM_VALIDATION", ["w1"], "O'Brien-10")
    assert "S_CUSTOMER_ID = 'O''Brien-10'" in sql

def test_pubmed_article_query_selects_title_abstract():
    sql = mod.build_pubmed_article_query("CORE.PUBMED.ARTICLE", ["39000001"])
    assert "CORE.PUBMED.ARTICLE" in sql
    assert "TITLE" in sql and "ABSTRACT" in sql and "'39000001'" in sql

def test_assemble_pubmed_joins_title_and_abstract_and_truncates():
    rows = [{"PMID":"39000001","TITLE":"T","ABSTRACT":"A"*100,"YEAR_VAL":2024}]
    out = mod.assemble_pubmed_sources(rows, max_chars=10)
    assert out[0]["kind"] == "pubmed" and out[0]["pmid"] == "39000001"
    assert out[0]["full_text"].startswith("T")
    assert len(out[0]["full_text"]) <= 10

def test_assemble_web_sets_source_id_from_website_id():
    rows = [{"WEBSITE_ID":"w1","URL":"http://x","CONTENT":"hello"}]
    out = mod.assemble_web_sources(rows, max_chars=1000)
    assert out[0]["source_id"] == "w1" and out[0]["kind"] == "web" and out[0]["full_text"] == "hello"

def test_cap_sources_keeps_newest():
    src = [{"source_id":str(y),"year":y} for y in [2018,2024,2020,2023]]
    out = mod.cap_sources(src, max_n=2)
    assert {s["source_id"] for s in out} == {"2024","2023"}
