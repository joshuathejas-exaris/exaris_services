import string

from conftest import load_stage

mod = load_stage("02_retrieve_sources.py")


def _fields(template: str) -> set:
    return {name for _, name, _, _ in string.Formatter().parse(template) if name}


def test_sql_templates_format_with_call_site_kwargs():
    # Guards against a placeholder having no matching kwarg at its call site
    # (e.g. VECTOR_SQL_SCOPED referencing {schema_final} but the call omitting it).
    assert _fields(mod.LAYER1_SQL) == {
        "schema_final", "schema_tmp", "near_by", "is_old", "is_doctor", "kw_predicate"}
    assert _fields(mod.VECTOR_SQL_SCOPED) == {
        "vec_literal", "id_list", "schema_final", "min_similarity", "top_chunks"}
    assert _fields(mod.VECTOR_SQL_GLOBAL) == {
        "vec_literal", "schema_final", "min_similarity", "top_chunks"}
    # And each actually renders with a representative kwarg set.
    mod.LAYER1_SQL.format(schema_final="F", schema_tmp="T", near_by=1, is_old=0,
                          is_doctor=1, kw_predicate="1=1")
    mod.VECTOR_SQL_SCOPED.format(vec_literal="[]::VECTOR(FLOAT, 768)", id_list="'w1'",
                                 schema_final="F", min_similarity=0.65, top_chunks=100)
    mod.VECTOR_SQL_GLOBAL.format(vec_literal="[]::VECTOR(FLOAT, 768)", schema_final="F",
                                 min_similarity=0.65, top_chunks=100)


def test_build_mapped_hcps_dedupes_by_customer():
    rows = [
        {"S_CUSTOMER_ID": "c1", "S_FIRSTNAME": "Michael", "S_LASTNAME": "Holznagel", "S_CITY": "Berlin"},
        {"S_CUSTOMER_ID": "c1", "S_FIRSTNAME": "Michael", "S_LASTNAME": "Holznagel", "S_CITY": "Berlin"},
        {"S_CUSTOMER_ID": "c2", "S_FIRSTNAME": "Vesna", "S_LASTNAME": "Budić", "S_CITY": "Wien"},
    ]
    hcps = mod.build_mapped_hcps(rows)
    assert {h["s_customer_id"] for h in hcps} == {"c1", "c2"}
    assert any(h["name"] == "Michael Holznagel" for h in hcps)


def test_group_sources_attaches_chunks_and_content():
    rows = [
        {"WEBSITE_ID": "w1", "SOURCE_TYPE": "VERTICAL", "URL_VALUE": "http://a",
         "CONTENT": "FULL A", "S_CUSTOMER_ID": "c1"},
    ]
    keep = {"w1"}
    chunks = {"w1": [{"text": "chunk", "similarity": 0.9}]}
    srcs = mod.group_sources(rows, keep, chunks, max_chars=100)
    assert len(srcs) == 1
    assert srcs[0]["website_id"] == "w1"
    assert srcs[0]["full_text"] == "FULL A"
    assert srcs[0]["matched_chunks"][0]["similarity"] == 0.9
