# Design — Tabbed report, COI filtering, sentiment rubric & drop-instrumentation

**Service:** `a_comp_hcp_communication` (Service 1.2 — Competitive HCP Communication Monitoring)
**Date:** 2026-07-06
**Author:** Joshua (with Claude)

## Background

A reviewer gave three pieces of feedback on `results/report_<ts>.html`:

1. **Too much scrolling.** Wants an overview of the overall sentiment distribution at
   the top, then a tab per topic:
   *Insgesamt / Saxenda / Mounjaro / Rybelsus / Wegovy / Doctors weighing / Most
   active voices / Methodology.*
2. **Conflict-of-interest disclosures leak into the output.** Two examples flagged:
   - *"Ich erhalte Forschungsgelder von der Firma Novo Nordisk, welche Semaglutid
     vermarktet. Ich halte auch Aktien der Firma Novo Nordisk."*
   - *"Ich erhielt Case payments bei Studien von Novo Nordisk (STEP-HF Trial, STEP-HF
     DM Trial), war Mitglied im Advisory Board und erhielt Speaker Honoraria für Novo
     Nordisk Produkte (ziltivekimab)."*

   These are financial-disclosure statements, not opinions about a drug. They must not
   appear in the HTML report **or** the Excel export.
3. **Suspiciously few negative opinions.** 2 negative + 2 ambivalent out of 80 claims.

## Investigation of feedback #3 (already done, against existing data)

