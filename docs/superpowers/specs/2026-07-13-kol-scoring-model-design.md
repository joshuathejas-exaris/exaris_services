# KOL Scoring Model — Multi-Factor Composite, Hybrid Relevance, Backtest

**Service:** `b_kol_identification/` (Service 2.1 — KOL Identification & Mapping)
**Date:** 2026-07-13
**Status:** Design approved — pending user review before planning
**Supersedes scoring in:** `2026-07-09-kol-identification-v2-design.md` (funnel + LLM ground/verify pass are retained unchanged; only the final scoring model is extended)

---

## 1. Summary

Today `kol_score = verified_web_count + verified_pubmed_count` — a single honest count
of LLM-verified relevant sources. This design turns that single number into a
**transparent, config-weighted composite of three factors**, all computed *on top of*
the existing LLM ground-then-verify pass:

- **f1 — Relevance** (backbone): verified relevant source count, now fed by a **wider
  hybrid candidate net** (keyword gate ∪ vector recall).
- **f2 — Co-author reach**: breadth of collaboration on verified-relevant PubMed work.
- **f3 — Relevance ratio**: verified-relevant sources ÷ all the HCP's sources (focus).

The composite is **not a learned model** — there is no ground-truth label for "is a
KOL." It is a weighted index whose weights, thresholds, and normalization all live in
`config.ini`. Empirical validation comes from a **PubMed time-machine backtest**
(`as_of_year`): run 2021 vs now and check whether yesterday's rising stars became
today's KOLs.

A 4th factor (relevant congresses/Tagungen) is **designed-for-later**: the feature
layer leaves a slot for it, but it is not built this iteration.

---

## 2. Goals & non-goals

### Goals
- Replace the single-count score with a 3-factor weighted composite, relevance-dominant.
- Widen web relevance recall with a vector arm while keeping the LLM verify pass as the
  sole precision arbiter (no drop in precision, no co-occurrence regression).
- Make every threshold/weight client-tunable in `config.ini` with zero code edits.
- Add an `as_of_year` knob so the pipeline is reproducible for any past year (PubMed
  axis), enabling the rising-star→KOL backtest.
- Make the report explain itself: co-authorship network graph, click-through score
  transparency, and per-section "how to read this" explainers.

### Non-goals (this iteration)
- No supervised/learned model (no labels exist).
- No congress/Tagungen factor (slot reserved, not built).
- No use of `WEBSITES_EMBEDDINGS_512` (public Ambulante-Arzt sites — they never pass
  through `LLM_VALIDATION`, so a website-ID-restricted vector join would be empty).
- No new deliverables beyond HTML/Excel + the optional compare script.

---

## 3. The honesty guardrail (do not regress)

v1's `composite = 0.45·norm_pub + 0.30·norm_cf + 0.25·norm_digi` was *professionally
wrong* because it rewarded **keyword co-occurrence and DigiScore** — proxies that do
not mean topical engagement (human geneticists landed in the Obesity top-50).

This composite is fundamentally different: **all three factors are derived downstream
of the LLM ground+verify pass.** None can crown an HCP who is not genuinely engaging
with the indication. We add *dimensions of influence* (reach, focus) on top of already
verified relevance; we do not re-introduce co-occurrence. This distinction MUST be
preserved in code and documentation.

---

## 4. Architecture

The 5-stage funnel is retained and extended (Stage 03 is untouched):

```
01_fetch_and_shortlist   → + vector recall arm (hybrid); + as_of_year capping
02_retrieve_sources      → fetch full text for the widened candidate set (shape unchanged)
03_wiki_build            → ingest → ground → verify        (UNCHANGED — precision arbiter)
04_assemble_kols         → NEW feature layer: f1/f2/f3 → normalize → weighted composite → tiers
05_generate_report       → network graph + score-breakdown drill-down + per-section explainers
06_backtest_compare.py   → NEW (simple): diff two as_of_year runs (tier moves, rising→KOL)
```

---

## 5. Feature layer (Stage 04)

All factors computed from verified data, then **percentile-rank normalized** across the
scored pool (config-switchable) so weights are comparable.

