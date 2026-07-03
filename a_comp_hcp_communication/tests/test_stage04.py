from conftest import load_stage

mod = load_stage("04_synthesize.py")

GRAPH = {"competitors": [
    {"competitor": "Saxenda", "generic": "Liraglutid", "track": "A",
     "nodes": {"hcps": [], "wirkstoffe": []},
     "claims": [
         {"speaker_name": "A", "mapped": True, "s_customer_id": "c1", "wirkstoff": "Saxenda",
          "sentiment": "positive", "confidence": "high", "verbatim_quote": "q1",
          "statement": "s", "citation": {"website_id": "w1", "url": "u1"}, "verified": True},
         {"speaker_name": "B", "mapped": False, "s_customer_id": "", "wirkstoff": "Saxenda",
          "sentiment": "negative", "confidence": "low", "verbatim_quote": "q2",
          "statement": "s", "citation": {"website_id": "w2", "url": "u2"}, "verified": True},
     ]}]}


def test_distribution_split_counts_mapped_and_unmapped():
    claims = GRAPH["competitors"][0]["claims"]
    d = mod.distribution_split(claims)
    assert d["mapped"]["positive"] == 1 and d["unmapped"]["negative"] == 1
    assert d["all"]["positive"] == 1 and d["all"]["negative"] == 1


def test_flatten_claims_adds_competitor():
    flat = mod.flatten_claims(GRAPH)
    assert len(flat) == 2 and all(c["competitor"] == "Saxenda" for c in flat)
