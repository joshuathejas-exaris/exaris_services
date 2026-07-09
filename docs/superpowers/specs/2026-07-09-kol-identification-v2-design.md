# KOL Identification v2 — Pipeline Overview & Design

**Service:** 2.1 KOL Identification & Mapping (`b_kol_identification/`)
**Date:** 2026-07-09
**Status:** Design — awaiting approval before implementation
**Supersedes:** the v1 three-stage pipeline (`01_fetch_kol_data.py` → `02_score_and_tier.py` → `03_generate_report.py`) and its plan `docs/superpowers/plans/2026-06-25-kol-identification-mapping.md`.

---

## 1. Why we are rebuilding

The v1 pipeline scored HCPs with `composite = 0.45·norm_pub + 0.30·norm_cf + 0.25·norm_digi`. It produced professionally wrong rankings — human geneticists and lab-medicine physicians landed in the Obesity top-50. Root causes (documented in `b_kol_identification/scoring_review/`):

- **`norm_cf` is keyword co-occurrence, not engagement.** A CF flag only means an indication keyword and an HCP name appeared in the *same document*. It does not mean the HCP said anything about the indication.
- **PubMed "relevance" was a global similarity cut.** An article counted as relevant if its `MAX_SIM` was in the top 75th percentile of the whole corpus — no per-HCP topical check.
- **DigiScore (25% weight) distorted the ranking.** HCPs with ≤1 publication but a high DigiScore floated to the top.
- **No check that the HCP actually engages with the indication.**

## 2. Guiding principle

> **A KOL's score = the number of genuinely relevant sources attributed to them.**

"Genuinely relevant" is decided the same way on both evidence tracks:

