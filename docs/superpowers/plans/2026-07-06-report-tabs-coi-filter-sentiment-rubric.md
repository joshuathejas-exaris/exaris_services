# Tabbed Report, COI Filtering, Sentiment Rubric — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the Competitor Intelligence HTML report into a tabbed layout with an overall sentiment overview, strip conflict-of-interest disclosures from the report and Excel, and give Stage 03 a sentiment rubric plus drop-instrumentation.

**Architecture:** All work is in `a_comp_hcp_communication/`. A new shared `is_coi_disclosure` predicate (in `pipeline_common.py`) is applied both at render time (Stage 05, cleaning existing data with no re-run) and at ingest (Stage 03, safety net). Stage 05's `build_report_a` is refactored into panel builders wrapped by a progressive-enhancement tab system (all panels visible without JS). Stage 03's ingest prompt gains a COI-exclusion block and a sentiment rubric, and the ingest→verify funnel is instrumented to persist dropped claims.

**Tech Stack:** Python 3, `configparser`, `openpyxl`, inline HTML/CSS/JS (no external assets), `pytest` with the `conftest.load_stage` loader (Snowflake/Bedrock/ONNX never touched by tests).

## Global Constraints

- All HTML output must be fully offline: inline CSS, inline SVG, inline vanilla JS — **no external assets, CDNs, fonts, or network calls**.
- The report must **degrade gracefully without JavaScript**: with JS disabled, every panel is visible (full-scroll fallback) — nothing is hidden by default CSS.
- Tests must not require AWS/Snowflake/ONNX; use the existing `conftest.load_stage` pattern and `import pipeline_common as pc`.
- Run tests from the service dir: `cd a_comp_hcp_communication && python -m pytest -q` (pytest.ini sets `testpaths = tests`).
- COI scope is **financial conflict-of-interest disclosures only** (funding/grants, stock/shares, advisory-board membership, consulting fees, speaker honoraria, case/study payments). The filter is **conservative**: a statement that both discloses funding *and* makes a clinical claim about the drug is **kept**.
- The sentiment rubric must **never instruct the model to invent negativity** — it defines labels and requires judging only from the quote.
- Preserve existing public function signatures used by tests: `build_report_a(synthesis, examples_per_section, timestamp)`, `write_excel(synthesis, path)`, `filter_grounded_claims(claims, source)`, `build_ingest_prompt(wirkstoff, generic, source)`, `write_wiki_tree(run_dir, block, claims[, dropped])`.
- Commit message trailer on every commit:
  `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`

---

### Task 1: `is_coi_disclosure` predicate (shared)

**Files:**
- Modify: `a_comp_hcp_communication/pipeline_common.py`
- Test: `a_comp_hcp_communication/tests/test_pipeline_common.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `is_coi_disclosure(quote: str, statement: str = "") -> bool` — True when the combined text is a financial COI disclosure carrying **no** clinical signal.

- [ ] **Step 1: Write the failing tests**

Append to `a_comp_hcp_communication/tests/test_pipeline_common.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd a_comp_hcp_communication && python -m pytest tests/test_pipeline_common.py -k coi -v`
Expected: FAIL with `AttributeError: module 'pipeline_common' has no attribute 'is_coi_disclosure'`

- [ ] **Step 3: Implement `is_coi_disclosure`**

In `a_comp_hcp_communication/pipeline_common.py`, add after the existing module-level regexes (after `_WS_RE`):

```python
# Financial conflict-of-interest / disclosure language (DE + EN). Note: study/trial
# and "board" membership terms are disclosure signals here, so they are NOT treated
# as clinical signals below.
_COI_PATTERNS = re.compile(
    r"forschungsgeld|forschungsförder|drittmittel|"
    r"\baktien\b|\bshares?\b|\bstocks?\b|"
    r"advisory board|\bbeirat\b|"
    r"honorar|honoraria|vortragshonorar|"
    r"case payment|"
    r"\bberater|consult(?:ing|ant)|"
    r"interessenkonflikt|conflict of interest|declaration of interest|"
    r"finanzielle (?:zuwendung|unterstützung)|"
    r"research (?:funding|grant)|grants? from",
    re.IGNORECASE,
)

# Clinical-signal vocabulary: if present, the statement carries real drug content and
# is kept even if it also mentions a financial tie (conservative). Deliberately
# excludes generic words like "study"/"board" that appear inside disclosures.
_CLINICAL_SIGNAL = re.compile(
    r"gewicht|weight|abnehm|"
    r"nebenwirkung|side.?effect|verträglich|toleran|tolerab|"
    r"wirksam|wirkung|efficac|effektiv|"
    r"blutzucker|hba1c|gluk|glyk|"
    r"appetit|sättig|satiety|"
    r"dosier|dosis|dosing|"
    r"übelkeit|erbrechen|durchfall|muskel|"
    r"reduktion|reduzier|verbesser|improve|"
    r"empfehl|prescrib|verordn",
    re.IGNORECASE,
)


def is_coi_disclosure(quote: str, statement: str = "") -> bool:
    """True when the text is primarily a financial conflict-of-interest disclosure.

    Conservative: fires only when a disclosure pattern matches AND no clinical-signal
    vocabulary is present, so a statement that both discloses a financial tie and makes
    a genuine claim about the drug is kept.
    """
    text = f"{quote or ''} {statement or ''}"
    if not _COI_PATTERNS.search(text):
        return False
    if _CLINICAL_SIGNAL.search(text):
        return False
    return True
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd a_comp_hcp_communication && python -m pytest tests/test_pipeline_common.py -k coi -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add a_comp_hcp_communication/pipeline_common.py a_comp_hcp_communication/tests/test_pipeline_common.py
git commit -m "feat(pipeline_common): add conservative is_coi_disclosure predicate

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Filter COI disclosures out of the report and Excel (Stage 05)

**Files:**
- Modify: `a_comp_hcp_communication/05_generate_report.py` (imports; add `_visible_claims`; wire into `build_report_a` and `write_excel`)
- Test: `a_comp_hcp_communication/tests/test_stage05.py`

