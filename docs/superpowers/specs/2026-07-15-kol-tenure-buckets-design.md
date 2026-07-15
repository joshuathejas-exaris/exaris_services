# KOL vs Rising Star — tenure partition, absolute floors, score-development chart

**Date:** 2026-07-15
**Service:** 2.1 KOL Identification & Mapping (`b_kol_identification/`)
**Status:** design — pending user review
**Supersedes (scoring section only):** `2026-07-13-kol-scoring-model-design.md`. The LLM
ground→verify funnel (Stages 01–03) and the composite's honesty guardrail are retained;
this changes how the composite is used, how buckets are assigned, and what the report shows.

---

## 1. Problem

When the HTML reports were shown to the team, the objection was: **a rising star cannot
also be a KOL.** Today they can — `kol_score` (a *level*) and `rising_star` (a *slope*) are
computed independently in `04_assemble_kols.py`, so nothing stops a Tier-A KOL from also
carrying the Rising badge. The overlap is conceptually wrong: a rising star is someone
*climbing toward* KOL status, i.e. explicitly **not yet** a KOL.

Two deeper concerns sit underneath the visible one:

- **No single truth of "who is a KOL."** All three factors (relevance, reach, ratio) are
  percentile-normalized across the shortlist, so "KOL" means only "top of *this* pool."
  Robust across rare/common indications, but purely relative — there is no absolute bar.
- **Factor design.** Ratio is already an intrinsic 0–1 quantity, yet it is percentile-
  normalized like the counts, throwing away its absolute meaning. (An LLM 1–5 relevance
  score per source was considered and **rejected**: it re-expresses ratio + the existing
  verify pass and is uncalibrated across indications — data-relative hidden inside the
  model. The binary verify bar is more reproducible.)

## 2. Goals

1. KOL and rising-star buckets are **mutually exclusive** by construction.
2. "KOL" gains an **absolute** meaning (real-engagement floors) while ranking stays
   data-relative and robust across indications.
3. Distinguish **young researcher vs. established professor** using data actually
   available (no author-order column exists in `CORE.PUBMED.AUTHOR`, confirmed — so
   author-position seniority is out; **publication tenure** is the proxy).
4. Richer report: total publications per KOL, a per-year **total-vs-relevant** stacked
   chart, and a per-KOL **score-development line chart** across years with tier bands.

## 3. Core model: two axes, one tenure partition

### Axis 1 — Level ("who has arrived")
The existing weighted composite, with **one change**: ratio is used **raw (0–1)**, not
percentile-normalized. Relevance and reach remain percentile-normalized (unbounded counts
that genuinely need a pool reference; ratio does not).

```
kol_score = w_relevance·norm(relevance) + w_reach·norm(reach) + w_ratio·ratio_raw
```

The composite's role is now **ranking *within* a bucket** — tiers A/B/C among KOLs,
ordering among rising stars.

### Axis 2 — Tenure ("career stage")
```
relevant_tenure = anchor_year − first_verified_relevant_year + 1
```
derived from `verified_pubmed_years`. HCPs with no verified PubMed years (web-only voices)
have **undefined** tenure.

### The partition (computed after Stage-03 verification, in order)

1. **Rising star** — `relevant_tenure ≤ rising_star_max_tenure_years` (default 3) **AND**
   genuinely active (`total verified pubs ≥ rising_star_min_pubs`, default 3, to exclude
   one-offs). These are **pulled out of the KOL pool first.** Web-only HCPs (undefined
   tenure) are never rising stars — "rising" is a publication-trajectory concept.
2. **KOL-eligible** — everyone else (tenure > 3y, or web-only).
3. A KOL-eligible HCP is a **KOL** only if it clears **all four absolute floors** (§4).
   Failing the floors → featured in neither bucket (the stale long-tenured name).
4. **KOL tiers** (A/B/C percentile thresholds) are computed over the **KOL pool only**, so
   rising stars no longer distort the KOL distribution.
5. **Breakout badge** — a rising star whose `kol_score` would have reached KOL Tier A is
   badged "Breakout" so an exceptional fast riser is surfaced, not lost (this is the
   accepted trade-off of the pure tenure partition: a prolific 3-year newcomer is bucketed
   as a rising star, but flagged as exceptional).

Rising stars are **ranked among themselves by `kol_score`** (the level composite).

## 4. Absolute floors (what gives "KOL" absolute meaning)

All four must hold for a tenure-eligible HCP to be a KOL. Values live in `[scoring]` and are
tunable; defaults chosen low enough to stay safe across rare vs. common indications.