- **Web** (`LLM_VALIDATION`): an LLM confirms the HCP is *actively engaging with / sharing a view on the indication* in the document — not merely co-present. Attribution matters: the HCP must be the one discussing it.
- **PubMed** (`CORE.PUBMED.ARTICLE`): authorship is already structurally verified (`merge_result > 1`), so the LLM confirms the *article itself genuinely concerns the indication* (the author actively contributing to the indication's science).

No DigiScore. No raw keyword co-occurrence. No global similarity percentile.

This directly encodes the client's stated KOL criteria: **# relevante Quellen (mehr als andere)** (relevant-source count) and **Zeitstempel** (recency, via source year).

## 3. Goals / Non-goals

**Goals**
- Rank HCPs by count of LLM-verified relevant sources across web + PubMed.
- Kill off-target specialties through relevance verification, not brittle exclusion lists.
- Produce a trustworthy top-25 KOL report (HTML) + HCP-level Excel export.
- Capture thematic distribution and a collaboration network (PubMed co-authors + web co-mentions).
- Keep LLM spend bounded: cheap SQL narrows to top-75 candidates before any LLM call.

**Non-goals (this iteration)**
- No PPTX deliverable (catalog paid deliverable; deferred).
- No Neo4j graph database; the network is computed in-process and rendered inline.
- No vector search / embeddings (see §6).
- No multi-temporal re-extraction; single extraction per run (Rising-Stars uses PubMed year history within one run).

## 4. The cost funnel

```
ALL HCPs (~1,176)
   │  STAGE 01 — cheap SQL only, NO LLM
   │  • Web candidates:   LLM_VALIDATION gated
   │                      (NEAR_BY=1, IS_OLD=0, IS_DOCTOR=1, IN_RELATION>29,
   │                       COL_KEYWORDS matches a PCA term)   -- IS_OLD=0 is the
   │                       only web freshness filter; no date window
   │                      → candidate website_ids + count per HCP
   │  • PubMed candidates: PUBMED_ARTICLE_MAPPING (merge_result>1)
   │                       ∩ PubMed CF-term flag, article ≤ 5 yrs old
   │                      → candidate PMIDs + years + count (CF-treffer weighted) per HCP
   │  candidate_score = web_candidate_count + pubmed_candidate_count
   │  (an UPPER BOUND on the final verified score → safe to rank on)
   ▼
TOP ~75 HCPs   ── everyone else dropped here, before any LLM spend
   │  STAGE 02 — fetch full text, NO LLM, NO vector search
   │  • Web:    LLM_VALIDATION.CONTENT for each candidate website_id
   │  • PubMed: CORE.PUBMED.ARTICLE.TITLE + ABSTRACT for each candidate PMID
   │  • recency-ordered per-HCP cap as a pure cost backstop (default generous)
   ▼
STAGE 03 — LLM WIKI (web + PubMed):  ingest → ground → verify → map  (Bedrock)
   │  A source "counts" only if it yields ≥1 grounded + adversarially-verified claim.
   │  Both candidate counts can only SHRINK here.
   ▼
FINAL SCORE = verified_web_count + verified_pubmed_count
   │  STAGE 04 — assemble: score, tiers, rising stars, network, themes
   ▼
STAGE 05 — report: TOP 25 KOLs → HTML + Excel
```

The 75 → 25 buffer absorbs candidates that look strong on cheap counts but collapse under verification (the Humangenetiker/Labormedizin failure mode). They fall out; genuine candidates take their place.

## 5. Stages, tables & checkpoints

| Stage | File | Snowflake tables used | LLM? | Output checkpoint |
|---|---|---|---|---|
| 01 | `01_fetch_and_shortlist.py` | `CONTENT_FRAME_SPEC` (PCA='X'); `LLM_VALIDATION`; `PUBMED_ARTICLE_MAPPING`; PubMed CF-flag table; `CUSTOMER_SOURCE` + `RATING_RESULT_FINAL` | No | `data/shortlist.json` |
| 02 | `02_retrieve_sources.py` | `LLM_VALIDATION` (CONTENT); `CORE.PUBMED.ARTICLE` (TITLE, ABSTRACT, YEAR_VAL) | No | `data/sources.json` |
| 03 | `03_wiki_build.py` | — | **Yes** (Nova Pro ingest + Qwen verify) | `data/wiki.json` + `wiki/<ts>/` tree |
| 04 | `04_assemble_kols.py` | `CORE.PUBMED.AUTHOR` (co-authors incl. non-mapped) | No | `data/kol_final.json` |
| 05 | `05_generate_report.py` | — | No | `results/kol_report_<ts>.html` + `.xlsx` |

All stages are resume-safe: skip if their output exists unless `--force` (matching Service A).

### 5.1 Stage 01 — fetch & shortlist (cheap SQL)

**Q1 — PCA terms.** `SELECT COL_MAP AS TERM_KEY, EN_TERM_1 AS TERM_EN FROM {schema_v1}.CONTENT_FRAME_SPEC WHERE UPPER(PCA) = 'X'`. (Configurable to *all* CF terms via `[terms] use_pca_only`.)

**Q2 — Web candidates per HCP.** Gate on `LLM_VALIDATION`, one candidate row per `(S_CUSTOMER_ID, WEBSITE_ID)`:
```sql
SELECT lv.S_CUSTOMER_ID, lv.WEBSITE_ID, lv.IN_RELATION, <year/recency col>
FROM {llm_validation} lv
WHERE lv.NEAR_BY = 1 AND lv.IS_OLD = 0 AND lv.IS_DOCTOR = 1
  AND lv.IN_RELATION > 29
  AND (<COL_KEYWORDS_ORIG/EN ILIKE any PCA term>)
```
`IS_OLD = 0` is the only web freshness filter — **no date window on web**. Refine the loose `ILIKE` predicate to whole-token matches in Python (reuse Service A's `matches_keywords`). Count → `web_candidate_count`; keep the website_id list.

**Q3 — PubMed candidates per HCP.** Verified authorship ∩ topical:
```sql
SELECT m.S_CUSTOMER_ID, m.PMID, cf.YEAR_VAL
FROM {pubmed_article_mapping} m           -- CUST_TC.ADIPOS_AMBU_TMP.PUBMED_ARTICLE_MAPPING
JOIN {pubmed_cf_flag_tbl} cf ON cf.PMID = m.PMID  -- PUBMED_CONTENT_FRAME_SINGLE_TBL
WHERE m.MERGE_RESULT > 1
  AND <cf has ≥1 PCA-term flag set>
  AND cf.YEAR_VAL >= (current_year - 5)
```
Count → `pubmed_candidate_count`; keep PMID+year list.

**Q4 — HCP metadata.** `CUSTOMER_SOURCE` + `RATING_RESULT_FINAL` (name, city, `S_HCP_GROUP` specialty, A/B/C/D rating). Specialty is **displayed only**, never used to filter.

**Shortlist.** `candidate_score = web_candidate_count + pubmed_candidate_count`; sort desc, tie-break by rating then recency; flag `top_n_candidates` (default **75**). Emit ALL HCPs (for report totals) with a `shortlisted` bool; only shortlisted carry their website_id/PMID lists forward.

### 5.2 Stage 02 — retrieve source text (cheap SQL, no vector search)

For each shortlisted HCP:
- **Web:** `SELECT WEBSITE_ID, CONTENT, URL... FROM {llm_validation} WHERE WEBSITE_ID IN (...) AND S_CUSTOMER_ID = ...`. Full `CONTENT` (Service A `content_source = llm_validation`), truncated to `max_source_chars`.
- **PubMed:** `SELECT PMID, TITLE, ABSTRACT, YEAR_VAL, JOURNAL_NAME FROM CORE.PUBMED.ARTICLE WHERE PMID IN (...)`. Text sent to LLM = `TITLE + "\n\n" + ABSTRACT`.
- **Cost backstop:** `max_sources_per_hcp` cap, ordered by recency (and `IN_RELATION` desc for web). Default generous; not a relevance filter.

`sources.json` = per HCP: meta, `web_sources[{website_id, url, full_text}]`, `pubmed_sources[{pmid, year, title, abstract}]`.

### 5.3 Stage 03 — LLM wiki-build (web + PubMed)

Reuses Service A's engine: `pipeline_common.call_bedrock_json`, two-model ground-then-verify, `ThreadPoolExecutor`, `wiki/<ts>/` tree, `drops.json`.

**Ingest** (Nova Pro, temp 0.0) — one call per source. Unified task, minor per-track wording:
```json
{"claims":[{
  "speaker_name":   "<HCP named/authoring — from the roster>",
  "verbatim_quote": "<exact span copied from the source text>",
  "statement":      "<one line: how the HCP engages with the indication>",
  "sentiment":      "positive|neutral|negative|ambivalent",
  "themes":         ["<which PCA terms this is about>"],
  "mentioned_hcps": ["<other doctor names appearing in the text>"],
  "confidence":     "high|medium|low"
}]}
```
- *Web prompt:* extract only where the named HCP is **actively expressing a view/engagement on the indication**; exclude mere name mentions and COI/financial disclosures.
- *PubMed prompt:* the author is known; extract whether the **article genuinely concerns the indication** and its themes; `mentioned_hcps` optional (co-authors come structurally in Stage 04).

**Ground** (deterministic, no LLM): `verbatim_quote` must be a whitespace-normalised, casefolded substring of the source text. Fabricated quotes dropped for free *before* any verify call (Service A cost control). COI regex safety-net retained for web.

**Verify** (Qwen, adversarial, ≤256 tokens): `{"verified": true|false}` answering *"Does this source genuinely show this HCP engaging with the indication?"* Using a different model than ingest is deliberate.

**Map:** resolve `speaker_name` and `mentioned_hcps` against the targeting roster by name (Service A `name_matches`).

A source is **relevant** for the HCP iff it produced ≥1 grounded + verified claim. `wiki.json` = per HCP: `verified_web_sources`, `verified_pubmed_sources`, all verified claims (with themes, sentiment, citations), and web co-mentions.

### 5.4 Stage 04 — assemble KOLs (no LLM)

- **Final score** = `len(verified_web_sources) + len(verified_pubmed_sources)`.
- **Rank** desc; recency (latest verified source year) as secondary sort.
- **Tiers A/B/C** from the score distribution of shortlisted HCPs (percentile thresholds, config).
- **Rising Stars** — v1 logic (`new_voice`: recent ≥ `min_pubs` and prior == 0; `accelerating`: recent/prior ≥ `growth_factor`), computed on **verified-relevant PubMed articles by year**. Relevance-filtered so we never crown a rising star on off-topic output. Recent/prior split from PubMed `YEAR_VAL`.
- **Collaboration network:**
  - *PubMed co-authors* (structural, incl. non-targeted HCPs): `SELECT * FROM CORE.PUBMED.AUTHOR WHERE PMID IN (<verified PMIDs of shortlisted KOLs>)`. Edges between our KOLs, and KOL → external co-author.
  - *Web co-mentions* (from LLM `mentioned_hcps`, mapped to roster).
- **Thematic distribution** — aggregated `themes` across each HCP's verified claims (web + PubMed).

`kol_final.json` = `{indication, client_drug, generated_at, pca_terms, hcps:[...], coauthor_edges:[...], comention_edges:[...]}`.

### 5.5 Stage 05 — report (HTML + Excel)

**HTML** — self-contained (no CDN/fonts/network), inline SVG. Sections (re-powered from v1, top 25):
Executive dashboard · KOL Ranking (top 25, with verified-source counts + verbatim-quote-backed) · Rising Stars · Thematic Distribution · Regional Distribution · **Collaboration Network** (PubMed co-authors + web co-mentions) · Individual KOL Profiles (real verified quotes + source links).

**Styling** — professional multi-hue palette, not the monotone blue: deep navy/slate base; tiers A/B/C in emerald / steel-blue / slate; sentiment in teal / amber / muted-red. Solid and corporate. `dataviz` skill consulted at build time; palette validated for light/dark.

**Excel** — one row per KOL: name, institution/city, specialty, tier, verified-source count (web + PubMed split), top themes, rising-star flag, source URLs, representative verbatim quotes.

## 6. Why no vector search

Service A used vector search because its candidate pool was the *whole corpus* — cosine similarity was the mechanism that picked which documents were on-topic enough to send to the LLM. Here two things already do that job: (a) the funnel restricts to 75 HCPs, and (b) the `LLM_VALIDATION` gate + `IN_RELATION > 29` + PCA-term match already guarantees each web doc is on-topic. So we send **all gated content** for the 75 shortlisted HCPs — no embeddings, no `VectorCreator`, no reranker. A simple recency-ordered per-HCP cap bounds cost. This removes a whole dependency and a source of silent relevance error.

## 7. Configuration (`config.ini`)

```ini
[snowflake]
aws_profile   = AdministratorAccess-311524101909
warehouse     = COMPUTE_WH
database      = CUST_NOVO
schema_v1     = ADIPOS_AMBU_V1
schema_final  = ADIPOS_AMBU_FINAL
schema_tmp    = ADIPOS_AMBU_V1
; Fully-qualified cross-database tables (CONFIRM before first run — see §9)
llm_validation_tbl   = CUST_NOVO.ADIPOS_AMBU_FINAL.LLM_VALIDATION
pubmed_mapping_tbl   = CUST_TC.ADIPOS_AMBU_TMP.PUBMED_ARTICLE_MAPPING
pubmed_cf_flag_tbl   = CUST_TC.ADIPOS_AMBU_TMP.PUBMED_CONTENT_FRAME_SINGLE_TBL
pubmed_article_tbl   = CORE.PUBMED.ARTICLE
pubmed_author_tbl    = CORE.PUBMED.AUTHOR

[terms]
use_pca_only = true          ; false = use all CONTENT_FRAME_SPEC terms

[funnel]
in_relation_min       = 29
counting_window_years = 5     ; PubMed only; web freshness uses IS_OLD=0 (no date window)
top_n_candidates      = 75    ; HCPs advanced to the LLM
max_sources_per_hcp   = 40    ; cost backstop, recency-ordered
max_source_chars      = 24000

[llm_validation]             ; web gate flags
near_by   = 1
is_old    = 0
is_doctor = 1

[bedrock]                    ; reuse Service A models
ingest_model_id     = eu.amazon.nova-pro-v1:0
verify_model_id     = qwen.qwen3-235b-a22b-2507-v1:0
ingest_max_workers  = 5
verify_max_workers  = 5
extraction_max_tokens = 4096

[scoring]
tier_a_percentile    = 85
tier_b_percentile    = 60
rising_star_min_pubs = 3
rising_star_growth   = 3.0

[report]
top_n_report = 25
```

## 8. Reuse from Service A (`a_comp_hcp_communication/`)

- `pipeline_common.py`: `call_bedrock_json`, `strip_json_fences`, `parse_json_object`, `make_bedrock_client`, `name_matches`, COI helpers.
- `connect_snowflake` pattern (AWS Secrets Manager via `shared/`).
- The ground-then-verify two-model design and `wiki/<ts>/` tree + `drops.json` audit trail.
- Config-driven schemas; resume-safe JSON checkpoints.
- **Not reused:** `vector_creator.py`, `reranker.py` (dead in Service A too), embeddings tables.

## 9. Open items to confirm before/at implementation

1. **Cross-database table locations** — confirm the fully-qualified names in `[snowflake]`, especially: is `PUBMED_CONTENT_FRAME_SINGLE_TBL` in `CUST_TC.ADIPOS_AMBU_TMP`, and does the KOL run connect to `CUST_NOVO` while reaching `CUST_TC` / `CORE.PUBMED` (cross-DB references in one connection)?
2. **`IN_RELATION`** — confirmed numeric; verify range so `> 29` is the intended cut.
3. **PubMed CF-flag columns** — confirm the flag column names in `PUBMED_CONTENT_FRAME_SINGLE_TBL` and how "has a PCA-term flag" is expressed (per-term columns vs. a single flag), and its `YEAR_VAL`.
4. **`IS_OLD` semantics** — confirmed as the sole web freshness filter (`IS_OLD = 0`); no date window on web. The 5-year window applies to PubMed only.
5. **Token/cost sanity check** — after Stage 01, log per-HCP candidate counts for the top 75 to estimate LLM volume before running Stage 03.

## 10. Success criteria

- No off-target-specialty HCP (e.g. Humangenetik, Laboratoriumsmedizin) appears in the top 25 unless it survives LLM relevance verification.
- Every ranked KOL is backed by ≥1 grounded, verified, verbatim-quoted source.
- LLM is invoked only for the ≤75 shortlisted HCPs' sources within the 5-year window.
- Report + Excel generate self-contained, with the collaboration network populated from both PubMed co-authorship and web co-mentions.