**Interfaces:**
- Consumes: `pipeline_common.is_coi_disclosure` (Task 1).
- Produces: `_visible_claims(claims: List[dict]) -> List[dict]` — claim list with COI disclosures removed (logged, not silent).

- [ ] **Step 1: Write the failing test**

Append to `a_comp_hcp_communication/tests/test_stage05.py`:

```python
def test_visible_claims_drops_coi():
    claims = [
        {"speaker_name": "A", "competitor": "Wegovy",
         "verbatim_quote": "Semaglutid senkt das Gewicht deutlich.",
         "statement": "efficacy"},
        {"speaker_name": "B", "competitor": "Wegovy",
         "verbatim_quote": ("Ich erhalte Forschungsgelder von Novo Nordisk. "
                            "Ich halte auch Aktien der Firma."),
         "statement": "receives funding and holds stocks"},
    ]
    out = mod._visible_claims(claims)
    assert len(out) == 1 and out[0]["speaker_name"] == "A"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd a_comp_hcp_communication && python -m pytest tests/test_stage05.py::test_visible_claims_drops_coi -v`
Expected: FAIL with `AttributeError: ... has no attribute '_visible_claims'`

- [ ] **Step 3: Implement `_visible_claims` and wire it in**

In `a_comp_hcp_communication/05_generate_report.py`, add the import near the top (after `sys.path.insert(...)` at line 27, which puts the service dir on the path):

```python
from pipeline_common import is_coi_disclosure  # noqa: E402
```

Add this helper in the "Claim grouping" section (near `claims_by_competitor`):

```python
def _visible_claims(claims: List[dict]) -> List[dict]:
    """Drop financial COI disclosures so they never reach the report or Excel."""
    out = []
    for c in claims:
        if is_coi_disclosure(c.get("verbatim_quote", ""), c.get("statement", "")):
            log.info("Filtered COI disclosure: %s / %s",
                     c.get("speaker_name"), c.get("competitor"))
            continue
        out.append(c)
    return out
```

In `build_report_a`, change the claims line (currently `claims = synthesis.get("claims", []) or []`) to:

```python
    claims = _visible_claims(synthesis.get("claims", []) or [])
```

In `write_excel`, change the per-claim loop header (currently `for c in synthesis.get("claims", []):`) to:

```python
    for c in _visible_claims(synthesis.get("claims", []) or []):
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd a_comp_hcp_communication && python -m pytest tests/test_stage05.py -q`
Expected: PASS (existing tests + new one; `test_write_excel_one_row_per_claim` still passes because its single claim is not a COI disclosure)

- [ ] **Step 5: Commit**

```bash
git add a_comp_hcp_communication/05_generate_report.py a_comp_hcp_communication/tests/test_stage05.py
git commit -m "feat(stage05): filter COI disclosures from report and Excel

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Overview helpers — `overall_distribution` and `tab_id` (Stage 05)

**Files:**
- Modify: `a_comp_hcp_communication/05_generate_report.py` (add `import re`; add two helpers)
- Test: `a_comp_hcp_communication/tests/test_stage05.py`

**Interfaces:**
- Consumes: `SENTIMENT_LABELS` (module constant).
- Produces:
  - `overall_distribution(summaries: List[dict]) -> Dict[str, int]` — sums each sentiment across every summary's `distribution_split["all"]`.
  - `tab_id(label: str) -> str` — DOM id like `tab-saxenda-liraglutid`.

- [ ] **Step 1: Write the failing tests**

Append to `a_comp_hcp_communication/tests/test_stage05.py`:

```python
def test_overall_distribution_sums_across_competitors():
    summaries = [
        {"distribution_split": {"all": {"positive": 2, "neutral": 1,
                                        "negative": 0, "ambivalent": 0}}},
        {"distribution_split": {"all": {"positive": 3, "neutral": 0,
                                        "negative": 1, "ambivalent": 2}}},
    ]
    assert mod.overall_distribution(summaries) == {
        "positive": 5, "neutral": 1, "negative": 1, "ambivalent": 2}


def test_tab_id_slugifies():
    assert mod.tab_id("Saxenda (Liraglutid)") == "tab-saxenda-liraglutid"
    assert mod.tab_id("Insgesamt") == "tab-insgesamt"
    assert mod.tab_id("Most active voices") == "tab-most-active-voices"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd a_comp_hcp_communication && python -m pytest tests/test_stage05.py -k "overall_distribution or tab_id" -v`
Expected: FAIL with `AttributeError` for `overall_distribution` / `tab_id`

- [ ] **Step 3: Implement the helpers**

In `a_comp_hcp_communication/05_generate_report.py`, add `import re` to the import block (alphabetically near `import os`). Add both helpers in the "Pure helpers (unit-tested)" section:

```python
def overall_distribution(summaries: List[dict]) -> Dict[str, int]:
    """Aggregate sentiment counts across all competitor summaries (the 'all' split)."""
    total = {s: 0 for s in SENTIMENT_LABELS}
    for cs in summaries:
        d = (cs.get("distribution_split") or {}).get("all", {})
        for s in SENTIMENT_LABELS:
            total[s] += int(d.get(s, 0) or 0)
    return total


def tab_id(label: str) -> str:
    """Slugify a tab label into a stable DOM id (e.g. 'tab-saxenda-liraglutid')."""
    slug = re.sub(r"[^a-z0-9]+", "-", (label or "").lower()).strip("-")
    return "tab-" + (slug or "x")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd a_comp_hcp_communication && python -m pytest tests/test_stage05.py -k "overall_distribution or tab_id" -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add a_comp_hcp_communication/05_generate_report.py a_comp_hcp_communication/tests/test_stage05.py
git commit -m "feat(stage05): add overall_distribution and tab_id helpers

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Tabbed layout for the Competitor Intelligence report (Stage 05)

