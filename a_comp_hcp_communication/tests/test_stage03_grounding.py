from conftest import load_stage

mod = load_stage("03_wiki_build.py")

SOURCE = {"website_id": "w1", "url": "http://a",
          "full_text": 'Dr. Vesna Budić-Spasić sagt: "Saxenda wirkt gut bei Adipositas."',
          "mapped_hcps": [{"s_customer_id": "c1", "name": "Michael Holznagel", "city": "Berlin"}]}


def test_quote_grounded_true():
    assert mod.quote_grounded("Saxenda wirkt gut bei Adipositas", SOURCE["full_text"]) is True


def test_quote_grounded_false_for_invented_quote():
    assert mod.quote_grounded("Saxenda ist gefährlich", SOURCE["full_text"]) is False


def test_normalize_claim_valid():
    raw = {"speaker_name": "Vesna Budić-Spasić",
           "verbatim_quote": "Saxenda wirkt gut bei Adipositas",
           "statement": "positive on efficacy", "sentiment": "positive", "confidence": "high"}
    c = mod.normalize_claim(raw, "Saxenda", SOURCE)
    assert c["sentiment"] == "positive" and c["wirkstoff"] == "Saxenda"
    assert c["citation"]["website_id"] == "w1"


def test_normalize_claim_bad_enum_coerced():
    raw = {"speaker_name": "X Y", "verbatim_quote": "q", "statement": "s",
           "sentiment": "great", "confidence": "certain"}
    c = mod.normalize_claim(raw, "Saxenda", SOURCE)
    assert c["sentiment"] == "neutral" and c["confidence"] == "low"


def test_normalize_claim_drops_empty_quote():
    raw = {"speaker_name": "X Y", "verbatim_quote": "  ", "sentiment": "positive"}
    assert mod.normalize_claim(raw, "Saxenda", SOURCE) is None


def test_resolve_speaker_maps_matching_hcp():
    mapped, cid = mod.resolve_speaker("Michael Holznagel", SOURCE["mapped_hcps"])
    assert mapped is True and cid == "c1"


def test_resolve_speaker_unmapped_for_other_doctor():
    mapped, cid = mod.resolve_speaker("Vesna Budić-Spasić", SOURCE["mapped_hcps"])
    assert mapped is False and cid == ""


def test_filter_grounded_drops_holznagel_false_attribution():
    # The mapped HCP (Holznagel) never speaks; only Budić-Spasić does. An ingest
    # that (wrongly) attributed a Saxenda view to Holznagel with a fabricated quote
    # must be dropped because that quote is not in the source text.
    claims = [
        {"speaker_name": "Michael Holznagel", "verbatim_quote": "Ich empfehle Saxenda",
         "wirkstoff": "Saxenda", "sentiment": "positive", "confidence": "high",
         "statement": "endorses", "citation": {"website_id": "w1", "url": "http://a"}},
        {"speaker_name": "Vesna Budić-Spasić",
         "verbatim_quote": "Saxenda wirkt gut bei Adipositas",
         "wirkstoff": "Saxenda", "sentiment": "positive", "confidence": "high",
         "statement": "efficacy", "citation": {"website_id": "w1", "url": "http://a"}},
    ]
    kept = mod.filter_grounded_claims(claims, SOURCE)
    assert len(kept) == 1
    assert kept[0]["speaker_name"] == "Vesna Budić-Spasić"
