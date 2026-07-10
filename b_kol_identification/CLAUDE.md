# Service 2.1 — KOL Identification & Mapping

## What this service does

Ranks HCPs as Key Opinion Leaders for a given indication (e.g. Obesity) using a
single, honest principle:

> **A KOL's score = the number of genuinely relevant sources attributed to them.**

"Genuinely relevant" is decided by an LLM on both evidence tracks — not by keyword
co-occurrence, not by a global similarity percentile, and not by a DigiScore. The
score is `verified_web_count + verified_pubmed_count`: web mentions where the HCP is
actually engaging with / sharing a view on the indication, plus PubMed articles that
genuinely concern the indication and are structurally authored by the HCP.

This supersedes the v1 pipeline (`01_fetch_kol_data.py` → `02_score_and_tier.py` →
`03_generate_report.py`), which produced professionally wrong rankings — e.g. human
geneticists and lab-medicine physicians landing in the Obesity top-50 — because its
`composite = 0.45·norm_pub + 0.30·norm_cf + 0.25·norm_digi` score rewarded raw
keyword co-occurrence and DigiScore rather than actual topical engagement.

---

## The cheap SQL → LLM funnel

```
ALL HCPs (~1,176)
   │  STAGE 01 — cheap SQL only, NO LLM
   │  • Web candidates:    LLM_VALIDATION gated
   │                       (NEAR_BY=1, IS_OLD=0, IS_DOCTOR=1, IN_RELATION>29,
   │                        COL_KEYWORDS matches a PCA term). IS_OLD=0 is the
   │                       only web freshness filter — no date window.
   │  • PubMed candidates: PUBMED_ARTICLE_MAPPING (MERGE_RESULT>1) joined to a
   │                       PubMed CF-flag table, articles ≤5 years old, CF-treffer
   │                       weighted.
   │  candidate_score = web_candidate_count + pubmed_candidate_count
   │  (an upper bound on the final verified score — safe to rank/cut on)
   ▼
TOP 75 HCPs  ── everyone else is dropped here, before any LLM spend
   │  STAGE 02 — fetch full text, NO LLM, NO vector search
   │  • Web:    LLM_VALIDATION.CONTENT for each candidate WEBSITE_ID
   │  • PubMed: CORE.PUBMED.ARTICLE TITLE + ABSTRACT for each candidate PMID
   │  • recency-ordered per-HCP cap (max_sources_per_hcp) as a cost backstop
   ▼
STAGE 03 — LLM wiki-build (web + PubMed): ingest → ground → verify → map (Bedrock)
   │  A source "counts" only if it yields ≥1 grounded + adversarially-verified claim.
   │  Candidate counts can only SHRINK here.
   ▼
FINAL SCORE = verified_web_count + verified_pubmed_count
   │  STAGE 04 — assemble: tiers, rising stars, themes, collaboration network
   ▼
STAGE 05 — report: TOP 25 KOLs → HTML + Excel
```

The 75 → 25 buffer absorbs candidates that look strong on cheap counts but collapse
under verification (the Humangenetiker/Labormedizin failure mode) — they fall out;
genuine candidates take their place.

**Why no vector search:** the funnel already restricts to 75 HCPs, and the
`LLM_VALIDATION` gate (`IN_RELATION > 29` + PCA-term match) already guarantees each
web doc is on-topic. All gated content for the 75 shortlisted HCPs is sent to the
LLM directly — no embeddings, no `VectorCreator`, no reranker.

---

## Pipeline

```
01_fetch_and_shortlist.py   →  data/shortlist.json
02_retrieve_sources.py      →  data/sources.json
03_wiki_build.py            →  data/wiki.json
04_assemble_kols.py         →  data/kol_final.json
05_generate_report.py       →  results/kol_report_<ts>.html + .xlsx
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

Tests (mock Snowflake/Bedrock):
```
.venv/bin/python -m pytest b_kol_identification/tests -q
```

---

## Files

| File | Purpose |
|------|---------|
| `01_fetch_and_shortlist.py` | Cheap-SQL candidate counts (web + PubMed) per HCP, `candidate_score = web_candidate_count + pubmed_candidate_count`, sorts and flags the top `top_n_candidates` (default 75) as `shortlisted`. Emits all HCPs (for report totals). |
| `02_retrieve_sources.py` | Fetches full text for shortlisted HCPs only: web `CONTENT` from `LLM_VALIDATION`, PubMed `TITLE`+`ABSTRACT` from `CORE.PUBMED.ARTICLE`. Recency-ordered per-HCP cap (`max_sources_per_hcp`) as a cost backstop, not a relevance filter. |
| `03_wiki_build.py` | Per source: Bedrock ingest (Nova Pro, `[bedrock].ingest_model_id`) extracts claims → deterministic quote-grounding (`verbatim_quote` must be a literal substring of the source text) drops fabrications before any verify call → adversarial verify (Qwen, `[bedrock].verify_model_id`) confirms genuine engagement → maps `mentioned_hcps` to the roster by name. A source counts only if it yields ≥1 grounded + verified claim. |
| `04_assemble_kols.py` | `kol_score = verified_web_count + verified_pubmed_count`; tiers A/B/C from the score-distribution percentile thresholds (`tier_a_percentile`, `tier_b_percentile`); Rising Stars (v1 logic — `new_voice` / `accelerating`) computed on verified-relevant PubMed articles by year only; theme aggregation from verified claims; collaboration network from `CORE.PUBMED.AUTHOR` co-authors (incl. non-mapped external authors) + web co-mentions from `mentioned_hcps`. |
| `05_generate_report.py` | Self-contained HTML (no CDN/fonts/network, inline SVG sparklines) — executive dashboard, KOL ranking (top 25), Rising Stars, Thematic heatmap, Regional distribution, Collaboration network, Individual KOL profiles with verbatim quotes. Also writes an Excel export (one row per KOL). |
| `pipeline_common.py` | Shared helpers reused from Service A's pattern: `call_bedrock_json`, `strip_json_fences`, `parse_json_object`, `make_bedrock_client`, `name_matches`, `normalize_name`, `connect_snowflake`. |
| `config.ini` | All tunable params (Snowflake connection + schema knobs, terms, funnel, Bedrock, scoring, report). Table FQNs are derived at runtime by `pipeline_common.resolve_tables`, not hand-maintained here. |
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

- Spec: `docs/superpowers/specs/2026-07-09-kol-identification-v2-design.md`
- Plan: `docs/superpowers/plans/2026-07-09-kol-identification-v2.md`

Confirmed decisions: no DigiScore, no raw keyword co-occurrence, no global
similarity percentile; relevance is decided per source by the LLM ground-then-verify
pass; Rising Stars are computed only on verified-relevant PubMed output so an
off-topic publication burst can never crown a rising star; no PPTX deliverable and
no Neo4j graph this iteration — the collaboration network is computed in-process and
rendered inline.