**Files:**
- Modify: `a_comp_hcp_communication/05_generate_report.py` (extend `BASE_CSS`; add panel builders + `_render_tabs` + `TAB_SCRIPT`; rewrite `build_report_a`)
- Test: `a_comp_hcp_communication/tests/test_stage05.py`

**Interfaces:**
- Consumes: `overall_distribution`, `tab_id`, `_visible_claims`, `cross_competitor_stats`, `svg_distribution_chart`, `competitor_heading`, `claims_by_competitor`, `claims_by_hcp`, `_claim_example_html`, `slice_examples`, `mapped_badge`, `footer_html` (all existing or from Tasks 2–3).
- Produces: rewritten `build_report_a(synthesis, examples_per_section, timestamp) -> str` (same signature) rendering a tab bar + one panel per tab. New module-level constant `TAB_SCRIPT` and helper `_render_tabs(sections: List[Tuple[str, str]]) -> str`.

- [ ] **Step 1: Write the failing test**

Append to `a_comp_hcp_communication/tests/test_stage05.py`:

```python
def test_report_a_has_tabs_and_panels():
    a = mod.build_report_a(SYNTH, 15, "2026-07-03 12:00:00")
    # progressive-enhancement tab scaffolding
    assert 'class="tabs"' in a
    assert "function showTab" in a
    assert "js-tabs" in a
    # the overview tab and a per-competitor tab exist as nav + panel
    assert 'href="#tab-insgesamt"' in a
    assert 'id="tab-insgesamt"' in a
    assert 'href="#tab-saxenda-liraglutid"' in a
    assert 'id="tab-saxenda-liraglutid"' in a
    # fixed tabs present
    for label in ("Insgesamt", "Doctors weighing", "Most active voices", "Methodology"):
        assert label in a
    # overview shows an aggregate sentiment chart and exec summary text
    assert "Overall sentiment" in a
    # substrings the existing render test relies on still present
    assert "Cross-Competitor Insights" in a
    assert "Most discussed by distinct doctors" in a
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd a_comp_hcp_communication && python -m pytest tests/test_stage05.py::test_report_a_has_tabs_and_panels -v`
Expected: FAIL (no `class="tabs"` / `showTab` in current output)

- [ ] **Step 3: Extend `BASE_CSS`**

In `a_comp_hcp_communication/05_generate_report.py`, append these rules to the `BASE_CSS` string (before its closing `"""`):

```css
.tabs { display: flex; flex-wrap: wrap; gap: 4px; border-bottom: 2px solid #e5e9ee;
        margin: 24px 0 8px; }
.tab { padding: 8px 14px; border: 1px solid transparent; border-bottom: none;
       border-radius: 8px 8px 0 0; background: transparent; color: #6b7280;
       font-size: 14px; font-weight: 600; cursor: pointer; text-decoration: none; }
.tab:hover { color: #1565c0; }
.tab.active { background: #fcfdfe; border-color: #e5e9ee; color: #1565c0;
              margin-bottom: -2px; }
body.js-tabs .panel { display: none; }
body.js-tabs .panel.active { display: block; }
```

- [ ] **Step 4: Add `TAB_SCRIPT` and `_render_tabs`**

In `a_comp_hcp_communication/05_generate_report.py`, add near `BASE_CSS`:

```python
TAB_SCRIPT = """
<script>
function showTab(id){
  var ps = document.querySelectorAll('.panel');
  for (var i = 0; i < ps.length; i++){ ps[i].classList.toggle('active', ps[i].id === id); }
  var ts = document.querySelectorAll('.tab');
  for (var j = 0; j < ts.length; j++){ ts[j].classList.toggle('active', ts[j].getAttribute('href') === '#' + id); }
  return false;
}
document.addEventListener('DOMContentLoaded', function(){
  document.body.classList.add('js-tabs');
  var f = document.querySelector('.panel');
  if (f) { showTab(f.id); }
});
</script>
"""


def _render_tabs(sections: "List[Tuple[str, str]]") -> str:
    """sections: list of (label, panel_html). First is active on load. Degrades to a
    full-scroll page when JS is disabled (no panel is hidden by default CSS)."""
    nav = ['<div class="tabs" role="tablist">']
    panels = []
    for i, (label, body) in enumerate(sections):
        tid = tab_id(label)
        active = " active" if i == 0 else ""
        nav.append(f'<a class="tab{active}" role="tab" id="{tid}-btn" href="#{tid}" '
                   f'aria-controls="{tid}" onclick="return showTab(\'{tid}\')">'
                   f"{esc(label)}</a>")
        panels.append(f'<section class="panel{active}" role="tabpanel" id="{tid}" '
                      f'aria-labelledby="{tid}-btn">\n{body}\n</section>')
    nav.append("</div>")
    return "\n".join(nav) + "\n" + "\n".join(panels)
```

- [ ] **Step 5: Add panel builders**

In `a_comp_hcp_communication/05_generate_report.py`, add these functions above `build_report_a`:

