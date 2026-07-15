# KOL Identification & Mapping — Pipeline Design Spec

**Date:** 2026-06-25
**Service:** 2.1 KOL Identification & Mapping
**Stakeholder:** Medical Affairs / MSL Management
**Status:** Approved — ready for implementation planning

---

## 1. Context & Goals

Identify, score, and tier Key Opinion Leaders (KOLs) in a given therapeutic indication using VSH data already present in Snowflake. Output a self-contained HTML report suitable for Medical Affairs teams before launches, advisory boards, new indication entries, and MSL territory planning.

The pipeline measures *real activity* — publication output, digital reach, topical relevance — not self-reported lists or purchased KOL databases.

---

## 2. File Structure

```
b_kol_identification/
├── config.ini
├── data/
│   ├── input.json             ← { "indication": "Obesity", "client_drug": "Ozempic" }
│   ├── kol_raw.json           ← output of 01_fetch_kol_data.py
│   └── kol_scored.json        ← output of 02_score_and_tier.py
├── results/
│   └── kol_report_{timestamp}.html
├── 01_fetch_kol_data.py
├── 02_score_and_tier.py
└── 03_generate_report.py
```

---

## 3. Data Model

All schema names are read from `config.ini [snowflake]`. Schema constants below use the placeholder names; override in config for each indication.

| Table | Schema Key | Purpose |
|---|---|---|
| `CONTENT_FRAME_SPEC` | `schema_v1` | Term definitions. PCA = 'X' marks the subset representative of the indication. |
| `PUBMED_SCORED` | `schema_final` | MAX_SIM per PMID — keyword-based relevance scoring. |
| `PUBMED_CONTENT_FRAME_SINGLE` | `schema_v1` | HCP×PMID links with topic CF flags (0/1) and YEAR_VAL. |
| `DIGISCORE_RESULT` | `schema_v1` | Pre-computed digital presence: DIGI_SCORE (−20 to +50), OTM, VS, SOCIAL, SENTIMENT_RATING. |
| `CUSTOMER_SOURCE` | `schema_tmp` | HCP master record: name, specialty (S_HCP_GROUP), city. |
| `RATING_RESULT_FINAL` | `schema_final` | Upstream quality rating (A/B/C/D). Gate: only RATING IN ('A','B','C','D') included. |

---

## 4. Script Specifications

### 4.1 `01_fetch_kol_data.py`

**Reads:** `data/input.json`
**Writes:** `data/kol_raw.json`
**Resume-safe:** yes — skips if output exists unless `--force` is passed.

#### Q1 — PCA topic terms
Fetched once at startup; drives the dynamic CF column list in Q3.

```sql
SELECT TERM_KEY, TERM_EN
FROM {schema_v1}.CONTENT_FRAME_SPEC
WHERE UPPER(PCA) = 'X'
```

#### Q2 — Relevant PMID set (top-75th-percentile MAX_SIM)
Forms the relevance gate: only articles in this set count toward publication scores.

```sql
WITH pct AS (
    SELECT PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY MAX_SIM) AS p75
    FROM {schema_final}.PUBMED_SCORED
)
SELECT s.PMID
FROM {schema_final}.PUBMED_SCORED s
CROSS JOIN pct
WHERE s.MAX_SIM >= pct.p75
```

#### Q3 — Publication counts, year trend, and CF tag sums per HCP
CF column list is built dynamically from Q1 results using Python string formatting.

```sql
SELECT cf.S_CUSTOMER_ID,
       cf.YEAR_VAL,
       COUNT(DISTINCT cf.PMID)    AS pub_count,
       SUM(cf.CF_OBESITY)         AS cf_obesity,
       SUM(cf.CF_GLP_1_DRUG)      AS cf_glp1_drug,
       -- [one SUM per PCA term, built dynamically]
FROM {schema_v1}.PUBMED_CONTENT_FRAME_SINGLE cf
INNER JOIN ({q2_subquery}) AS relevant ON cf.PMID = relevant.PMID
GROUP BY cf.S_CUSTOMER_ID, cf.YEAR_VAL
```

#### Q4 — Digital scores

```sql
SELECT S_CUSTOMER_ID, DIGI_SCORE, OTM, VS, SOCIAL, SENTIMENT_RATING
FROM {schema_v1}.DIGISCORE_RESULT
```

#### Q5 — HCP metadata and quality rating

```sql
SELECT cs.S_CUSTOMER_ID, cs.S_FIRSTNAME, cs.S_LASTNAME,
       cs.S_CITY, cs.S_HCP_GROUP, r.RATING
FROM {schema_tmp}.CUSTOMER_SOURCE cs
JOIN {schema_final}.RATING_RESULT_FINAL r
    ON cs.S_CUSTOMER_ID = r.S_CUSTOMER_ID
WHERE r.RATING IN ('A','B','C','D')
```

---

### 4.2 `02_score_and_tier.py`

**Reads:** `data/kol_raw.json`
**Writes:** `data/kol_scored.json`

#### Normalisation
All four score components are min-max normalised to [0, 1] within the current dataset before weighting. This prevents any one component dominating due to natural range differences (e.g. DIGI_SCORE range −20→50 vs pub_count which could be 0–200).

```python
def minmax(values):
    lo, hi = min(values), max(values)
    if hi == lo:
        return [0.0] * len(values)
    return [(v - lo) / (hi - lo) for v in values]
```

