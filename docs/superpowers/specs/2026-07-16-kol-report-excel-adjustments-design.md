# KOL Report & Excel Adjustments — Design

**Date:** 2026-07-16
**Service:** `b_kol_identification` (2.1 KOL Identification & Mapping)
**Scope:** Six adjustments to the Stage-05 report/Excel, plus one enabling Stage-04 change.

## Motivation

The current HTML report and Excel export (`kol_report_20260716_101030.*`) are close, but
(a) the Rising Stars tab lacks the score-development view and score explanation the KOL
tabs have, (b) the KOL Profile cards are visually cramped by two elements that don't fit,
and (c) the Excel is a "black box" — it does not let the analyst trace *why* a source
counted or *how* a composite score evolved year by year. The goal is to make everything
shown in the report **nachvollziehbar** (traceable/auditable) from the Excel, with the
Excel carrying *more* detail than the top-25 report.

## Changes

### HTML report (`05_generate_report.py`)

**C1 — Rising Stars: score-development chart section.**
Rising stars already carry a `score_trajectory` (identical structure to KOLs). After the
existing "Publication trajectory — total vs indication-relevant" section in
`render_rising_stars`, add a second, separate section **"Score development"** — one card
per rising star rendering `render_score_dev_chart(trajectory, t_a, t_b, rising_max=...)`
(the same A/B/C tier bands and tenure-crossing marker used on KOL profile cards). It is a
distinct section, not placed under each star's bar chart.
`render_rising_stars` gains `t_a`, `t_b`, `rising_max` parameters, threaded from
`build_report_html` (which already computes `t_a, t_b` via `_kol_tier_thresholds`).

**C2 — Rising Stars: composite-score drill-down.**
Add the `render_score_breakdown(h, weights)` `<details>` dropdown to each rising-star
**table row** (in the Composite score cell), exactly as the KOL Ranking table has it.
`render_rising_stars` gains a `weights` parameter.

**C3 — KOL Profiles: remove the "how it was scored" dropdown.**
Remove the `render_score_breakdown(h, weights)` call from each profile card in
`render_profiles`. The card keeps its existing `meta` line
("Composite score X · N verified sources (…) · N total publications"). `render_profiles`
keeps its `weights` param (still used elsewhere / signature stability) but no longer emits
the breakdown; unused param is acceptable, or drop it — see Plan.

**C4 — KOL Profiles: remove the "Ny on-topic" tenure sticker.**
Remove the `stage` pill (`tenure_chip`) from the profile card header in `render_profiles`.
The tenure remains visible in the KOL Ranking table (unchanged) and interpretable from the
charts.

**C8 — KOL Profiles: align the two charts' x-axis and add axes to the bar chart.**
The publication bar chart (`render_year_bars`) and the score-development line chart
(`render_score_dev_chart`) in each profile card cover the **same years** (verified:
`all_years` == the trajectory years, both spanning `pub_history_years` ending at
`anchor_year`) but render at different pixel widths (190 vs 320), so they don't line up.
Two sub-changes:
- **C8a (equal length):** render both charts at the same width, using a shared module
  constant `PROFILE_CHART_W` (= 320). Both charts also use the same left/right inset so
  the plotted data area starts and ends at the same x — the bar columns then sit directly
  above the corresponding points on the line below, making it easy to read publication
  movement against score movement.
- **C8b (axis lines):** draw a baseline **x-axis** and a left **y-axis** line (muted
  `PALETTE["line"]`/`muted` colour) in `render_year_bars` so the bars are anchored to a
  visible frame instead of "starting from nowhere". A small left inset accommodates the
  y-axis line; year tick labels stay on the x-axis.

Note: `render_year_bars` is also used by the Rising Stars publication-trajectory cards
(C1's section sits beneath). Widening it there is harmless (SVGs are `max-width:100%` in
the card grid) and keeps the rising-star bar/line pair aligned too.

### Excel export (`write_excel` in `05_generate_report.py`)

Two new sheets are added after the existing "KOLs" sheet. Both get a frozen header row and
basic column auto-width, matching the existing sheet's conventions.

**C5 — Sheet "LLM Wiki Verdicts".**
Source-level audit trail. Reads `sources.json` (every source handed to the LLM, with URL)
and `wiki.json` (surviving verified claims). **One row per source**, covering **all HCPs in
`kol_final.json`** (94). Columns:

| Rank | Name | Kind | URL | PMID | Verdict | Verified claims | Statements | Themes | Sentiments |
|------|------|------|-----|------|---------|-----------------|-----------|--------|------------|

- `Kind` = `web` / `pubmed`.
- `Verdict` = `counted` if the source produced ≥1 verified claim, else `rejected`
  (handed to the LLM but yielded no verified claim).
- `Verified claims` = integer count.
- `Statements` = the verified claim `statement`s for that source, joined (`" | "`),
  truncated per-cell to a safe length. `Themes` / `Sentiments` = the distinct
  themes / sentiments across that source's verified claims.