```python
def _overview_sentiment_table(summaries: List[dict]) -> str:
    rows = ['<div class="scroll"><table>',
            "<tr><th>Competitor</th><th>positive</th><th>neutral</th>"
            "<th>negative</th><th>ambivalent</th></tr>"]
    for cs in summaries:
        d = (cs.get("distribution_split") or {}).get("all", {})
        rows.append(
            f"<tr><td>{esc(competitor_heading(cs.get('competitor', ''), cs.get('generic', '')))}</td>"
            f"<td>{int(d.get('positive', 0) or 0)}</td>"
            f"<td>{int(d.get('neutral', 0) or 0)}</td>"
            f"<td>{int(d.get('negative', 0) or 0)}</td>"
            f"<td>{int(d.get('ambivalent', 0) or 0)}</td></tr>")
    rows.append("</table></div>")
    return "".join(rows)


def _panel_overview(summaries, claims, overall, stats) -> str:
    n_mapped = sum(1 for c in claims if c.get("mapped"))
    n_unmapped = len(claims) - n_mapped
    p = []
    # KPI tiles
    p.append('<div class="kpis">'
             f'<div class="kpi"><div class="n">{len(summaries)}</div>'
             '<div class="l">Competitors</div></div>'
             f'<div class="kpi"><div class="n">{stats["total_doctors"]}</div>'
             '<div class="l">Doctors with a grounded statement</div></div>'
             f'<div class="kpi"><div class="n">{len(claims)}</div>'
             '<div class="l">Grounded statements</div></div>'
             f'<div class="kpi"><div class="n">{n_mapped} / {n_unmapped}</div>'
             '<div class="l">Mapped / not mapped</div></div>'
             "</div>")
    # Overall sentiment overview
    p.append("<h2>Overall sentiment across all competitors</h2>")
    p.append('<div class="card">')
    p.append(svg_distribution_chart(overall_distribution(summaries)))
    p.append(_overview_sentiment_table(summaries))
    p.append("</div>")
    # Executive summary
    p.append("<h2>Executive Summary</h2>")
    p.append(f"<p>{esc(overall)}</p>" if overall
             else '<p class="muted">No overall summary was produced for this run.</p>')
    # Cross-competitor KPIs + reach
    p.append("<h2>Cross-Competitor Insights</h2>")
    p.append('<div class="kpis">'
             f'<div class="kpi"><div class="n">{stats["total_doctors"]}</div>'
             '<div class="l">Distinct doctors</div></div>'
             f'<div class="kpi"><div class="n">{stats["n_multi"]}</div>'
             '<div class="l">Discuss 2+ competitors</div></div>'
             f'<div class="kpi"><div class="n">{stats["mapped_doctors"]}</div>'
             '<div class="l">Mapped HCPs</div></div>'
             f'<div class="kpi"><div class="n">{stats["unmapped_doctors"]}</div>'
             '<div class="l">Not-mapped doctors</div></div>'
             "</div>")
    reach = stats["competitor_reach"]
    p.append("<h3>Reach — distinct doctors per competitor</h3>")
    if not reach:
        p.append('<p class="muted">No competitors had grounded statements this run.</p>')
    else:
        top = reach[0]
        p.append('<p>Most discussed by distinct doctors: '
                 f'<strong>{esc(competitor_heading(top["competitor"], top["generic"]))}</strong> '
                 f'— {top["n_doctors"]} doctor(s).</p>')
        p.append('<div class="scroll"><table>')
        p.append("<tr><th>Competitor</th><th>Distinct doctors</th></tr>")
        for r in reach:
            p.append(f"<tr><td>{esc(competitor_heading(r['competitor'], r['generic']))}</td>"
                     f"<td>{r['n_doctors']}</td></tr>")
        p.append("</table></div>")
    return "\n".join(p)


def _panel_competitor(cs, by_comp, examples_per_section) -> str:
    competitor = cs.get("competitor", "")
    dist = (cs.get("distribution_split") or {}).get("all", {})
    market_view = (cs.get("market_view") or "").strip()
    p = ['<div class="card">', svg_distribution_chart(dist)]
    if market_view:
        p.append(f"<p>{esc(market_view)}</p>")
    else:
        p.append('<p class="muted">No market-view narrative available.</p>')
    comp_claims = by_comp.get(competitor, [])
    shown, remaining = slice_examples(comp_claims, examples_per_section)
    if shown:
        p.append("<p><strong>Grounded HCP statements</strong></p>")
        for c in shown:
            p.append(_claim_example_html(c))
        if remaining > 0:
            p.append(f'<p class="more">+ {remaining} more statement(s) for '
                     f"{esc(competitor)} — see the Excel export.</p>")
    else:
        p.append('<p class="muted">No grounded statements for this competitor.</p>')
    p.append("</div>")
    return "\n".join(p)


def _panel_multi(stats, examples_per_section) -> str:
    p = ['<p class="muted">Voices comparing several competitor drugs are the most '
         "commercially interesting — they signal where the market conversation "
         "overlaps.</p>"]
    shown_multi, remaining_multi = slice_examples(stats["multi_doctors"], examples_per_section)
    if not shown_multi:
        p.append('<p class="muted">No doctor discussed more than one competitor in this run.</p>')
        return "\n".join(p)
    p.append('<div class="scroll"><table>')
    p.append("<tr><th>Doctor</th><th></th><th>Competitors</th>"
             "<th># statements</th><th>Sentiment mix</th></tr>")
    for d in shown_multi:
        comps = ", ".join(esc(x) for x in d["competitors"])
        senti = ", ".join(f"{k}:{v}" for k, v in d["sentiments"].items() if k)
        cid = f' <span class="meta">{esc(d["s_customer_id"])}</span>' if d["s_customer_id"] else ""
        p.append(f"<tr><td><strong>{esc(d['name'])}</strong>{cid}</td>"
                 f"<td>{mapped_badge(d['mapped'])}</td>"
                 f"<td>{comps} <span class='meta'>({len(d['competitors'])})</span></td>"
                 f"<td>{d['n_statements']}</td>"
                 f"<td>{esc(senti)}</td></tr>")
    p.append("</table></div>")
    if remaining_multi > 0:
        p.append(f'<p class="more">+ {remaining_multi} more multi-competitor doctor(s) '
                 "— see the Excel export.</p>")
    return "\n".join(p)


def _panel_top_voices(stats) -> str:
    p = ['<p class="muted">Doctors with the most grounded statements this run.</p>']
    top = [d for d in stats["top_voices"] if d["n_statements"] > 1][:10]
    if not top:
        p.append('<p class="muted">No doctor made more than one grounded statement.</p>')
        return "\n".join(p)
    p.append('<div class="scroll"><table>')
    p.append("<tr><th>Doctor</th><th></th><th># statements</th><th>Competitors covered</th></tr>")
    for d in top:
        p.append(f"<tr><td><strong>{esc(d['name'])}</strong></td>"
                 f"<td>{mapped_badge(d['mapped'])}</td>"
                 f"<td>{d['n_statements']}</td>"
                 f"<td>{len(d['competitors'])}</td></tr>")
    p.append("</table></div>")
    return "\n".join(p)


def _panel_methodology() -> str:
    return ("<p>Doctors were included only where a database gate confirmed a genuine "
            "doctor discussing the drug's topic, then an LLM extracted verbatim "
            "statements. Every statement is grounded twice: its quote must be present "
            "verbatim in the source, and an independent verification pass confirms the "
            "named doctor actually expresses that view. Mapped statements resolve to a "
            "known HCP record; unmapped ones are genuine doctor statements from the "
            "same sources without a customer match. Financial conflict-of-interest "
            "disclosures (funding, shares, advisory-board roles, honoraria) are "
            "excluded from this report and the Excel export.</p>")
```

