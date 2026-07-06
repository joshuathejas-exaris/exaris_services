import pipeline_common as pc


def test_strip_json_fences_plain():
    assert pc.strip_json_fences('{"a":1}') == '{"a":1}'


def test_strip_json_fences_fenced():
    raw = '```json\n{"a": 1}\n```'
    assert pc.parse_json_object(raw) == {"a": 1}


def test_strip_json_fences_with_prose():
    raw = 'Here you go: {"a": 2} thanks'
    assert pc.parse_json_object(raw) == {"a": 2}


def test_normalize_name_strips_titles():
    # Diacritics preserved; titles/punctuation stripped.
    assert pc.normalize_name("Dr. med. Vesna Budić-Spasić") == "vesna budić-spasić"


def test_name_matches_last_and_first():
    assert pc.name_matches("Michael Holznagel", "Michael", "Holznagel") is True


def test_name_matches_last_and_initial():
    assert pc.name_matches("M. Holznagel", "Michael", "Holznagel") is True


def test_name_matches_wrong_person():
    assert pc.name_matches("Vesna Budić-Spasić", "Michael", "Holznagel") is False


def test_name_matches_last_only_is_not_enough():
    assert pc.name_matches("Holznagel", "Michael", "Holznagel") is False


def test_coi_flagged_quote_funding_and_stocks():
    q = ("Ich erhalte Forschungsgelder von der Firma Novo Nordisk, welche "
         "Semaglutid vermarktet. Ich halte auch Aktien der Firma Novo Nordisk.")
    assert pc.is_coi_disclosure(q, "receives research funding and holds stocks") is True


def test_coi_flagged_quote_advisory_board_honoraria():
    q = ("Ich erhielt Case payments bei Studien von Novo Nordisk (STEP-HF Trial), "
         "war Mitglied im Advisory Board und erhielt Speaker Honoraria für Novo "
         "Nordisk Produkte.")
    assert pc.is_coi_disclosure(q) is True


def test_coi_advisory_board_only():
    assert pc.is_coi_disclosure(
        "Tätigkeit im wissenschaftlichen Advisory Board Deutschland für Novo Nordisk.") is True


def test_coi_kept_when_clinical_claim_present():
    # Discloses honoraria BUT also makes a clinical claim -> conservative: keep.
    q = ("Ich erhalte Honorare von Novo Nordisk. Semaglutid senkt das Gewicht "
         "deutlich und verbessert den HbA1c.")
    assert pc.is_coi_disclosure(q) is False


def test_coi_plain_clinical_statement_not_flagged():
    assert pc.is_coi_disclosure(
        "Liraglutid senkt das Gewicht um 8 bis 10 Prozent.", "efficacy") is False


def test_coi_empty_not_flagged():
    assert pc.is_coi_disclosure("", "") is False


def test_coi_kept_when_cost_claim_present():
    # discloses honoraria but also states a cost view about the drug -> keep (conservative)
    assert pc.is_coi_disclosure(
        "Ich erhalte Honorare von Novo Nordisk. Mounjaro ist relativ teuer.") is False
