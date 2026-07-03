from conftest import load_stage

mod = load_stage("01_identify_competitors.py")


def test_indication_rejects_competitor_brand_leak():
    # The LLM sometimes echoes one of the competitor brands as the "indication".
    # That self-referential leak is deterministically detectable and must be dropped.
    comps = [{"brand_name": "Saxenda", "generic_name": "Liraglutid"},
             {"brand_name": "Wegovy", "generic_name": "Semaglutid"}]
    assert mod.sanitize_indication("Wegovy", comps, "Ozempic") == ""
    assert mod.sanitize_indication("Liraglutid", comps, "Ozempic") == ""


def test_indication_rejects_client_drug():
    assert mod.sanitize_indication("Ozempic", [], "Ozempic") == ""


def test_indication_keeps_real_indication():
    comps = [{"brand_name": "Saxenda", "generic_name": "Liraglutid"}]
    assert mod.sanitize_indication("Adipositas", comps, "Ozempic") == "Adipositas"