- [ ] **Step 6: Rewrite `build_report_a`**

Replace the entire body of `build_report_a` in `a_comp_hcp_communication/05_generate_report.py` with:

```python
def build_report_a(synthesis: dict, examples_per_section: int, timestamp: str) -> str:
    client_drug = (synthesis.get("client_drug") or "").strip() or "the client drug"
    indication = (synthesis.get("indication") or "").strip() or "unspecified"
    claims = _visible_claims(synthesis.get("claims", []) or [])
    summaries = synthesis.get("competitor_summaries", []) or []
    overall = (synthesis.get("overall_summary") or "").strip()

    by_comp = claims_by_competitor(claims)
    stats = cross_competitor_stats(claims)

    # Header (stays above the tab bar, always visible)
    head: List[str] = []
    head.append("<h1>Competitor Intelligence Report</h1>")
    head.append(f'<p class="subtitle">Client drug: <strong>{esc(client_drug)}</strong> · '
                f"Indication: <strong>{esc(indication)}</strong></p>")
    head.append(f'<p class="meta">Generated {esc(timestamp)}</p>')
    head.append(f'<p class="muted">Legend: {mapped_badge(True)} a speaker resolved to a '
                f"known HCP customer record · {mapped_badge(False)} a doctor genuinely "
                "quoted in a source but not in our HCP records.</p>")

    # Tabs
    sections: "List[Tuple[str, str]]" = [
        ("Insgesamt", _panel_overview(summaries, claims, overall, stats))]
    for cs in summaries:
        label = competitor_heading(cs.get("competitor", ""), cs.get("generic", ""))
        sections.append((label, _panel_competitor(cs, by_comp, examples_per_section)))
    sections.append(("Doctors weighing", _panel_multi(stats, examples_per_section)))
    sections.append(("Most active voices", _panel_top_voices(stats)))
    sections.append(("Methodology", _panel_methodology()))

    body = "\n".join(head) + "\n" + _render_tabs(sections) + "\n" + TAB_SCRIPT + "\n" \
        + footer_html(timestamp)
    return html_document("Competitor Intelligence Report", body)
```

> Note: the "Cross-Competitor Insights" heading and the "Reach — distinct doctors per competitor" table now live inside the Insgesamt panel; "Doctors weighing" and "Most active voices" are their own tabs. The footer is rendered once, always visible, after the panels.

- [ ] **Step 7: Run the full Stage 05 suite**

Run: `cd a_comp_hcp_communication && python -m pytest tests/test_stage05.py -q`
Expected: PASS — including `test_reports_render_without_error` (asserts `"Saxenda (Liraglutid)"`, `"Cross-Competitor Insights"`, `"Most discussed by distinct doctors"` present and `"Per-HCP Drill-Down"` absent) and the new `test_report_a_has_tabs_and_panels`.

- [ ] **Step 8: Commit**

```bash
git add a_comp_hcp_communication/05_generate_report.py a_comp_hcp_communication/tests/test_stage05.py
git commit -m "feat(stage05): tabbed report layout with overall-sentiment overview

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Ingest prompt — COI exclusion + sentiment rubric (Stage 03)

**Files:**
- Modify: `a_comp_hcp_communication/03_wiki_build.py` (`build_ingest_prompt`)
- Test: `a_comp_hcp_communication/tests/test_stage03_wiki.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: updated `build_ingest_prompt(wirkstoff, generic, source) -> str` (same signature) whose text includes a COI-exclusion block and a four-label sentiment rubric.

- [ ] **Step 1: Write the failing test**

Append to `a_comp_hcp_communication/tests/test_stage03_wiki.py`:

```python
def test_ingest_prompt_excludes_coi_and_defines_sentiment():
    p = mod.build_ingest_prompt("Saxenda", "Liraglutid", BLOCK["sources"][0])
    low = p.lower()
    # COI exclusion present
    assert "advisory board" in low
    assert "honorar" in low or "honoraria" in low
    assert "aktien" in low or "shares" in low or "stock" in low
    # sentiment rubric defines negative as covering drawbacks
    assert "drawback" in low or "side-effect" in low or "side effect" in low
    assert "never invent" in low or "invent" in low
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd a_comp_hcp_communication && python -m pytest tests/test_stage03_wiki.py::test_ingest_prompt_excludes_coi_and_defines_sentiment -v`
Expected: FAIL (current prompt has none of this)

- [ ] **Step 3: Update `build_ingest_prompt`**

In `a_comp_hcp_communication/03_wiki_build.py`, replace the `build_ingest_prompt` function body with (keeps the existing structure and JSON shape, adds an EXCLUSION block and a SENTIMENT rubric):

