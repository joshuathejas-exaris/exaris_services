# Service 2.1 — KOL Identification & Mapping

## What this service does

Ranks HCPs as Key Opinion Leaders for a given indication (e.g. Obesity) using a
transparent, config-weighted composite of three factors, all computed **downstream
of** an LLM verify pass:

> **A KOL's score = weighted(relevance, co-author reach, relevance ratio)** — every
> factor is derived from sources an LLM already confirmed are genuinely relevant.

- **Relevance** (`weight_relevance`, default 0.60, backbone) — verified relevant
  source count (`verified_web_count + verified_pubmed_count`), now fed by a **wider
  hybrid candidate net** at Stage 01 (keyword gate ∪ vector recall) instead of just
  keyword gate.
- **Co-author reach** (`weight_reach`, default 0.25) — distinct co-authors across the
  HCP's verified-relevant PubMed articles (`04_assemble_kols.py::compute_reach`).
- **Relevance ratio** (`weight_ratio`, default 0.15) — verified-relevant sources ÷ all
  of the HCP's sources, i.e. topical focus (`compute_ratio`); neutral (0) below
  `min_ratio_denominator` sources so thin profiles aren't penalized or rewarded.

Each factor is normalized across the shortlisted pool (`normalization` = `percentile`
by default; `minmax`/`zscore` also supported) and combined:
`kol_score = weight_relevance·norm_relevance + weight_reach·norm_reach + weight_ratio·norm_ratio`
(`04_assemble_kols.py::apply_composite`). "Genuinely relevant" is still decided by an
LLM on both evidence tracks — not by keyword co-occurrence, not by a global
similarity percentile, and not by a DigiScore.

**Honesty guardrail (do not regress):** these three factors are dimensions of
*influence on top of verified relevance* — reach and ratio can only reweight HCPs who
already cleared the LLM verify bar. This is not a return to v1's
`composite = 0.45·norm_pub + 0.30·norm_cf + 0.25·norm_digi`, which rewarded raw
keyword co-occurrence and DigiScore rather than actual topical engagement and put
human geneticists and lab-medicine physicians in the Obesity top-50. Stage 03 (the
ground → verify pass) is untouched by this composite — verification still happens
before any of the three factors are computed.

---

## The cheap SQL → LLM funnel

```
ALL HCPs (~1,176)
   │  STAGE 01 — cheap SQL + a local ONNX embed call, NO LLM
   │  • Web candidates:    keyword gate (LLM_VALIDATION: NEAR_BY=1, IS_OLD=0,
   │                       IS_DOCTOR=1, IN_RELATION>29, COL_KEYWORDS matches a PCA
   │                       term) UNION a vector-recall arm when
   │                       [hybrid].hybrid_relevance=true: embed the indication +
   │                       PCA terms with VectorCreator (GTE multilingual ONNX,
   │                       768-dim, vendored from Service A) and cosine-match
   │                       against WEBSITES_VERTICAL_EMBEDDINGS_512, gated only by
   │                       NEAR_BY/IS_DOCTOR and vector_sim_threshold (default
   │                       0.55, top vector_top_k_per_hcp per HCP). The vector arm
   │                       widens RECALL only — the LLM verify pass at Stage 03 is
   │                       still the sole precision arbiter, so a wider net cannot
   │                       inflate the final score, only who gets a chance to earn
   │                       it. IS_OLD=0 is the only web freshness filter — no date
   │                       window.
   │  • PubMed candidates: PUBMED_ARTICLE_MAPPING (MERGE_RESULT>1) joined to a
   │                       PubMed CF-flag table, articles within the
   │                       pubmed_window_years (5) scoring window of the anchor
   │                       year, CF-treffer weighted. A separate pub_history_years
   │                       (20) query fetches a longer display-only publication
   │                       history per HCP for the report's chart (does not affect
   │                       candidate_score).
   │  • Anchor year: `as_of_year` (config, default `latest`) resolves to either
   │                       MAX(YEAR_VAL) in the PubMed CF table or a pinned 4-digit
   │                       year, capping the PubMed scoring window, history window,
   │                       and total-source denominator so a past year can be
   │                       replayed for the backtest (see below). Web sources are
   │                       timestamp-free and always shown as-is.
   │  candidate_score = web_candidate_count + pubmed_candidate_count
   │  (an upper bound on the final verified relevance factor — safe to rank/cut on)
   ▼
TOP 75 HCPs  ── everyone else is dropped here, before any LLM spend
   │  STAGE 02 — fetch full text, NO LLM, NO vector search
   │  • Web:    LLM_VALIDATION.CONTENT for each candidate WEBSITE_ID
   │  • PubMed: CORE.PUBMED.ARTICLE TITLE + ABSTRACT for each candidate PMID
   │  • recency-ordered per-HCP cap (max_sources_per_hcp) as a cost backstop
   ▼
STAGE 03 — LLM wiki-build (web + PubMed): ingest → ground → verify → map (Bedrock)
   │  A source "counts" only if it yields ≥1 grounded + adversarially-verified claim.
   │  Candidate counts can only SHRINK here. Unchanged by the composite score —
   │  still the sole precision arbiter for relevance.
   ▼
STAGE 04 — assemble: reach + ratio features, normalize, weighted composite
   │  (relevance + co-author reach + relevance ratio) → kol_score, tiers, rising
   │  stars, themes, collaboration network.
   ▼
STAGE 05 — report: TOP 25 KOLs → HTML + Excel
   │
   ▼
STAGE 06 (optional) — `06_backtest_compare.py` diffs two `as_of_year` runs
   (e.g. 2021 vs. latest) to see whether yesterday's rising stars became today's KOLs.
```

