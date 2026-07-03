import json
import os

from conftest import load_stage

mod = load_stage("03_wiki_build.py")

BLOCK = {"competitor": "Saxenda", "generic": "Liraglutid", "track": "A",
         "mapped_hcps": [{"s_customer_id": "c1", "name": "Michael Holznagel", "city": "Berlin"}],
         "sources": [{"website_id": "w1", "url": "http://a", "source_type": "VERTICAL",
                      "full_text": "Dr. Holznagel: Saxenda wirkt gut."}]}


def test_ingest_prompt_has_grounding_rules():
    p = mod.build_ingest_prompt("Saxenda", "Liraglutid", BLOCK["sources"][0])
    assert "Saxenda" in p and "verbatim" in p.lower()
    assert "only" in p.lower()  # must instruct to emit only genuine statements


def test_build_competitor_graph_nodes_and_claims():
    claims = [{"speaker_name": "Michael Holznagel", "s_customer_id": "c1", "mapped": True,
               "wirkstoff": "Saxenda", "verbatim_quote": "Saxenda wirkt gut", "statement": "x",
               "sentiment": "positive", "confidence": "high",
               "citation": {"website_id": "w1", "url": "http://a"}, "verified": True}]
    g = mod.build_competitor_graph(BLOCK, claims)
    assert g["competitor"] == "Saxenda"
    assert any(h["mapped"] for h in g["nodes"]["hcps"])
    assert g["claims"][0]["verified"] is True


def test_write_wiki_tree_creates_files(tmp_path):
    claims = [{"speaker_name": "Michael Holznagel", "s_customer_id": "c1", "mapped": True,
               "wirkstoff": "Saxenda", "verbatim_quote": "Saxenda wirkt gut", "statement": "x",
               "sentiment": "positive", "confidence": "high",
               "citation": {"website_id": "w1", "url": "http://a"}, "verified": True}]
    run_dir = str(tmp_path / "run1")
    mod.write_wiki_tree(run_dir, BLOCK, claims)
    comp_dir = os.path.join(run_dir, "Saxenda")
    assert os.path.exists(os.path.join(comp_dir, "raw", "w1.md"))
    assert os.path.exists(os.path.join(comp_dir, "wiki", "index.md"))
    assert os.path.exists(os.path.join(comp_dir, "wiki", "log.md"))
    assert os.path.exists(os.path.join(comp_dir, "schema", "knowledge_graph.json"))
    with open(os.path.join(comp_dir, "schema", "knowledge_graph.json"), encoding="utf-8") as fh:
        assert json.load(fh)["competitor"] == "Saxenda"
