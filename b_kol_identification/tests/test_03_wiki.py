import importlib.util, os
_S = os.path.join(os.path.dirname(__file__), "..", "03_wiki_build.py")
_spec = importlib.util.spec_from_file_location("wiki", _S)
mod = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(mod)

def test_quote_grounded_true_on_substring_ignoring_ws_case():
    assert mod.quote_grounded("the  PATIENT improved", "... The patient improved a lot ...") is True

def test_quote_grounded_false_when_absent():
    assert mod.quote_grounded("never said this", "some other text") is False

def test_ingest_prompt_web_demands_active_engagement_and_schema():
    p = mod.build_ingest_prompt("web", "Obesity", ["Obesity"], "Anna Berg", "text")
    assert "Obesity" in p and "Anna Berg" in p
    assert "verbatim_quote" in p and "themes" in p and "mentioned_hcps" in p

def test_ingest_prompt_pubmed_frames_article_relevance():
    p = mod.build_ingest_prompt("pubmed", "Obesity", ["Obesity"], "Anna Berg", "text")
    assert "article" in p.lower()

def test_normalise_claim_coerces_lists():
    c = mod.normalise_claim({"verbatim_quote":"q","statement":"s","sentiment":"positive",
                             "themes":"CF_OBESITY","mentioned_hcps":None,"confidence":"high"})
    assert c["themes"] == ["CF_OBESITY"] and c["mentioned_hcps"] == []

def test_resolve_mentions_matches_roster():
    roster = [{"s_customer_id":"77","firstname":"Anna","lastname":"Berg"}]
    out = mod.resolve_mentions(["Prof. Anna Berg","Nobody Here"], roster)
    assert {"name":"Prof. Anna Berg","s_customer_id":"77"} in out
    assert any(m["s_customer_id"] == "" for m in out)   # unmatched kept, no id

def test_source_is_relevant_needs_verified_claim():
    assert mod.source_is_relevant([{"verified":False}]) is False
    assert mod.source_is_relevant([{"verified":True}]) is True

def test_process_source_end_to_end_with_mocks():
    # ingest returns one claim; grounding passes; verify returns true
    class FakeBedrock: pass
    calls = {"n": 0}
    def fake_call(bedrock, model_id, prompt, temperature=0.0, max_tokens=4096):
        calls["n"] += 1
        if "verbatim_quote" in prompt:   # ingest
            return {"claims":[{"verbatim_quote":"patient improved","statement":"engaged",
                    "sentiment":"positive","themes":["CF_OBESITY"],"mentioned_hcps":[],"confidence":"high"}]}
        return {"verified": True}        # verify
    mod.call_bedrock_json = fake_call    # monkeypatch the imported name
    src = {"source_id":"w1","kind":"web","url":"u","full_text":"the patient improved after therapy"}
    hcp = {"name":"Anna Berg","s_customer_id":"10"}
    cfg = {"ingest_model_id":"m1","verify_model_id":"m2","extraction_max_tokens":4096}
    out = mod.process_source(src, hcp, "Obesity", ["Obesity"], FakeBedrock(), cfg)
    assert out is not None and out["claims"][0]["verified"] is True
    assert out["claims"][0]["source_id"] == "w1"

def test_build_pmid_years_maps_verified_pubmed_claims_only():
    claims = [
        {"kind": "pubmed", "source_id": "p1", "year": 2015},
        {"kind": "pubmed", "source_id": "p2", "year": 2018},
        {"kind": "web",    "source_id": "w1", "year": None},
        {"kind": "pubmed", "source_id": "p3"},               # no year -> skipped
    ]
    assert mod.build_pmid_years(claims) == {"p1": 2015, "p2": 2018}
