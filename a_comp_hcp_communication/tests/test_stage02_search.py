from conftest import load_stage

mod = load_stage("02_retrieve_sources.py")


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