| Floor | Config key | Default | Rule |
|---|---|---|---|
| Min verified sources | `kol_floor_min_verified` | 5 | `verified_web_count + verified_pubmed_count ≥ 5` |
| Min relevance ratio | `kol_floor_min_ratio` | 0.10 | `ratio.ratio ≥ 0.10` (thin-profile neutral ratios, `ratio.neutral==true`, are treated as failing this floor) |
| Recent activity | `kol_floor_active_within_yrs` | 5 | ≥1 verified relevant source that is current: any verified **web** source (web is timestamp-free → treated as current) **OR** ≥1 verified PubMed pub within `active_within_yrs` of the anchor |
| Min co-author reach | `kol_floor_min_coauthors` | 3 | `reach.distinct_coauthors ≥ 3` — **waived** for HCPs with no PubMed activity at all (`verified_pubmed_count == 0`), so a purely web-based voice is not silently deleted |

## 5. Config changes (`config.ini`)

```ini
[funnel]
pubmed_window_years   = 10   # was 5  — widens the relevance SCORING window (not just display)
pub_history_years     = 10   # was 20
top_n_candidates      = 100  # was 75 — mitigate funnel-starving low-volume rising stars

[scoring]
rising_star_max_tenure_years = 3     # new — the KOL/rising-star partition line
kol_floor_min_verified       = 5     # new
kol_floor_min_ratio          = 0.10  # new
kol_floor_active_within_yrs  = 5     # new
kol_floor_min_coauthors      = 3     # new (waived if the HCP has no PubMed)
# ratio is no longer percentile-normalized in apply_composite
# rising_star_growth is retained but no longer the primary rising-star gate (tenure is)
```

**Funnel-starvation note:** the Stage-01 shortlist ranks by `candidate_score` (raw source
volume), which favours established, high-volume names; genuine rising stars have lower
volume and could be cut before Stage 04 sees them. Bumping `top_n_candidates` 75→100 is the
first mitigation. Whether it is sufficient is a **first-live-run validation item** — if
rising stars are still starved, reserve a quota of shortlist slots for short-tenure / high
CF-treffer candidates. Documented, not silently capped.

## 6. Stage-by-stage changes

### Stage 01 — `01_fetch_and_shortlist.py`
- Widen windows via config (no code change beyond reading the new values).
- **New query + map:** all-publications-per-year, **CF filter removed**, over
  `pub_history_years` ending at the anchor → `total_pub_by_year` per HCP (a
  `{year: count}` map). Feeds both the stacked chart (§6 Stage 05) and the per-year total
  denominator used by the score-development reconstruction. Existing CF-flagged
  `pub_by_year` and the single `total_pubmed_sources` count are retained.

### Stage 04 — `04_assemble_kols.py`
- `apply_composite`: use `ratio.ratio` **raw** (do not pass it through `normalize_values`);
  relevance and reach unchanged.
- New `compute_tenure(verified_pubmed_years, anchor_year) -> {relevant_tenure, first_year}`.
- New `partition_buckets(...)`: apply the tenure rule + activity gate → `rising_star`
  boolean; mark KOL-eligible; apply the four floors (`passes_kol_floors(...)`) → `is_kol`.
- `flag_rising_stars` is **replaced** by the tenure-based rule (keep the function name and
  its `verified_pubmed_years`-only input for continuity; change the body).
- Tiers: compute percentile thresholds over the **KOL pool only** (HCPs with `is_kol`),
  not the whole shortlist.
- `breakout` boolean on rising stars whose `kol_score ≥ tier_a_threshold` (computed on the
  KOL pool).
- New `build_score_trajectory(hcp, anchor_year, span, ref_dist, weights, thresholds)`:
  for each year `Y` in `[anchor − span + 1 .. anchor]`, recompute
  `relevance(Y)` (constant web + verified PubMed in the rolling `pubmed_window_years`
  ending at Y), `reach(Y)` (distinct co-authors from verified pubs with year ≤ Y),
  `ratio(Y)` (relevance(Y) ÷ total sources as of Y, using `total_pub_by_year`), and
  `tenure(Y)`. Map relevance/reach to [0,1] against a **fixed reference distribution**
  captured from the final pool (§7); ratio raw; combine into `score(Y)`; assign `tier(Y)`
  by the final KOL-pool thresholds. Emit a compact per-year array. Computed for the
  reported KOLs (top `top_n_report`) to keep `kol_final.json` lean.
- Persist `relevant_tenure`, `first_relevant_year`, `is_kol`, `breakout`,
  `total_pub_by_year`, and `score_trajectory` on each HCP.

### Stage 05 — `05_generate_report.py`
- **KOL ranking / profiles:** show **total publications** (`total_pubmed_sources`) and a
  **career-stage label** derived from tenure (e.g. "Emerging (≤3y)", "Established").
