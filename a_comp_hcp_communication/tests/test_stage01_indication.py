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


def test_resolve_indication_cf_is_authoritative_ignores_llm():
    # CF row 1 wins; the LLM's 'Pathovy' guess is ignored entirely.
    ind, src = mod.resolve_indication("Adipositas", "Pathovy", True, [], "Ozempic")
    assert ind == "Adipositas" and src == "cf_spec"


def test_resolve_indication_cf_empty_yields_none_not_llm():
    ind, src = mod.resolve_indication("", "Pathovy", True, [], "Ozempic")
    assert ind == "" and src == "none"


def test_resolve_indication_knowledge_run_uses_llm():
    ind, src = mod.resolve_indication(None, "Adipositas", False, [], "Ozempic")
    assert ind == "Adipositas" and src == "llm"


def test_resolve_indication_drug_collision_dropped():
    comps = [{"brand_name": "Wegovy", "generic_name": "Semaglutid"}]
    ind, src = mod.resolve_indication("Wegovy", None, True, comps, "Ozempic")
    assert ind == "" and src == "none"