```python
def build_ingest_prompt(wirkstoff: str, generic: str, source: dict) -> str:
    names = wirkstoff if not generic else f"{wirkstoff} (Wirkstoff: {generic})"
    return f"""You are a pharmaceutical medical-affairs analyst. Extract, from the \
document below, ONLY concrete statements that a NAMED doctor makes ABOUT the drug \
"{names}".

A statement qualifies ONLY IF the same named doctor is the one expressing a view \
about the drug in the text. If a doctor is merely named on the page while the drug \
is mentioned elsewhere, and that doctor does not actually say anything about the \
drug, DO NOT extract it. Do not infer, translate, or invent. Every "verbatim_quote" \
must be copied character-for-character from the document, in its original language.

EXCLUDE (never extract), even when a named doctor says them:
- Conflict-of-interest / financial-disclosure statements: research funding or grants, \
stock or share ownership, advisory-board membership, consulting fees, speaker \
honoraria, and case/study payments or other financial ties to a manufacturer \
(e.g. "Ich erhalte Forschungsgelder von ...", "Ich halte Aktien ...", "war Mitglied \
im Advisory Board", "Speaker Honoraria", "Case payments").
Extract only statements expressing a view or clinical claim ABOUT the drug itself — \
efficacy, safety/tolerability, dosing, mechanism, positioning, patient experience, or \
comparison with other drugs.

Assign "sentiment" by the doctor's stance TOWARD the drug:
- "positive" — favourable: efficacy, benefit, endorsement, good tolerability.
- "negative" — unfavourable OR reports a material drawback: significant side-effect \
burden, safety risk, cost concern, efficacy limitation, weight regain, need for \
lifelong therapy, muscle-mass loss, or an explicitly critical view.
- "ambivalent" — names a benefit AND a drawback together.
- "neutral" — purely descriptive/factual with no benefit or drawback implied \
(e.g. approval status, dosing schedule, mechanism, brand/generic identity).
Judge only from the quote; never invent a stance the text does not support. Extract \
critical statements with the SAME fidelity as positive ones.

Document (source_url: {source.get('url') or '(none)'}):
\"\"\"
{source.get('full_text', '')}
\"\"\"

Respond with ONLY a JSON object in exactly this shape:
{{
  "claims": [
    {{"speaker_name": "<doctor named in the text>",
      "verbatim_quote": "<exact span copied from the document>",
      "statement": "<one short line: what they say about {wirkstoff}>",
      "sentiment": "positive|neutral|negative|ambivalent",
      "confidence": "high|medium|low"}}
  ]
}}
If there are no qualifying statements, return {{"claims": []}}."""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd a_comp_hcp_communication && python -m pytest tests/test_stage03_wiki.py -v`
Expected: PASS — including the existing `test_ingest_prompt_has_grounding_rules` (still finds `"Saxenda"`, `"verbatim"`, `"only"`) and the new test.

- [ ] **Step 5: Commit**

```bash
git add a_comp_hcp_communication/03_wiki_build.py a_comp_hcp_communication/tests/test_stage03_wiki.py
git commit -m "feat(stage03): COI exclusion + sentiment rubric in ingest prompt

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Drop-instrumentation + ingest-time COI safety net (Stage 03)

**Files:**
- Modify: `a_comp_hcp_communication/03_wiki_build.py` (import `is_coi_disclosure`; add `split_grounded`; refactor `filter_grounded_claims` to reuse it; update `ingest_source`, `write_wiki_tree`, `main`)
- Test: `a_comp_hcp_communication/tests/test_stage03_grounding.py`

**Interfaces:**
- Consumes: `pipeline_common.is_coi_disclosure` (Task 1); existing `quote_grounded`.
- Produces:
  - `split_grounded(claims: List[dict], source: dict) -> Tuple[List[dict], List[dict]]` — `(kept, dropped)`; each dropped claim gets `drop_reason="grounding"`.
  - `filter_grounded_claims(claims, source) -> List[dict]` — unchanged behaviour, now `split_grounded(...)[0]`.
  - `ingest_source(...) -> Tuple[List[dict], List[dict]]` — `(kept_claims, dropped_claims)` where dropped carry `drop_reason` in `{"coi", "grounding"}`.
  - `write_wiki_tree(run_dir, block, claims, dropped=None)` — writes `schema/drops.json` when `dropped` is given; `log.md` gains a funnel line.

- [ ] **Step 1: Write the failing test**

Append to `a_comp_hcp_communication/tests/test_stage03_grounding.py`:

```python
def test_split_grounded_tags_dropped_reason():
    claims = [
        {"speaker_name": "Vesna Budić-Spasić",
         "verbatim_quote": "Saxenda wirkt gut bei Adipositas",
         "wirkstoff": "Saxenda", "sentiment": "positive", "confidence": "high",
         "statement": "efficacy", "citation": {"website_id": "w1", "url": "http://a"}},
        {"speaker_name": "Michael Holznagel", "verbatim_quote": "Ich empfehle Saxenda",
         "wirkstoff": "Saxenda", "sentiment": "positive", "confidence": "high",
         "statement": "endorses", "citation": {"website_id": "w1", "url": "http://a"}},
    ]
    kept, dropped = mod.split_grounded(claims, SOURCE)
    assert len(kept) == 1 and kept[0]["speaker_name"] == "Vesna Budić-Spasić"
    assert len(dropped) == 1 and dropped[0]["drop_reason"] == "grounding"


def test_filter_grounded_claims_still_returns_kept_only():
    claims = [
        {"speaker_name": "Vesna Budić-Spasić",
         "verbatim_quote": "Saxenda wirkt gut bei Adipositas",
         "wirkstoff": "Saxenda", "sentiment": "positive", "confidence": "high",
         "statement": "efficacy", "citation": {"website_id": "w1", "url": "http://a"}},
    ]
    kept = mod.filter_grounded_claims(claims, SOURCE)
    assert len(kept) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd a_comp_hcp_communication && python -m pytest tests/test_stage03_grounding.py -k "split_grounded or filter_grounded_claims_still" -v`
Expected: FAIL for `split_grounded` (attribute error); the `filter_grounded_claims_still` test may already pass — that is fine, it guards the refactor.

- [ ] **Step 3: Add `is_coi_disclosure` import**

In `a_comp_hcp_communication/03_wiki_build.py`, extend the existing `pipeline_common` import (currently `from pipeline_common import (call_bedrock_json, make_bedrock_client, name_matches, normalize_name)`) to also import `is_coi_disclosure`:

```python
from pipeline_common import (call_bedrock_json, is_coi_disclosure,  # noqa: E402
                             make_bedrock_client, name_matches, normalize_name)