The 75 → 25 buffer absorbs candidates that look strong on cheap counts but collapse
under verification (the Humangenetiker/Labormedizin failure mode) — they fall out;
genuine candidates take their place.

**Why a vector arm now:** the keyword gate can miss relevant HCPs whose web content
doesn't happen to contain a literal PCA-term match. The vector arm (Task 3) recovers
those via embedding similarity, but only ever *adds candidates to the funnel* — it
never bypasses Stage 03. `pubmed_vector_arm` (against `PUBMED_EMBEDDINGS_512`) exists
as a config flag but is off by default and unwired this iteration — PubMed recall is
still keyword/CF-flag only. `reranker.py` (`Reranker`, a cross-encoder over the
mmarco mMiniLM ONNX model) is vendored alongside `vector_creator.py` for future use;
`[hybrid].rerank` is off by default and no stage currently calls it.

---

## Pipeline

```
01_fetch_and_shortlist.py   →  data/shortlist.json
02_retrieve_sources.py      →  data/sources.json
03_wiki_build.py            →  data/wiki.json
04_assemble_kols.py         →  data/kol_final.json
05_generate_report.py       →  results/kol_report_<ts>.html + .xlsx
06_backtest_compare.py      →  data/backtest_compare.json  (optional, needs two kol_final.json runs)
```

Every stage is resume-safe: skip if output exists unless `--force` (matching
Service A).

Run order:
```
python 01_fetch_and_shortlist.py
python 02_retrieve_sources.py
python 03_wiki_build.py
python 04_assemble_kols.py
python 05_generate_report.py
```

Stage 01 reads `data/input.json` (`{"indication": "...", "client_drug": "..."}`) to
seed the run.

To backtest, set `[funnel].as_of_year` to a past 4-digit year, run the pipeline with
`--force` into separate `data/` copies (or move `kol_final.json` between runs), then:
```
python 06_backtest_compare.py --earlier data/kol_final_2021.json --later data/kol_final_latest.json
```
It reports (and writes `data/backtest_compare.json` with) rising-star→KOL promotions,
tier moves, and brand-new KOLs between the two runs.

Tests (mock Snowflake/Bedrock):
```
.venv/bin/python -m pytest b_kol_identification/tests -q
```

---

## Files