- **Stacked per-year bar chart** (inline SVG) replacing/augmenting the sparkline: total
  pubs per year (`total_pub_by_year`) with the verified-relevant subset
  (`verified_pubmed_years`) stacked/overlaid.
- **Rising Stars section:** now provably disjoint from KOLs; show `relevant_tenure` and the
  **Breakout** badge.
- **"Established, new to this indication"** highlight: long *total* tenure (from
  `total_pub_by_year` span) + short *relevant* tenure — surfaced as its own small callout.
- **Score-development line chart** (inline SVG) in each top-25 profile: the
  `score_trajectory` line with horizontal **tier bands (A/B/C)** drawn in, and a marker at
  the year tenure crossed the rising→KOL line. Web drawn as a labelled flat baseline; the
  PubMed-driven climb sits on top.
- **Explainers/caveats** (one line each): fixed-yardstick normalization; web is a
  time-invariant baseline; the development chart traces a single run's people *backward*
  and therefore **cannot** show pool entry/exit or demotions (that is Stage 06's job); it
  applies today's verification verdicts to historical years ("time machine with today's
  knowledge").
- Update the executive-dashboard counts so "KOLs" and "Rising Stars" are disjoint totals.

### Stage 06 — `06_backtest_compare.py`
- **Unchanged.** Still the tool for genuine pool entry/exit and demotions across two runs.
  Its narrative gets cleaner: short-tenure rising stars in the earlier run becoming
  long-tenure KOLs in the later run is now a literal tenure transition.

## 7. Score-development reconstruction — normalization detail

**Fixed yardstick.** Capture, from the final pool, the sorted raw relevance and reach value
lists (the reference distributions) and the KOL-pool tier thresholds. Each historical
year's raw relevance/reach is mapped to [0,1] by its percentile *within those fixed
reference lists*; ratio is raw; the composite uses the production weights. Rationale: the
line then reflects **the individual's growth**, not pool churn, and is cheap (no per-year
pool re-normalization). Trade-off recorded: it answers "using today's pool as the ruler,
where would year-Y evidence rank?" rather than "what would the pipeline have scored in
year Y" — the latter would make the line jitter for pool reasons unrelated to the person.

## 8. Edge cases

- **Web-only HCP** (no verified PubMed): tenure undefined → KOL-eligible (never rising);
  co-author floor waived; recent-activity floor satisfied by verified web presence;
  `score_trajectory` is a flat web-only baseline (annotated).
- **Thin profile** (`ratio.neutral == true`, below `min_ratio_denominator`): fails the
  ratio floor → not a KOL (cannot be crowned on ~no denominator).
- **Tenure-eligible but weak** (long tenure, fails floors): neither bucket — correctly
  filtered.
- **Prolific 3-year newcomer**: rising star + Breakout badge (accepted trade-off).
- **Empty KOL pool** (all shortlisted HCPs are rising / fail floors): tiers degrade
  gracefully; `drop_zero_score` still keys off raw verified counts (unchanged).

## 9. Testing

Extend `tests/` (mock Snowflake/Bedrock), one concern per test:
- `partition_buckets`: mutual exclusivity holds for tenure/floor combinations.
- Each floor gates as specified, incl. co-author-floor waiver for web-only HCPs and
  ratio-floor rejection of neutral thin profiles.
- `apply_composite`: ratio passed through raw; relevance/reach still normalized.
- Tiers computed over KOL pool only (rising stars excluded from thresholds).
- `build_score_trajectory`: monotone-ish reconstruction on a synthetic year series;
  fixed-yardstick mapping; tenure crossing year correct; web-only flat baseline.
- Stage 01: `total_pub_by_year` query shape (CF filter removed) and map assembly.

## 10. Out of scope (future work)

- **Two-run entry/exit analysis** — using Stage 06 to understand who newly entered the pool
  and who left/was demoted between runs (the development chart deliberately cannot show
  this). Explicitly deferred at the user's request.
- Author-position seniority (no author-order data available).
- 4th factor (relevant congresses/Tagungen) — still designed-for-later, not built.
- PubMed vector-recall arm, reranker — unchanged, still off by default.

## 11. Confirmed decisions

- Two axes (level vs. tenure), mutually exclusive via **pure tenure partition** (tenure ≤3y
  & active → rising star; else KOL-eligible then floor-gated).
- KOL bar = **relative ranking + absolute floors** (all four floors selected).
- Ratio used **raw**; LLM 1–5 relevance rejected.
- `pubmed_window_years` 5→10, `pub_history_years` 20→10, `top_n_candidates` 75→100.
- Score-development chart: **fixed-yardstick** normalization, web as annotated constant
  baseline, per top-25, with tier bands and a tenure-crossing marker.
- Stages 01/04/05 change; Stage 06 unchanged.