- **Distribution:** 47 positive · 29 neutral · 2 negative · 2 ambivalent (80 claims).
- **Primary cause is labeling, not suppression.** 12 claims that plainly describe
  drawbacks (GI side-effects + retinopathy; 35–40 % muscle-mass loss; "relatively
  expensive"; weight regain after discontinuation; lifelong therapy; "disadvantages")
  are currently filed as **neutral**. The Stage 03 ingest prompt **never defines** the
  four sentiment labels — it only lists `positive|neutral|negative|ambivalent` — so the
  model defaults any *factual* mention of a downside to neutral.
- **The corpus is not sentiment-barren:** across 149 raw sources, "Nebenwirkung"
  appears in 48, "Kosten" in 43, "Krebs" in 21, "gefährlich" in 11, "kritisch" in 7.
- **Blind spot:** Stage 03 discards claims at the deterministic grounding gate and the
  LLM verify gate **without persisting what it dropped or why**, so the ingest→verify
  drop rate (and whether negatives are disproportionately killed) cannot be measured
  from saved data.

## Goals

1. Replace the single scrolling report (Report A) with a tabbed layout + an overall
   sentiment overview, keeping it fully offline (inline CSS + vanilla JS, no external
   assets).
2. Stop conflict-of-interest / financial-disclosure statements from appearing in the
   report and Excel — both cleaning the **existing** data immediately and preventing
   re-extraction on future runs.
3. Make the sentiment mix credible by giving the ingest LLM an explicit rubric, and
   make the "are negatives dropped?" question answerable by instrumenting Stage 03.

## Non-goals

- No re-labeling of the existing 80 claims by hand (that would fabricate grounding).
  The sentiment rubric changes labels only on a genuine re-run.
- No change to Stages 01/02 (retrieval), the vector/reranker modules, or `shared/`.
- The Plain-Language Guide (Report B) and Technical Doc (Report C) keep their current
  single-page layout — only the main Competitor Intelligence report (Report A) is
  tabbed.

## Sequencing / what takes effect when

| Change | File(s) | Effect without re-run? |
|--------|---------|------------------------|
| Tabbed report + overview | `05_generate_report.py` | **Yes** — re-render existing `synthesis.json` |
| Deterministic COI filter | `pipeline_common.py` + `05_generate_report.py` | **Yes** — cleans current report + Excel |
| COI exclusion in ingest prompt | `03_wiki_build.py` | No — next run |
| Sentiment rubric in ingest prompt | `03_wiki_build.py` | No — next run |
| Dropped-claim logging | `03_wiki_build.py` | No — next run |

The deterministic COI filter is the single source of truth applied at **render time**
in Stage 05 (and Excel), so the current outputs are clean immediately. It is *also*
applied at Stage 03 ingest as a safety net, so future `knowledge_graph.json` files are
clean at the source. The ingest-prompt exclusion is the primary future-run fix; the
deterministic filter is the belt-and-suspenders backstop for both old and new data.

---

## Component 1 — Tabbed Competitor Intelligence report (Stage 05)

### Behaviour

- A header (title, subtitle, generated-at, legend) stays **above** the tab bar and is
  always visible.
- A tab bar with buttons, generated dynamically:
  `Insgesamt` · one tab per competitor summary (label = `competitor_heading`, e.g.
  "Saxenda (Liraglutid)") · `Doctors weighing` · `Most active voices` · `Methodology`.
- Exactly one panel visible at a time; `Insgesamt` is active on load.
- **Insgesamt** panel contains, in order:
  1. The 4 KPI tiles (competitors, doctors, grounded statements, mapped/not-mapped).
  2. **NEW — Overall sentiment distribution overview:** an aggregate sentiment bar
     chart across *all* competitors (reuses `svg_distribution_chart`), plus a compact
     per-competitor sentiment table (competitor × positive/neutral/negative/ambivalent).
  3. The Executive Summary paragraph.
  4. The Cross-Competitor KPI row (distinct doctors / discuss 2+ / mapped / not-mapped)
     and the "Reach — distinct doctors per competitor" table.
- **Per-competitor** panels: the existing per-competitor card (SVG chart + market view
  + grounded statements + "N more" note), one competitor per tab.
- **Doctors weighing** panel: the "Doctors weighing in on multiple competitors" table.
- **Most active voices** panel: the "Most active voices" table.
- **Methodology** panel: the methodology paragraph + footer.

### Implementation

- New pure helper `overall_distribution(summaries) -> Dict[str,int]` — sums each
  sentiment across every competitor's `distribution_split["all"]`. Unit-tested.
- New pure helper `tab_id(label) -> str` — slugify a tab label to a DOM id
  (lowercase, non-alnum → `-`). Unit-tested.
- Refactor `build_report_a` to assemble a list of `(tab_label, panel_html)` sections,
  then render: nav bar + panels. Section-builder functions return HTML strings so they
  stay individually testable (e.g. `_panel_overview`, `_panel_competitor`,
  `_panel_multi`, `_panel_top_voices`, `_panel_methodology`). Keeps functions focused
  and under the "large file doing too much" threshold.
- **Tabs mechanism:** semantic buttons + panels with a ~15-line inline `<script>`
  toggling an `.active` class and `hidden` on panels; `role="tab"`/`role="tabpanel"`
  and `aria-selected` for accessibility. CSS in `BASE_CSS`.
- **No-JS fallback:** panels are plain sections; when JS is disabled the script simply
  never hides them, so the report degrades to the current full-scroll page (nothing is
  lost). The nav uses in-page `#anchor` hrefs so it still jumps correctly.
- Report B and Report C are untouched.

### Data flow

`synthesis.json` → `build_report_a` (unchanged inputs) → section builders → tabbed HTML.
No new inputs; purely a rendering change.

---

## Component 2 — COI / disclosure filtering

### Scope (confirmed: financial COI disclosures only)

Drop a claim when its content is a **financial conflict-of-interest / disclosure**:
research funding/grants, stock/share ownership, advisory-board membership, consulting
fees, speaker honoraria, case/study payments, or any declaration of financial ties to a
manufacturer. Do **not** expand to biographical/role filler in this change.

### 2a. Deterministic filter (shared, conservative)

New helper in `pipeline_common.py`:

```python
def is_coi_disclosure(quote: str, statement: str = "") -> bool:
    """True when the text is primarily a financial conflict-of-interest disclosure."""
```

- Matches a curated bilingual (DE/EN) pattern set against `quote` + `statement`:
  `Forschungsgeld|Forschungsförderung|Drittmittel`, `Aktien|shares|stock`,
  `Advisory Board|Beirat`, `Honorar(e|ia)?|honorar`, `Speaker (Honoraria|fee)`,
  `Case payment|Vortragshonorar`, `Berater(tätigkeit)?|consult(ing|ant)`,
  `Interessenkonflikt|conflict of interest|declaration of interest`,
  `finanzielle (Zuwendung|Unterstützung)`, `erhielt … von <Firma>`, `research funding`,
  `grants? from`, `member of the .*advisory board`, etc.
- **Conservative design to limit false positives:** fire only when the text is
  *dominated* by disclosure language — e.g. a disclosure pattern hits **and** the text
  lacks any clinical-signal token (efficacy/dosing/side-effect/mechanism/comparison
  vocabulary). A statement that both discloses funding *and* makes a clinical claim is
  kept (the clinical content is the signal). Exact predicate finalised during
  implementation with the flagged examples as fixtures.
- Every drop is `log.info`-ed with speaker + competitor + reason `coi_disclosure` so
  removals are auditable, never silent.

Applied in **two** places:
- **Stage 05** (`build_report_a` claim iteration **and** `write_excel`): filter the
  claim list up-front so the current report + Excel are clean without a re-run. A single
  `_visible_claims(claims)` gate feeds both HTML and Excel so they never diverge.
- **Stage 03** `ingest_source`, right after `normalize_claim`, as a safety net so new
  `knowledge_graph.json` files are clean at the source.

> Note: filtering at render time means the Stage 05 KPI counts (e.g. "Grounded
> statements", the sentiment overview, cross-competitor stats) are computed on the
> filtered list, so the numbers stay internally consistent with what's shown.

### 2b. Ingest-prompt exclusion (primary future-run fix)

Add an explicit EXCLUSION block to `build_ingest_prompt` in `03_wiki_build.py`:

> **Do NOT extract conflict-of-interest or financial-disclosure statements**, even when
> a named doctor makes them. Exclude declarations of research funding/grants, stock or
> share ownership, advisory-board membership, consulting fees, speaker honoraria, and
> case/study payments or other financial ties to a manufacturer (e.g. "Ich erhalte
> Forschungsgelder von …", "Ich halte Aktien …", "war Mitglied im Advisory Board",
> "Speaker Honoraria"). Extract only statements expressing a view or clinical claim
> **about the drug itself** — efficacy, safety/tolerability, dosing, mechanism,
> positioning, patient experience, or comparison with other drugs.

---

## Component 3 — Sentiment rubric + drop-instrumentation (Stage 03)

### 3a. Sentiment rubric in the ingest prompt

`build_ingest_prompt` currently lists the four labels with no definitions. Add a rubric
so a doctor **factually** citing a serious downside is not defaulted to neutral:

> Assign `sentiment` by the doctor's stance **toward the drug**:
> - **positive** — favourable: efficacy, benefit, endorsement, good tolerability.
> - **negative** — unfavourable **or reports a material drawback**: significant
>   side-effect burden, safety risk, cost concern, efficacy limitation, weight regain,
>   need for lifelong therapy, muscle-mass loss, or an explicitly critical view.
> - **ambivalent** — names a benefit **and** a drawback together.
> - **neutral** — purely descriptive/factual with no benefit or drawback implied
>   (e.g. approval status, dosing schedule, mechanism, brand↔generic identity).
>
> Judge only from the quote; never invent a stance the text does not support. Extract
> critical statements with the **same fidelity** as positive ones.

This is a neutral-framing nudge: it defines negative/ambivalent precisely and explicitly
forbids inventing negativity — it does not instruct the model to find more negatives.

### 3b. Dropped-claim instrumentation

Make the ingest→verify funnel observable:

- `ingest_source` returns not just surviving claims but also the ones dropped by the
  **grounding** gate (quote not in source), each tagged `drop_reason="grounding"`.
- The **verify** loop in `main` records claims dropped with `drop_reason="verify"`.
- Per competitor, write a `drops.json` (or a `## drops` section in the existing
  `wiki/<ts>/<competitor>/wiki/log.md`) capturing, for each dropped claim: speaker,
  sentiment, verbatim_quote, drop_reason. This lets a re-run answer "are negatives
  disproportionately dropped?" by comparing the sentiment mix of dropped vs kept claims.
- The existing `log.md` one-liner is extended to:
  `ingested N source(s); K claim(s) extracted → G grounded → V verified
  (dropped: {grounding: x, verify: y}); sentiment(kept)=…`.

This is additive telemetry; it does not change which claims are kept beyond the COI
filter already specified.

---

## Testing

All external boundaries (Snowflake/Bedrock/ONNX) stay mocked; tests are pure-function
unit tests via the existing `conftest.load_stage` pattern.

- **`test_pipeline_common.py`** — `is_coi_disclosure`: the two flagged quotes → True; a
  clinical statement mentioning an advisory board but making a drug claim → False; plain
  clinical statements → False; empty → False.
- **`test_stage05.py`** — `overall_distribution` sums correctly; `tab_id` slugifies;
  `_visible_claims` removes COI claims; rendered HTML contains one nav button per
  expected tab and a panel per tab; the Insgesamt panel contains the overall chart.
- **`test_stage03_*`** — updated ingest prompt still parses expected JSON shape;
  `ingest_source` classifies a COI raw claim as dropped; grounding-drop path tags
  `drop_reason="grounding"`.
- **Manual verification:** re-run `python 05_generate_report.py --force` on the existing
  `synthesis.json`; confirm (a) the two flagged disclosures are gone from HTML + Excel,
  (b) tabs switch correctly, (c) the report still renders with JS disabled. Then a full
  pipeline re-run to confirm the rubric shifts the distribution and `drops.json` is
  written.

## Files touched

| File | Change |
|------|--------|
| `05_generate_report.py` | Tabbed layout, overview panel, `overall_distribution`, `tab_id`, `_visible_claims`, COI filter in HTML + Excel, tab CSS/JS |
| `pipeline_common.py` | `is_coi_disclosure` helper |
| `03_wiki_build.py` | Ingest-prompt COI exclusion + sentiment rubric; dropped-claim capture + `drops.json`/log |
| `tests/test_stage05.py`, `tests/test_pipeline_common.py`, `tests/test_stage03_*.py` | Unit tests for the above |
| `a_comp_hcp_communication/CLAUDE.md` | Note the COI filter + drop telemetry in the pipeline description |

## Open questions

None blocking. The exact `is_coi_disclosure` predicate and the drops-file format
(`drops.json` vs a `log.md` section) are finalised during implementation using the two
flagged quotes as fixtures.