- Rank follows `kol_final.json` ordering (already sorted by score desc).
- Sources are joined to claims by `source_id` (falling back to URL / PMID).

**C6 — Sheet "Score by Year".**
Composite-score reconstruction per year. From each HCP's `score_trajectory`.
**One row per (HCP, year)**. Columns:

| Rank | Name | Year | Composite score | Relevance | Reach | Ratio | Tenure | Tier |
|------|------|------|-----------------|-----------|-------|-------|--------|------|

The trajectory fields map directly: `year, score, relevance, reach, ratio, tenure, tier`.
Coverage = every HCP that has a non-empty `score_trajectory` — see C7.

### Enabling change (`04_assemble_kols.py`)

**C7 — Build trajectories for all KOLs + rising stars.**
Today Stage 04 builds `score_trajectory` only for `[x for x in hcps if x.get("is_kol")][:rep_n]`
(the reported top-N KOLs) — 22 of the current file. Rising stars get none under current
code (the on-disk file's 3 rising-star trajectories are a stale artifact of an older
version). To (a) make C1 work on freshly-generated data and (b) give C6 the chosen
coverage ("all KOLs + rising stars"), change the trajectory loop to iterate
**`[x for x in hcps if x.get("is_kol") or x.get("rising_star")]`** (no `rep_n` slice) —
53 KOLs + 7 rising stars = 60 for the current pool. The report display still slices
top-N; only the data coverage widens.

## Data flow / wiring

- `write_excel(data, path)` → `write_excel(data, path, sources_path=None, wiki_path=None)`.
  `main()` passes `data/sources.json` and `data/wiki.json`. If either file is missing or
  unreadable, the corresponding sheet is emitted with a header + a single note row rather
  than crashing (the Excel must always be produced). `full_text` from `sources.json` is
  **not** read into the sheet (only `source_id`, `kind`, `url`).
- `build_report_html` threads `weights`, `t_a`, `t_b`, `rising_max` into
  `render_rising_stars` (all already in scope there).
- Stage 04's change is a single loop-predicate edit; the trajectory math is unchanged.

## Testing

Follow the existing per-stage `importlib`-load + inline-fixture pattern
(`tests/test_04_assemble.py`, `tests/test_05_report.py`).

- **C1:** `render_rising_stars` output for a star with a `score_trajectory` contains a
  "Score development" heading and an SVG `polyline`; absent when no trajectory.
- **C2:** rising-star output contains a `score-breakdown` `<details>` with the three
  factor rows.
- **C3:** `render_profiles` output does **not** contain `score-breakdown` / "how it was
  scored".
- **C4:** `render_profiles` output does **not** contain the `pill stage` tenure sticker.
- **C8:** `render_year_bars` output contains axis lines (an x-axis baseline + y-axis line)
  and renders at the shared `PROFILE_CHART_W` width; the bar chart and dev chart in a
  profile card share the same width value.
- **C5:** `write_excel` with fixture `sources.json`/`wiki.json` produces a "LLM Wiki
  Verdicts" sheet: a counted source (has a verified claim) → `counted`; a source with no
  verified claim → `rejected`; header present; `full_text` never appears in a cell.
- **C6:** `write_excel` produces a "Score by Year" sheet with one row per trajectory year
  and the seven numeric columns; HCPs without a trajectory contribute no rows.
- **C7:** Stage-04 trajectory loop assigns `score_trajectory` to a rising star (not only
  KOLs). Unit-test the predicate via a small pool with one KOL + one rising star (mock
  `authors_by_pmid`), asserting both receive a non-empty trajectory.
- Regression: full existing suite (127 tests) stays green.

## Verification

- Run the full pytest suite (mock Snowflake/Bedrock).
- Run Stage 05 against the existing `data/kol_final.json` (no DB needed) and open the
  produced HTML + XLSX to confirm C1–C6 render. Note: until Stage 04 is re-run on the
  DB-enabled sandbox, the "Score by Year" sheet and rising-star score charts reflect the
  25 trajectories in the current file; after `python 04_assemble_kols.py --force` +
  `python 05_generate_report.py` on the sandbox, coverage becomes the full 60.

## Non-goals / YAGNI

- No change to scoring math, tenure buckets, floors, or the funnel.
- Source `full_text` / abstract bodies are deliberately excluded from the Excel.
- No per-claim grain in the Wiki sheet (source-level chosen).
- No new report sections beyond C1's "Score development".

## Out-of-environment constraint

Stage 04 requires Snowflake (co-author fetch) and cannot be re-run in the current
dev environment (no `snowflake.connector` import / no AWS creds). The C7 code change is
delivered and unit-tested here; regenerating real `kol_final.json` for full-coverage
output is done by the user on the DB-enabled sandbox.