### f1 — Relevance (backbone)
- Raw: `verified_web_count + verified_pubmed_count` (today's score), but the candidate
  net is widened by the hybrid vector arm (§6). Stage 03 verification unchanged.
- Default weight `weight_relevance = 0.60`.

### f2 — Co-author reach
- Raw: count of **distinct co-authors** across the HCP's **verified-relevant** PubMed
  articles (`verified_pmids`), deduped by `ORCID`, fallback to normalized name.
- **Distinct affiliations** computed alongside as a reported sub-metric (surfaces
  "active across multiple organizations"); not folded into the score this iteration.
- Source: `CORE.PUBMED.AUTHOR` (already queried in Stage 04 for the network).
- **PubMed-only.** Web co-mentions (`mentioned_hcps`) remain display-only in the
  network viz — too noisy for the score.
- Default weight `weight_reach = 0.25`.

### f3 — Relevance ratio (focus)
- Raw: `verified_relevant_sources / total_sources_for_hcp`.
- Denominator = **topic-agnostic totals**: all the HCP's PubMed articles
  (`PUBMED_ARTICLE_MAPPING`, `MERGE_RESULT > 1`) + all their web docs in
  `LLM_VALIDATION` (`IS_DOCTOR = 1`), regardless of topic. Two cheap COUNT queries.
- **Guard:** if denominator `< min_ratio_denominator` (default 5), the factor is set to
  neutral (does not contribute) to kill low-volume noise (e.g. 1/1 = 100%).
- Default weight `weight_ratio = 0.15`.

### Composite & tiers
- `kol_score = Σ wᵢ · normᵢ(fᵢ)` over the three factors.
- `normalization` ∈ {`percentile` (default), `minmax`, `zscore`}, applied per factor
  across all scored candidates.
- Tiers A/B/C = percentiles of the composite (existing `tier_a_percentile` /
  `tier_b_percentile`).
- Rising Stars: unchanged (verified-relevant PubMed by year; `new_voice` / `accelerating`).

---

## 6. Hybrid relevance (Stage 01)

The keyword gate is high-precision / low-recall (misses Adipositas↔Obesity↔near
topics). Add a recall arm; the LLM verify pass stays the precision arbiter, so nothing
counts unless verified.

**Per candidate HCP:**
- **Keyword arm (today):** `LLM_VALIDATION` where
  `NEAR_BY=1 AND IS_OLD=0 AND IS_DOCTOR=1 AND IN_RELATION>in_relation_min` AND PCA-term
  match → website IDs.
- **Vector arm (new):** embed the indication concept once (`VectorCreator` on the PCA
  term set / indication string). Take the HCP's websites from `LLM_VALIDATION` **without**
  the keyword/IN_RELATION filter (`NEAR_BY=1 AND IS_DOCTOR=1`), then
  `VECTOR_COSINE_SIMILARITY` against `WEBSITES_VERTICAL_EMBEDDINGS_512` restricted to
  those website IDs; keep chunks above `vector_sim_threshold` / top `vector_top_k_per_hcp`.
- **Union** keyword ∪ vector website IDs = web candidate set.
- **PubMed vector arm:** optional, behind `pubmed_vector_arm` (default false — CF flags
  already give good recall), using `PUBMED_EMBEDDINGS_512`.

**Cost control:** vector search is SQL-cheap; the 75-HCP shortlist and
`max_sources_per_hcp` still bound LLM spend. Optional cross-encoder `Reranker` pass
(`rerank`) to trim the union before Stage 02/03. All gated by `hybrid_relevance`.

**Assets:** copy Service A's `vector_creator.py` and `reranker.py` into
`b_kol_identification/` (keeping the "do not modify" convention); reuse the ONNX models
in `/assets/` (`gte_multilang_model_quantized.onnx`, `tokenizer.json`,
`mmarco-reranker/`).

---

## 7. Backtest / as-of-year time machine

Single knob `as_of_year` (default `latest`):
- When set to year Y: `anchor_year = Y`; PubMed candidate window = `Y − pubmed_window_years … Y`;
  pub-history capped at Y; co-author reach and ratio's PubMed denominator capped at ≤ Y.
- **Web/PubMed asymmetry:** web has no timestamps, so web relevance and the web side of
  the ratio are **frozen** across years. The backtest is "PubMed time-travel with web
  held constant." This caveat is documented in the report banner.
- Output filenames carry the year (e.g. `kol_final_<asof>.json`) so two runs coexist.

**`06_backtest_compare.py`** (simple, included): reads two `kol_final_<asof>.json`
files and reports tier movements and rising-star→KOL transitions between the two years.

---

## 8. Config surface

```ini
[funnel]
as_of_year            = latest      ; new — 'latest' or a year (e.g. 2021)

[hybrid]                            ; new section
hybrid_relevance      = true
vector_sim_threshold  = 0.55
vector_top_k_per_hcp  = 20
pubmed_vector_arm     = false
rerank                = false

[scoring]
weight_relevance      = 0.60        ; new
weight_reach          = 0.25        ; new
weight_ratio          = 0.15        ; new
normalization         = percentile  ; new — percentile | minmax | zscore
min_ratio_denominator = 5           ; new
; existing: tier_a_percentile, tier_b_percentile, rising_star_min_pubs, rising_star_growth
```

---

## 9. Report (Stage 05)

Self-contained HTML (no CDN/fonts/network), matching the existing inline-SVG style.

1. **Co-authorship network graph** — inline SVG force-directed graph of `coauthor_edges`.
   Nodes = KOLs + notable external co-authors; **node size = co-author reach**, edge
   width = shared relevant papers; **affiliation on hover/label** to show cross-org
   activity. Lives in the Collaboration Network section.
2. **Click-through score transparency** — clicking a KOL's composite score expands a
   breakdown panel: the three factor contributions (raw → normalized → weighted →
   total) and the evidence behind each — verified sources with verbatim quotes + URLs
   (relevance), co-author/affiliation list (reach), relevant/total counts (ratio).
3. **Per-section explainers** — each section carries a short "How to read this" note:
   the thresholds in play (current weights, tier percentiles, `as_of_year`,
   `min_ratio_denominator`) and what the graph/heatmap means.
4. **`as_of_year` banner** — shown when backtesting, so labels are unambiguous about
   which year's data they reflect.

Excel export gains per-factor columns (raw + normalized + weighted contributions).

---

## 10. Testing

Unit tests per new pure function, following the existing `tests/` one-file-per-stage
pattern with Snowflake/Bedrock/**ONNX (VectorCreator + Reranker) mocked**:
- feature computation: co-author reach dedup (ORCID/name), affiliation count, ratio +
  `min_ratio_denominator` guard;
- the three normalization modes (percentile/minmax/zscore) incl. degenerate pools;
- composite weighting (weights from config, tie handling);
- hybrid vector SQL builder (correct table, website-ID restriction, threshold/top-K);
- `as_of_year` capping across Stage 01 queries (window, history, denominator);
- `06_backtest_compare.py` diff (tier moves, rising→KOL transitions).

---

## 11. Open items to confirm on first live run

- Presence + column layout of `WEBSITES_VERTICAL_EMBEDDINGS_512` and
  `PUBMED_EMBEDDINGS_512` under `CUST_TC.ADIPOS_AMBU_FINAL` (user confirms they exist).
- Availability of `ORCID` (for reach dedup) and `AFFILIATION` in `CORE.PUBMED.AUTHOR`
  for the ADIPOS PMIDs; fallback to normalized-name dedup if ORCID sparse.
- Sensible default for `vector_sim_threshold` — calibrate on the first run against a few
  known synonym cases (Adipositas/Obesity).
- Whether authorship position (senior/last author) is available to weight reach later
  (not used this iteration).

---

## 12. Confirmed decisions

- No labels → transparent config-weighted composite, not a learned model; backtest is
  the empirical validation.
- Relevance-dominant weighted composite (0.60 / 0.25 / 0.15 defaults), percentile-rank
  normalization by default, all in `config.ini`.
- Co-author reach = PubMed co-authors only (web co-mentions display-only).
- Ratio denominator = topic-agnostic totals; neutralized below `min_ratio_denominator`.
- Hybrid relevance ON; `WEBSITES_VERTICAL_EMBEDDINGS_512` only (drop
  `WEBSITES_EMBEDDINGS_512`); LLM verify remains the precision arbiter.
- Congress factor design-for-later (slot reserved).
- `as_of_year` time machine built now; `06_backtest_compare.py` included (simple).
- Report: co-authorship network graph, click-through score transparency, per-section
  explainers.
