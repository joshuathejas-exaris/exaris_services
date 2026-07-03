from conftest import load_stage

mod = load_stage("02_retrieve_sources.py")


def test_competitor_terms_dedupes_and_drops_empty():
    c = {"brand_name": "Saxenda", "generic_name": "Liraglutid"}
    assert mod.competitor_terms(c) == ["Saxenda", "Liraglutid"]
    assert mod.competitor_terms({"brand_name": "", "generic_name": "Liraglutid"}) == ["Liraglutid"]


def test_build_query_strings():
    c = {"brand_name": "Saxenda", "generic_name": "Liraglutid"}
    qs = mod.build_query_strings(c, "Adipositas")
    assert "Saxenda" in qs and "Liraglutid" in qs and "Saxenda Adipositas" in qs


def test_build_query_strings_drops_augmented_when_no_indication():
    c = {"brand_name": "Saxenda", "generic_name": "Liraglutid"}
    qs = mod.build_query_strings(c, None)
    assert qs == ["Saxenda", "Liraglutid"]


def test_trusted_indication_cf_spec_is_used():
    data = {"indication": "Adipositas", "indication_source": "cf_spec"}
    assert mod.trusted_indication(data) == "Adipositas"


def test_trusted_indication_llm_source_dropped():
    data = {"indication": "Pathovy", "indication_source": "llm"}
    assert mod.trusted_indication(data) is None


def test_trusted_indication_legacy_no_source_key_trusts_value():
    # Older competitors.json without the provenance key keeps prior behaviour.
    data = {"indication": "Adipositas"}
    assert mod.trusted_indication(data) == "Adipositas"


def test_matches_keywords_hit():
    assert mod.matches_keywords("Gewichtsverlust, Saxenda, Abnehmen", "weight loss",
                                ["Saxenda", "Liraglutid"])


def test_matches_keywords_generic_hit_english_col():
    assert mod.matches_keywords("", "SELECT, Liraglutide trial", ["Saxenda", "Liraglutide"])


def test_matches_keywords_no_substring_false_positive():
    # 'SELECT' must not match term 'ELE'; token-boundary only
    assert mod.matches_keywords("(SELECT), SELECT", "", ["ELE"]) is False


def test_matches_keywords_miss():
    assert mod.matches_keywords("Gewichtsverlust, Abnehmen", "weight loss", ["Mounjaro"]) is False


def test_assemble_full_text_prefers_content():
    assert mod.assemble_full_text("FULL DOC", ["chunk a", "chunk b"], 100) == "FULL DOC"


def test_assemble_full_text_falls_back_to_chunks():
    assert mod.assemble_full_text("", ["chunk a", "chunk b"], 100) == "chunk a\n\nchunk b"


def test_assemble_full_text_truncates():
    assert mod.assemble_full_text("x" * 50, [], 10) == "x" * 10


def test_dedupe_sources_keeps_first_by_website():
    rows = [{"website_id": "w1", "n": 1}, {"website_id": "w1", "n": 2}, {"website_id": "w2", "n": 3}]
    out = mod.dedupe_sources(rows)
    assert [r["website_id"] for r in out] == ["w1", "w2"]
    assert out[0]["n"] == 1
