import os

from conftest import load_stage

mod = load_stage("05_generate_report.py")


def test_slice_examples_reports_remaining():
    shown, remaining = mod.slice_examples(list(range(20)), 15)
    assert len(shown) == 15 and remaining == 5


def test_slice_examples_no_remaining():
    shown, remaining = mod.slice_examples([1, 2, 3], 15)
    assert shown == [1, 2, 3] and remaining == 0


def test_mapped_badge_text():
    assert "mapped" in mod.mapped_badge(True).lower()
    assert "not" in mod.mapped_badge(False).lower()


SYNTH = {"indication": "Adipositas", "client_drug": "Ozempic",
         "claims": [
             {"speaker_name": "A", "mapped": True, "s_customer_id": "c1", "competitor": "Saxenda",
              "statement": "x", "verbatim_quote": "q", "sentiment": "positive", "confidence": "high",
              "citation": {"website_id": "w1", "url": "http://a"}, "verified": True}],
         "competitor_summaries": [{"competitor": "Saxenda",
             "distribution_split": {"all": {"positive": 1, "neutral": 0, "negative": 0, "ambivalent": 0},
                                    "mapped": {"positive": 1, "neutral": 0, "negative": 0, "ambivalent": 0},
                                    "unmapped": {"positive": 0, "neutral": 0, "negative": 0, "ambivalent": 0}},
             "market_view": "mv"}],
         "overall_summary": "os"}


def test_write_excel_one_row_per_claim(tmp_path):
    path = str(tmp_path / "out.xlsx")
    mod.write_excel(SYNTH, path)
    from openpyxl import load_workbook
    wb = load_workbook(path)
    ws = wb["Grounded Claims"]
    assert ws.max_row == 2  # header + 1 claim
    headers = [c.value for c in ws[1]]
    assert "Mapped" in headers and "Verbatim Quote" in headers


def test_reports_render_without_error():
    a = mod.build_report_a(SYNTH, 15, "2026-07-03 12:00:00")
    b = mod.build_report_b(SYNTH, "2026-07-03 12:00:00")
    assert "Competitor Intelligence Report" in a and "Saxenda" in a
    assert "Plain-Language Guide" in b