```

- [ ] **Step 4: Add `split_grounded` and refactor `filter_grounded_claims`**

In `a_comp_hcp_communication/03_wiki_build.py`, replace the existing `filter_grounded_claims` with:

```python
def split_grounded(claims: List[dict], source: dict) -> Tuple[List[dict], List[dict]]:
    """Split claims into (grounded, dropped). Dropped claims get drop_reason='grounding'."""
    text = source.get("full_text", "")
    kept, dropped = [], []
    for c in claims:
        if quote_grounded(c.get("verbatim_quote", ""), text):
            kept.append(c)
        else:
            d = dict(c)
            d["drop_reason"] = "grounding"
            dropped.append(d)
    return kept, dropped


def filter_grounded_claims(claims: List[dict], source: dict) -> List[dict]:
    """Keep only claims whose verbatim_quote is literally present in the source."""
    return split_grounded(claims, source)[0]
```

- [ ] **Step 5: Update `ingest_source` (COI safety net + return dropped)**

In `a_comp_hcp_communication/03_wiki_build.py`, replace `ingest_source` with:

```python
def ingest_source(bedrock, config, competitor: str, generic: str,
                  source: dict) -> Tuple[List[dict], List[dict]]:
    """Ingest one source → (grounded+speaker-resolved kept claims, dropped claims).

    Dropped claims carry drop_reason in {"coi", "grounding"}. Verify-stage drops are
    recorded by the caller.
    """
    cfg = config["comp_hcp"]
    wcfg = config["wiki"]
    prompt = build_ingest_prompt(competitor, generic, source)
    try:
        raw = call_bedrock_json(bedrock, wcfg["ingest_model_id"], prompt,
                                cfg.getfloat("temperature"),
                                cfg.getint("extraction_max_tokens"))
    except Exception as err:  # noqa: BLE001
        log.error("Ingest failed for %s / %s: %s", competitor,
                  source.get("website_id"), err)
        return [], []
    claims, dropped = [], []
    for rc in raw.get("claims") or []:
        c = normalize_claim(rc, competitor, source)
        if c is None:
            continue
        mapped, cid = resolve_speaker(c["speaker_name"], source.get("mapped_hcps", []))
        c["mapped"] = mapped
        c["s_customer_id"] = cid
        # COI safety net (belt-and-suspenders with the ingest-prompt exclusion)
        if is_coi_disclosure(c["verbatim_quote"], c.get("statement", "")):
            d = dict(c)
            d["drop_reason"] = "coi"
            dropped.append(d)
            continue
        claims.append(c)
    # deterministic grounding gate BEFORE spending a verify call
    kept, grounding_dropped = split_grounded(claims, source)
    dropped.extend(grounding_dropped)
    return kept, dropped
```

- [ ] **Step 6: Update `write_wiki_tree` to persist drops**

In `a_comp_hcp_communication/03_wiki_build.py`, change the `write_wiki_tree` signature and its `log.md` + `schema/` writes:

Change the signature line to:

```python
def write_wiki_tree(run_dir: str, block: dict, claims: List[dict],
                    dropped: Optional[List[dict]] = None) -> None:
```

Replace the `log.md` write with a funnel line, and add a `drops.json` write. Locate the block that currently writes `wiki/log.md` and the `schema/knowledge_graph.json`, and replace with:

```python
    dropped = dropped or []
    drop_counts: Dict[str, int] = {}
    for d in dropped:
        drop_counts[d.get("drop_reason", "?")] = drop_counts.get(d.get("drop_reason", "?"), 0) + 1
    sent_kept: Dict[str, int] = {}
    for c in claims:
        sent_kept[c.get("sentiment", "?")] = sent_kept.get(c.get("sentiment", "?"), 0) + 1
    _write_text(os.path.join(comp_dir, "wiki", "log.md"),
                f"## run\n- ingested {len(block.get('sources', []))} source(s); "
                f"{len(claims)} grounded+verified claim(s) "
                f"(dropped: {drop_counts or '{}'}); sentiment(kept)={sent_kept or '{}'}.\n")
    # schema/
    _write_text(os.path.join(comp_dir, "schema", "knowledge_graph.json"),
                json.dumps(build_competitor_graph(block, claims), indent=2,
                           ensure_ascii=False))
    _write_text(os.path.join(comp_dir, "schema", "drops.json"),
                json.dumps(dropped, indent=2, ensure_ascii=False))
```

> The existing test `test_write_wiki_tree_creates_files` calls `write_wiki_tree(run_dir, BLOCK, claims)` without `dropped`; the default `None` keeps it passing (it still creates `log.md` and `knowledge_graph.json`; `drops.json` is written with `[]`).

- [ ] **Step 7: Update `main` to thread dropped claims through**

In `a_comp_hcp_communication/03_wiki_build.py` `main`, update the ingest/verify loops. Replace the INGEST and VERIFY blocks inside the `for b in blocks:` loop with:

```python
        comp, generic = b.get("competitor", ""), b.get("generic", "")
        sources = b.get("sources", [])
        # INGEST (parallel over sources)
        ingested: List[Tuple[dict, dict]] = []  # (claim, source)
        dropped: List[dict] = []
        if sources:
            with ThreadPoolExecutor(max_workers=max(1, min(iw, len(sources)))) as pool:
                futs = {pool.submit(ingest_source, bedrock, config, comp, generic, s): s
                        for s in sources}
                for f in as_completed(futs):
                    s = futs[f]
                    kept, drop = f.result()
                    for c in kept:
                        ingested.append((c, s))
                    dropped.extend(drop)
        # VERIFY (parallel over claims)
        verified: List[dict] = []
        if ingested:
            with ThreadPoolExecutor(max_workers=max(1, min(vw, len(ingested)))) as pool:
                futs = {pool.submit(verify_claim, bedrock, config, c, s): (c, s)
                        for c, s in ingested}
                for f in as_completed(futs):
                    c, _ = futs[f]
                    if f.result():
                        c["verified"] = True
                        verified.append(c)
                    else:
                        d = dict(c)
                        d["drop_reason"] = "verify"
                        dropped.append(d)
        log.info("Competitor '%s': %d ingested → %d verified claim(s); %d dropped.",
                 comp, len(ingested), len(verified), len(dropped))
        write_wiki_tree(run_dir, b, verified, dropped)
        graph["competitors"].append(build_competitor_graph(b, verified))