| File | Purpose |
|------|---------|
| `01_fetch_and_shortlist.py` | Cheap-SQL + vector-recall candidate counts (web + PubMed) per HCP: keyword-gated web rows ∪ vector-recall web rows (via `VectorCreator` + `WEBSITES_VERTICAL_EMBEDDINGS_512`, gated by `[hybrid]`), merged by `merge_web_ids`. Resolves the `as_of_year` anchor (`resolve_anchor_year`), builds `candidate_score = web_candidate_count + pubmed_candidate_count`, the 20y display-only pub history, and per-HCP total-source counts (`build_totals_map`) used later as the ratio denominator. Sorts and flags the top `top_n_candidates` (default 75) as `shortlisted`. Emits all HCPs (for report totals). |
| `02_retrieve_sources.py` | Fetches full text for shortlisted HCPs only: web `CONTENT` from `LLM_VALIDATION`, PubMed `TITLE`+`ABSTRACT` from `CORE.PUBMED.ARTICLE`. Recency-ordered per-HCP cap (`max_sources_per_hcp`) as a cost backstop, not a relevance filter. |
| `03_wiki_build.py` | Per source: Bedrock ingest (Nova Pro, `[bedrock].ingest_model_id`) extracts claims → deterministic quote-grounding (`verbatim_quote` must be a literal substring of the source text) drops fabrications before any verify call → adversarial verify (Qwen, `[bedrock].verify_model_id`) confirms genuine engagement → maps `mentioned_hcps` to the roster by name. A source counts only if it yields ≥1 grounded + verified claim. Untouched by the hybrid arm and the composite score — still the sole precision arbiter. |
| `04_assemble_kols.py` | Computes the three factors — relevance (`verified_web_count + verified_pubmed_count`), co-author reach (`compute_reach`, PubMed-only, dedup by ORCID/normalized name, self excluded), relevance ratio (`compute_ratio`, neutral below `min_ratio_denominator`) — normalizes each across the pool (`normalize_values`: `percentile`/`minmax`/`zscore`) and combines them into `kol_score` (`apply_composite`, weights from `[scoring]`). Tiers A/B/C from the `kol_score`-distribution percentile thresholds (`tier_a_percentile`, `tier_b_percentile`); Rising Stars (v1 logic — `new_voice` / `accelerating`) computed on verified-relevant PubMed articles by year only; theme aggregation from verified claims; `top_affiliations` for the network graph; collaboration network from `CORE.PUBMED.AUTHOR` co-authors (incl. non-mapped external authors) + web co-mentions from `mentioned_hcps`. |
| `05_generate_report.py` | Self-contained HTML (no CDN/fonts/network, inline SVG sparklines) — executive dashboard, KOL ranking (top 25) with a per-KOL click-through score drill-down (`render_score_breakdown`: weight/norm/contribution/evidence per factor), Rising Stars, Thematic heatmap, Regional distribution, an inline-SVG collaboration network graph with real co-author affiliations (`render_network_svg`), per-section "how to read this" explainers (`section_explainer`), an `as_of_year` backtest banner (`as_of_banner`) when the run is capped to a past year, Individual KOL profiles with verbatim quotes. Also writes an Excel export (one row per KOL). |
| `06_backtest_compare.py` | Diffs two `as_of_year` runs' `kol_final.json` (`compare_runs`): rising-star→KOL promotions, tier moves, brand-new KOLs. CLI: `--earlier`/`--later`; writes `data/backtest_compare.json`. |
| `vector_creator.py` | `VectorCreator` — embeds text with the GTE multilingual ONNX model (768-dim, L2-normalized), vendored from Service A's `assets/` (no cross-service import). Used by Stage 01's vector-recall arm. |
| `reranker.py` | `Reranker` — cross-encoder over the mmarco mMiniLM ONNX model, vendored from Service A's `assets/mmarco-reranker`. Not currently called by any stage; `[hybrid].rerank` is off by default (reserved for a future rerank-the-recall-arm step). |
| `pipeline_common.py` | Shared helpers reused from Service A's pattern: `call_bedrock_json`, `strip_json_fences`, `parse_json_object`, `make_bedrock_client`, `name_matches`, `normalize_name`, `connect_snowflake`, `resolve_tables`. |
| `config.ini` | All tunable params: `[snowflake]` connection/schema knobs, `[terms]`, `[funnel]` (incl. `as_of_year`, `pub_history_years`), `[hybrid]` (vector arm + rerank flags/thresholds), `[bedrock]`, `[scoring]` (composite weights, normalization, tier/rising-star thresholds), `[report]`. Table FQNs are derived at runtime by `pipeline_common.resolve_tables`, not hand-maintained here. |
| `tests/` | Pytest unit tests, one file per stage + `pipeline_common` (mock Snowflake/Bedrock). |
| `data/` | JSON checkpoints (gitignored). `input.json` seeds the run (`indication`, `client_drug`); not gitignored. |
| `results/` | HTML + Excel outputs (gitignored). |
| `scoring_review/` | v1-vs-v2 scoring comparison artifacts kept for reference. |

---

## Snowflake tables

