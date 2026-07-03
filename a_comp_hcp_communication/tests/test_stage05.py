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


def test_competitor_heading_adds_wirkstoff():
    assert mod.competitor_heading("Saxenda", "Liraglutid") == "Saxenda (Liraglutid)"


def test_competitor_heading_no_generic():
    assert mod.competitor_heading("Saxenda", "") == "Saxenda"
    # Avoid redundant '(Saxenda)' when brand == generic.
    assert mod.competitor_heading("Saxenda", "saxenda") == "Saxenda"


def test_cross_competitor_stats_counts_multi():
    claims = [
        {"speaker_name": "Dr. A", "mapped": True, "s_customer_id": "c1",
         "competitor": "Saxenda", "sentiment": "positive"},
        {"speaker_name": "Dr. A", "mapped": True, "s_customer_id": "c1",
         "competitor": "Mounjaro", "sentiment": "negative"},
        {"speaker_name": "Dr. B", "mapped": False, "s_customer_id": "",
         "competitor": "Saxenda", "sentiment": "neutral"},
    ]
    s = mod.cross_competitor_stats(claims)
    assert s["total_doctors"] == 2
    assert s["n_multi"] == 1
    assert s["multi_doctors"][0]["name"] == "Dr. A"
    assert s["multi_doctors"][0]["competitors"] == ["Mounjaro", "Saxenda"]
    assert s["mapped_doctors"] == 1 and s["unmapped_doctors"] == 1
    # Reach: Saxenda has 2 distinct doctors, Mounjaro 1 → Saxenda leads.
    assert s["competitor_reach"][0] == {"competitor": "Saxenda", "generic": "",
                                        "n_doctors": 2}
    assert s["competitor_reach"][1]["competitor"] == "Mounjaro"


SYNTH = {"indication": "Adipositas", "client_drug": "Ozempic",
         "claims": [
             {"speaker_name": "A", "mapped": True, "s_customer_id": "c1", "competitor": "Saxenda",
              "generic": "Liraglutid", "statement": "x", "verbatim_quote": "q",
              "sentiment": "positive", "confidence": "high",
              "citation": {"website_id": "w1", "url": "http://a"}, "verified": True}],
         "competitor_summaries": [{"competitor": "Saxenda", "generic": "Liraglutid",
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
    assert "Mapped" in headers and "Verbatim Quote" in headers and "Wirkstoff" in headers


def test_reports_render_without_error():
    a = mod.build_report_a(SYNTH, 15, "2026-07-03 12:00:00")
    b = mod.build_report_b(SYNTH, "2026-07-03 12:00:00")
    assert "Competitor Intelligence Report" in a and "Saxenda (Liraglutid)" in a
    assert "Cross-Competitor Insights" in a and "Per-HCP Drill-Down" not in a
    assert "Most discussed by distinct doctors" in a
    assert "Plain-Language Guide" in b