```

- [ ] **Step 8: Run the Stage 03 suites**

Run: `cd a_comp_hcp_communication && python -m pytest tests/test_stage03_grounding.py tests/test_stage03_wiki.py -q`
Expected: PASS — new `split_grounded` test, refactored `filter_grounded_claims` test, and the existing grounding/wiki tests all green.

- [ ] **Step 9: Commit**

```bash
git add a_comp_hcp_communication/03_wiki_build.py a_comp_hcp_communication/tests/test_stage03_grounding.py
git commit -m "feat(stage03): COI safety net + ingest/verify drop instrumentation

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Docs update + full verification

**Files:**
- Modify: `a_comp_hcp_communication/CLAUDE.md` (note the COI filter + drop telemetry)
- Verify: full test suite + manual regeneration on existing data

- [ ] **Step 1: Update `CLAUDE.md`**

In `a_comp_hcp_communication/CLAUDE.md`, in the Files table, update the Stage 03 and Stage 05 rows to mention the new behaviour:

- Stage 03 row → append: ` COI disclosures excluded; drops (coi/grounding/verify) persisted to wiki/<ts>/<competitor>/schema/drops.json.`
- Stage 05 row → append: ` Tabbed HTML (overview + per-competitor + doctors-weighing + most-active + methodology); COI disclosures filtered from HTML + Excel.`

- [ ] **Step 2: Run the entire suite**

Run: `cd a_comp_hcp_communication && python -m pytest -q`
Expected: PASS — all pre-existing tests plus the new ones across `test_pipeline_common.py`, `test_stage05.py`, `test_stage03_wiki.py`, `test_stage03_grounding.py`.

- [ ] **Step 3: Manual verification — regenerate on existing data (no pipeline re-run)**

Run:
```bash
cd a_comp_hcp_communication && python 05_generate_report.py --force
```
Expected: writes new `results/report_<ts>.html` + `report_<ts>.xlsx`. Then confirm:

```bash
cd a_comp_hcp_communication && python - <<'PY'
import glob, re
html = sorted(glob.glob("results/report_*.html"))[-1]
t = open(html, encoding="utf-8").read()
# COI disclosures gone
assert "Forschungsgelder" not in t, "COI (Forschungsgelder) still present!"
assert "Case payments" not in t and "Speaker Honoraria" not in t, "COI still present!"
# tabs present
assert 'class="tabs"' in t and "function showTab" in t and "js-tabs" in t
# expected tabs
for lbl in ("Insgesamt", "Doctors weighing", "Most active voices", "Methodology"):
    assert lbl in t, f"missing tab {lbl}"
print("HTML checks passed:", html)

from openpyxl import load_workbook
xlsx = sorted(glob.glob("results/report_*.xlsx"))[-1]
wb = load_workbook(xlsx)
ws = wb["Grounded Claims"]
joined = "\n".join(str(c.value) for row in ws.iter_rows() for c in row if c.value)
assert "Forschungsgelder" not in joined and "Speaker Honoraria" not in joined, "COI in Excel!"
print("Excel checks passed:", xlsx)
PY
```
Expected: prints "HTML checks passed" and "Excel checks passed". Manually open the HTML in a browser: tabs switch on click; with JS disabled all sections are visible (full scroll).

- [ ] **Step 4: (Optional, user-run) Full pipeline re-run to validate rubric + drops**

Run (needs Bedrock/Snowflake, done by the user):
```bash
cd a_comp_hcp_communication
python 03_wiki_build.py --force
python 04_synthesize.py --force
python 05_generate_report.py --force
```
Expected: `wiki/<ts>/<competitor>/schema/drops.json` files exist; `wiki/<ts>/<competitor>/wiki/log.md` shows the funnel line with drop counts and kept-sentiment mix; the report's sentiment distribution reflects the rubric (drawback statements now negative/ambivalent, not neutral).

- [ ] **Step 5: Commit**

```bash
git add a_comp_hcp_communication/CLAUDE.md
git commit -m "docs(comp_hcp): note COI filter, tabbed report, drop telemetry

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage:**
- Component 1 (tabbed report + overview) → Tasks 3, 4. ✓
- Component 2a (deterministic COI filter, render-time + ingest safety net) → Tasks 1, 2, 6. ✓
- Component 2b (ingest-prompt COI exclusion) → Task 5. ✓
- Component 3a (sentiment rubric) → Task 5. ✓
- Component 3b (drop-instrumentation, drops.json + log) → Task 6. ✓
- Testing section → tests in every task; full-suite + manual regen in Task 7. ✓
- Docs (CLAUDE.md) → Task 7. ✓
- Sequencing (no-re-run vs re-run) → honoured: Tasks 2 & 4 fix existing data via `--force` render (Task 7 Step 3); Tasks 5 & 6 take effect on re-run (Task 7 Step 4). ✓

**Placeholder scan:** No TBD/TODO; every code step shows complete code; every command shows expected output. ✓

**Type consistency:** `is_coi_disclosure(quote, statement="")` defined in Task 1, used identically in Tasks 2 & 6. `_visible_claims(claims)` defined and reused for HTML + Excel in Task 2. `overall_distribution`/`tab_id` defined in Task 3, consumed in Task 4. `split_grounded` returns `(kept, dropped)` in Task 6 and `filter_grounded_claims` reuses it (preserving the signature the existing test expects). `write_wiki_tree(..., dropped=None)` optional param preserves the existing call. `ingest_source` new `(kept, dropped)` return is consumed by the updated `main` in the same task. ✓