| Table (config key) | Database.Schema | Purpose | Key columns |
|---|---|---|---|
| `llm_validation` | `CUST_TC.ADIPOS_AMBU_FINAL` | Web gate + full `CONTENT` + keywords | `NEAR_BY, IS_OLD, IS_DOCTOR, IN_RELATION, COL_KEYWORDS_ORIG, COL_KEYWORDS_EN, WEBSITE_ID, S_CUSTOMER_ID, CONTENT` |
| `rating_result_final` | `CUST_TC.ADIPOS_AMBU_FINAL` | HCP A/B/C/D rating | `S_CUSTOMER_ID, RATING` |
| `pubmed_cf_flag` | `CUST_TC.ADIPOS_AMBU_FINAL` (`PUBMED_CONTENT_FRAME_SINGLE_TBL`) | PubMed CF-term flags | `PMID, YEAR_VAL`, one column per CF term |
| `websites_vertical_all_source` | `CUST_TC.ADIPOS_AMBU_FINAL` | Web-source URLs (Stage 02 join) | `WEBSITE_ID, URL` |
| `websites_vertical_embeddings` (`WEBSITES_VERTICAL_EMBEDDINGS_512`) | `CUST_TC.ADIPOS_AMBU_FINAL` | Vector-recall web arm (Stage 01, `[hybrid].hybrid_relevance`) — per-chunk embeddings joined to `LLM_VALIDATION`, cosine-matched (`VECTOR_COSINE_SIMILARITY`) against the indication query vector. **Note:** the pipeline embeds/queries at 768 dims (`EMBEDDING_DIM` in `01_fetch_and_shortlist.py`) despite the `_512` table-name suffix — not yet live-run-verified, confirm the column's actual vector width on first live run. | `WEBSITE_ID, EMBEDDINGS` |
| `pubmed_embeddings` (`PUBMED_EMBEDDINGS_512`) | `CUST_TC.ADIPOS_AMBU_FINAL` | Resolved by `resolve_tables` for a PubMed vector-recall arm; **not queried yet** — `[hybrid].pubmed_vector_arm` is off by default and unwired this iteration (deferred by design) | — |
| `content_frame_spec` | `CUST_TC.ADIPOS_AMBU_TMP` | PCA / CF terms (Stage 01 Q1) | `COL_MAP, EN_TERM_1, PCA` |
| `customer_source` | `CUST_TC.ADIPOS_AMBU_TMP` | HCP master record | `S_CUSTOMER_ID, S_FIRSTNAME, S_LASTNAME, S_CITY, S_HCP_GROUP` |
| `pubmed_mapping` | `CUST_TC.ADIPOS_AMBU_TMP` (`PUBMED_ARTICLE_MAPPING`) | Verified PubMed authorship | `S_CUSTOMER_ID, PMID, MERGE_RESULT` |
| `pubmed_article` | `CORE.PUBMED` (`ARTICLE`) | Article text | `PMID, TITLE, ABSTRACT, YEAR_VAL, JOURNAL_NAME` |
| `pubmed_author` | `CORE.PUBMED` (`AUTHOR`) | Co-author network (Stage 04) | `PMID, ORCID, FIRSTNAME, LASTNAME, AFFILIATION` |

Specialty (`S_HCP_GROUP`) is displayed only — it is never used to filter or exclude
candidates; relevance verification does that job.

> **Not yet live-run-verified** (see spec §9): the cross-database table locations,
> the `IN_RELATION` numeric range, the PubMed CF-flag column layout, and `IS_OLD`
> semantics. Confirm these on the first live run.

---

## Snowflake connection

Same pattern as Service A: `pipeline_common.connect_snowflake` — boto3 + AWS Secrets
Manager private-key auth via `shared/` (`ParameterManager`, `SecretReader`). Each
stage adds the repo root to `sys.path`:
```python
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
```

---

## Design & plan

Original funnel (v2, superseded scoring section only — funnel/LLM ground-verify pass
retained unchanged):
- Spec: `docs/superpowers/specs/2026-07-09-kol-identification-v2-design.md`
- Plan: `docs/superpowers/plans/2026-07-09-kol-identification-v2.md`

Current scoring model — composite score, hybrid vector-recall arm, `as_of_year`
backtest:
- Spec: `docs/superpowers/specs/2026-07-13-kol-scoring-model-design.md`
- Plan: `docs/superpowers/plans/2026-07-13-kol-scoring-model.md`

Confirmed decisions: no DigiScore, no raw keyword co-occurrence, no global
similarity percentile; relevance is decided per source by the LLM ground-then-verify
pass, unchanged by the composite; the composite's three factors are dimensions of
influence computed downstream of that verify pass, not a replacement for it; Rising
Stars are computed only on verified-relevant PubMed output so an off-topic
publication burst can never crown a rising star; a 4th factor (relevant
congresses/Tagungen) is designed-for-later but not built this iteration; no PPTX
deliverable and no Neo4j graph this iteration — the collaboration network is computed
in-process and rendered inline; no supervised/learned model — there is no
ground-truth label for "is a KOL," so empirical validation comes from the
`as_of_year` PubMed time-machine backtest instead.