#### Composite score formula

```
composite = a × norm_pub_count
          + b × norm_cf_count
          + c × norm_digi_score
          + d × norm_sim_score
```

Default weights (configurable in `config.ini [scoring]`):

| Weight | Component | Rationale |
|---|---|---|
| `a = 0.40` | `norm_pub_count` | Core KOL signal — topic-relevant publications |
| `b = 0.25` | `norm_cf_count` | Breadth of topic coverage across PCA terms |
| `c = 0.20` | `norm_digi_score` | Digital reach (DIGISCORE_RESULT) |
| `d = 0.15` | `norm_sim_score` | Semantic relevance from vector search |

Weights must sum to 1.0. Validated at startup.

#### Tier assignment (percentile-based)

Percentile thresholds are used rather than fixed score cutoffs — this ensures tiers adapt to the actual distribution in each run.

- **Tier A:** composite_score ≥ 85th percentile
- **Tier B:** composite_score ≥ 60th percentile
- **Tier C:** remaining

#### Rising Star detection (either criterion qualifies)

1. `pub_count_last_2yr >= 3` AND `pub_count_pre_2yr == 0` — newly appeared, already prolific
2. `pub_count_last_2yr / max(pub_count_pre_2yr, 1) >= 3.0` — 300%+ publication growth

Year boundary for "last 2 years" is computed at runtime from the current date.

#### Theme labels
Top-3 PCA terms by raw CF tag sum per HCP → displayed as tags in the report.

#### Q6 — Co-authorship edges (run here, after top-50 is known)
Two HCPs sharing a PMID in the relevant set are co-authors of that article. At least one side of the pair must be in the top-50 — the other side can be any HCP in the database. This reveals who the top KOLs collaborate with, including collaborators who did not rank in the top-50 themselves.

```sql
SELECT a.S_CUSTOMER_ID AS hcp_a,
       b.S_CUSTOMER_ID AS hcp_b,
       COUNT(DISTINCT a.PMID) AS shared_pmids
FROM {schema_v1}.PUBMED_CONTENT_FRAME_SINGLE a
JOIN {schema_v1}.PUBMED_CONTENT_FRAME_SINGLE b
    ON a.PMID = b.PMID
    AND a.S_CUSTOMER_ID < b.S_CUSTOMER_ID   -- dedup: each pair appears once
INNER JOIN ({q2_subquery}) AS relevant ON a.PMID = relevant.PMID
WHERE (   a.S_CUSTOMER_ID IN ({top_50_ids})
       OR b.S_CUSTOMER_ID IN ({top_50_ids}) )
GROUP BY a.S_CUSTOMER_ID, b.S_CUSTOMER_ID
HAVING COUNT(DISTINCT a.PMID) >= 2
```

The `{q2_subquery}` PMID set is passed in from the raw JSON (written by step 1) — step 2 does not re-query Snowflake for Q1/Q2, only for this one co-authorship edge query.

---

### 4.3 `03_generate_report.py`

**Reads:** `data/kol_scored.json`
**Writes:** `results/kol_report_{timestamp}.html`

Self-contained HTML output: no external fonts, no CDN libraries, no API calls at render time. All charts are inline SVG.

#### Report sections

| # | Section | Key visual |
|---|---|---|
| 1 | Executive Dashboard | Stat cards: total KOLs, tier counts, rising stars, total publications |
| 2 | KOL Ranking Table | Tier badge, composite score bar, pub count, digi score, theme tags |
| 3 | Rising Stars Spotlight | Publication sparkline + growth metric per flagged HCP |
| 4 | Publication Activity Timeline | SVG bar chart: publications/year, top-5 KOLs overlaid |
| 5 | Thematic Distribution | Heatmap grid: top-20 KOLs × PCA themes, colour = CF tag count |
| 6 | Regional Distribution | Table by city: KOL count, tier breakdown, top specialty |
| 7 | Co-authorship Network Preview | Inline SVG node-link diagram (top-30) + Neo4j teaser note |
| 8 | Individual KOL Profiles | Top-20 expandable cards: score breakdown, sparkline, pub snippets |

---

## 5. Configuration (`config.ini`)

```ini
[snowflake]
aws_profile          = exaris-prod
warehouse            = COMPUTE_WH
database             = EXARIS_DB
schema_v1            = ADIPOS_AMBU_V1
schema_final         = ADIPOS_AMBU_FINAL
schema_tmp           = ADIPOS_AMBU_TMP

[scoring]
weight_pub_count     = 0.40
weight_cf_count      = 0.25
weight_digi_score    = 0.20
weight_sim_score     = 0.15
tier_a_percentile    = 85
tier_b_percentile    = 60
rising_star_min_pubs = 3
rising_star_growth   = 3.0
data_window_years    = 3

[report]
top_n_profiles       = 20
top_n_heatmap        = 20
top_n_coauth         = 50
```

---

## 6. Open Questions

- [ ] Confirm schema names for V1, FINAL, TMP (placeholders used above — adjust in config.ini)
- [ ] Confirm source of `norm_sim_score` — is this a separate vector search pass in 01_fetch, or reused from the existing 02b comp-HCP pipeline output?
- [ ] Rising Star year boundary — confirm "last 2 years" aligns with available YEAR_VAL range in PUBMED_CONTENT_FRAME_SINGLE
- [ ] Neo4j co-authorship network — for the report preview, use static synthetic example or live top-30 data?
