# KOL Identification v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild Service 2.1 so a KOL's score is the count of LLM-verified genuinely-relevant sources (web + PubMed), with a cheap SQL funnel that sends only the top-75 candidates to the LLM.

**Architecture:** Five resume-safe stages, each writing a JSON checkpoint. Stage 01 counts candidate sources per HCP by cheap SQL (`LLM_VALIDATION` gate + PubMed authorship/CF signal) and shortlists the top ~75. Stage 02 fetches full text for the shortlist only. Stage 03 runs the reused Bedrock ingest→ground→verify engine over web + PubMed and keeps a source only if it yields ≥1 grounded, verified claim. Stage 04 computes the final score, tiers, rising stars, themes, and the collaboration network. Stage 05 renders an HTML report (top 25) + Excel.

**Tech Stack:** Python 3.10+, `snowflake-connector-python`, `boto3` (Bedrock + Secrets Manager), `cryptography`, `openpyxl` (Excel). No embeddings, no reranker. All already present in the repo except `openpyxl` (add to the service's deps).

## Global Constraints

- Snowflake credentials come from AWS Secrets Manager via `shared.parameter_manager` + `shared.secret_reader` — never hardcoded.
- All schema/table names read from `config.ini` — no schema strings in source. Cross-database references (`CUST_NOVO`, `CUST_TC`, `CORE`) use fully-qualified names in one connection.
- Every stage is resume-safe: skip if its output JSON exists unless `--force`.
- Each stage adds the repo root to `sys.path`: `sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))`.
- Tests mock all external boundaries (Snowflake, Bedrock) — no network/AWS needed. Load numeric-prefixed scripts with `importlib.util.spec_from_file_location` (existing repo pattern).
- Report HTML is fully self-contained: no CDN, no external fonts, no network at render time; all charts inline SVG.
- LLM: AWS Bedrock region `eu-central-1`; ingest `eu.amazon.nova-pro-v1:0`, verify `qwen.qwen3-235b-a22b-2507-v1:0`; temperature `0.0`.
- Web freshness filter is `IS_OLD = 0` only (no date window). The 5-year window applies to PubMed only.
- Specialty (`S_HCP_GROUP`) is displayed, never used to filter.
- Python files at `b_kol_identification/`; tests at `b_kol_identification/tests/`.
- Spec: `docs/superpowers/specs/2026-07-09-kol-identification-v2-design.md`.

## Schema constants (config-driven; confirm on first live run — spec §9)

These are the best-known names from the v1 pipeline, Service A, and the user's table notes. They live in `config.ini` so a wrong guess is a one-line fix, not a code change.

| Purpose | Value |
|---|---|
| Web gate + content | `CUST_NOVO.ADIPOS_AMBU_FINAL.LLM_VALIDATION` — cols `NEAR_BY, IS_OLD, IS_DOCTOR, IN_RELATION, COL_KEYWORDS_ORIG, COL_KEYWORDS_EN, CONTENT, WEBSITE_ID, S_CUSTOMER_ID, URL` |
| PubMed authorship | `CUST_TC.ADIPOS_AMBU_TMP.PUBMED_ARTICLE_MAPPING` — cols `PMID, S_CUSTOMER_ID, MERGE_RESULT` |
| PubMed CF flags | `CUST_TC.ADIPOS_AMBU_TMP.PUBMED_CONTENT_FRAME_SINGLE_TBL` — cols `PMID, YEAR`, one integer col per CF term key |
| PubMed article text | `CORE.PUBMED.ARTICLE` — cols `PMID, TITLE, ABSTRACT, YEAR_VAL, JOURNAL_NAME` |
| PubMed authors | `CORE.PUBMED.AUTHOR` — cols `PMID, ORCID, FIRSTNAME, LASTNAME, AFFILIATION` |
| CF term spec | `CUST_NOVO.ADIPOS_AMBU_V1.CONTENT_FRAME_SPEC` — cols `COL_MAP, EN_TERM_1, PCA` |
| HCP master + rating | `CUST_NOVO.ADIPOS_AMBU_V1.CUSTOMER_SOURCE` + `CUST_NOVO.ADIPOS_AMBU_FINAL.RATING_RESULT_FINAL` |

---

## Data Contracts

**`data/shortlist.json`** (Stage 01)
```json
{
  "indication": "Obesity", "client_drug": "Ozempic",
  "generated_at": "2026-07-09T10:00:00",
  "pca_terms": [{"term_key": "CF_OBESITY", "term_en": "Obesity"}],
  "hcps": [
    {"s_customer_id":"12345","name":"Max Mustermann","firstname":"Max","lastname":"Mustermann",
     "city":"Berlin","specialty":"Innere Medizin","rating":"A",
     "web_candidate_count": 8, "web_website_ids": ["w1","w2"],
     "pubmed_candidate_count": 5, "pubmed_cf_treffer": 12,
     "pubmed_articles": [{"pmid":"39000001","year":2024}],
     "pub_by_year": {"2022": 2, "2023": 3},
     "candidate_score": 13, "shortlisted": true}
  ]
}
```

**`data/sources.json`** (Stage 02) — shortlisted HCPs only
```json
{"indication":"Obesity","client_drug":"Ozempic","generated_at":"...","pca_terms":[...],
 "hcps":[
   {"s_customer_id":"12345","name":"Max Mustermann","city":"Berlin","specialty":"...","rating":"A",
    "pub_by_year": {"2023": 3},
    "web_sources":[{"source_id":"w1","kind":"web","url":"http://...","full_text":"..."}],
    "pubmed_sources":[{"source_id":"39000001","kind":"pubmed","pmid":"39000001","year":2024,
                       "url":"https://pubmed.ncbi.nlm.nih.gov/39000001/","full_text":"TITLE\n\nABSTRACT"}]}
 ]}
```

**`data/wiki.json`** (Stage 03)
```json
{"indication":"Obesity","client_drug":"Ozempic","generated_at":"...","pca_terms":[...],
 "hcps":[
   {"s_customer_id":"12345","name":"Max Mustermann","city":"Berlin","specialty":"...","rating":"A",
    "pub_by_year": {"2023": 3},
    "verified_web_count": 3, "verified_pubmed_count": 4,
    "verified_pubmed_years": {"2023": 2, "2024": 2},
    "claims":[{"source_id":"w1","kind":"web","verbatim_quote":"...","statement":"...",
               "sentiment":"positive","themes":["CF_OBESITY"],"mentioned_hcps":["Anna Berg"],
               "url":"http://...","verified":true}],
    "verified_pmids": ["39000001","39000002"]}
 ]}
```

**`data/kol_final.json`** (Stage 04)
```json
{"indication":"Obesity","client_drug":"Ozempic","generated_at":"...","pca_terms":[...],
 "hcps":[
   {"s_customer_id":"12345","name":"...","city":"...","specialty":"...","rating":"A",
    "verified_web_count":3,"verified_pubmed_count":4,"kol_score":7,
    "latest_year":2024,"tier":"A","rising_star":false,
    "theme_labels":[{"term_key":"CF_OBESITY","term_en":"Obesity","count":5}],
    "pub_by_year":{"2023":2,"2024":2},
    "top_quotes":[{"quote":"...","url":"...","sentiment":"positive"}]}
 ],
 "coauthor_edges":[{"hcp_a":"12345","hcp_b":"67890","shared_pmids":3,"a_name":"...","b_name":"...","b_external":false}],
 "comention_edges":[{"from":"12345","to":"67890","from_name":"...","to_name":"...","count":2}]}
```

---

## File Map

| File | Responsibility |
|---|---|
| `b_kol_identification/config.ini` | All runtime params — table names, funnel/scoring/report knobs, Bedrock models |
| `b_kol_identification/data/input.json` | Run input: indication + client drug |
| `b_kol_identification/pipeline_common.py` | Reused helpers: Snowflake connect, Bedrock JSON call, JSON parsing, name-match |
| `b_kol_identification/01_fetch_and_shortlist.py` | Cheap SQL candidate counts → top-75 shortlist |
| `b_kol_identification/02_retrieve_sources.py` | Full-text fetch for shortlisted HCPs (web + PubMed) |
| `b_kol_identification/03_wiki_build.py` | Bedrock ingest→ground→verify→map; per-source relevance |
| `b_kol_identification/04_assemble_kols.py` | Final score, tiers, rising stars, themes, network |
| `b_kol_identification/05_generate_report.py` | HTML report (top 25) + Excel |
| `b_kol_identification/tests/*` | Pytest units (mock Snowflake/Bedrock) |

**Archived v1 files** (`01_fetch_kol_data.py`, `02_score_and_tier.py`, `03_generate_report.py`) are reused as *source material* for renderers and scoring helpers, then deleted in the final task. Do not run them.

---

## Task 1: Scaffold + `pipeline_common.py`

**Files:**
- Create: `b_kol_identification/config.ini`
- Create: `b_kol_identification/data/input.json`, `b_kol_identification/data/.gitkeep`, `b_kol_identification/results/.gitkeep`
- Create: `b_kol_identification/pipeline_common.py`
- Create: `b_kol_identification/tests/__init__.py`
- Test: `b_kol_identification/tests/test_pipeline_common.py`

**Interfaces:**
- Produces: `connect_snowflake(aws_profile, warehouse, database) -> conn`; `make_bedrock_client(profile) -> client`; `call_bedrock_json(bedrock, model_id, prompt, temperature=0.0, max_tokens=4096) -> dict`; `strip_json_fences(s) -> str`; `parse_json_object(s) -> dict`; `name_matches(full_name, first, last) -> bool`.

- [ ] **Step 1: Create directories and input template**

```bash
mkdir -p b_kol_identification/data b_kol_identification/results b_kol_identification/tests
touch b_kol_identification/data/.gitkeep b_kol_identification/results/.gitkeep b_kol_identification/tests/__init__.py
printf '{\n  "indication": "Obesity",\n  "client_drug": "Ozempic"\n}\n' > b_kol_identification/data/input.json
```

- [ ] **Step 2: Write `config.ini`**

```ini
[snowflake]
aws_profile  = AdministratorAccess-311524101909
warehouse    = COMPUTE_WH
database     = CUST_NOVO
schema_v1    = ADIPOS_AMBU_V1
schema_final = ADIPOS_AMBU_FINAL

[tables]
llm_validation      = CUST_NOVO.ADIPOS_AMBU_FINAL.LLM_VALIDATION
content_frame_spec  = CUST_NOVO.ADIPOS_AMBU_V1.CONTENT_FRAME_SPEC
customer_source     = CUST_NOVO.ADIPOS_AMBU_V1.CUSTOMER_SOURCE
rating_result_final = CUST_NOVO.ADIPOS_AMBU_FINAL.RATING_RESULT_FINAL
pubmed_mapping      = CUST_TC.ADIPOS_AMBU_TMP.PUBMED_ARTICLE_MAPPING
pubmed_cf_flag      = CUST_TC.ADIPOS_AMBU_TMP.PUBMED_CONTENT_FRAME_SINGLE_TBL
pubmed_article      = CORE.PUBMED.ARTICLE
pubmed_author       = CORE.PUBMED.AUTHOR

[terms]
use_pca_only = true

[funnel]
in_relation_min       = 29
pubmed_window_years   = 5
top_n_candidates      = 75
max_sources_per_hcp   = 40
max_source_chars      = 24000

[bedrock]
aws_profile           = AdministratorAccess-311524101909
region                = eu-central-1
ingest_model_id       = eu.amazon.nova-pro-v1:0
verify_model_id       = qwen.qwen3-235b-a22b-2507-v1:0
ingest_max_workers    = 5
verify_max_workers    = 5
extraction_max_tokens = 4096

[scoring]
tier_a_percentile    = 85
tier_b_percentile    = 60
rising_star_min_pubs = 3
rising_star_growth   = 3.0

[report]
top_n_report = 25
```

- [ ] **Step 3: Create `pipeline_common.py` by copying from Service A**

Copy `a_comp_hcp_communication/pipeline_common.py` to `b_kol_identification/pipeline_common.py` **verbatim**, keeping exactly these functions: `make_bedrock_client`, `call_bedrock_json`, `strip_json_fences`, `parse_json_object`, `name_matches`. Then append the `connect_snowflake` function copied verbatim from the v1 `b_kol_identification/01_fetch_kol_data.py` (lines under `# ── Snowflake connection ──`). Remove any competitor/COI-specific helpers not listed here.

- [ ] **Step 4: Write tests for the pure helpers**

```python
# b_kol_identification/tests/test_pipeline_common.py
import importlib.util, os
_S = os.path.join(os.path.dirname(__file__), "..", "pipeline_common.py")
_spec = importlib.util.spec_from_file_location("pc", _S)
pc = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(pc)

def test_strip_json_fences_removes_backticks():
    assert pc.strip_json_fences('```json\n{"a":1}\n```') .strip() == '{"a":1}'

def test_parse_json_object_parses_fenced():
    assert pc.parse_json_object('```json\n{"a":1}\n```') == {"a": 1}

def test_name_matches_last_and_first_initial():
    assert pc.name_matches("Prof. Anna Berg", "Anna", "Berg") is True
    assert pc.name_matches("Karl Neu", "Anna", "Berg") is False
```

- [ ] **Step 5: Run tests**

Run: `cd b_kol_identification && python -m pytest tests/test_pipeline_common.py -v`
Expected: 3 PASS. (If `name_matches` signature differs in Service A, adjust the test call to match the copied signature.)

- [ ] **Step 6: Commit**

```bash
git add b_kol_identification/config.ini b_kol_identification/data b_kol_identification/results \
        b_kol_identification/pipeline_common.py b_kol_identification/tests
git commit -m "feat(kol): scaffold config, input, and reused pipeline_common"
```

---

## Task 2: Stage 01 — query builders + row normalisers

**Files:**
- Create: `b_kol_identification/01_fetch_and_shortlist.py` (pure helpers only — no `main()` yet)
- Test: `b_kol_identification/tests/test_01_fetch.py`

**Interfaces:**
- Produces:
  - `build_pca_terms_query(content_frame_spec: str, use_pca_only: bool) -> str`
  - `build_web_candidates_query(llm_validation: str, term_predicate: str, in_relation_min: int) -> str`
  - `build_pubmed_candidates_query(pubmed_mapping: str, pubmed_cf_flag: str, cf_cols: list[str], window_years: int, current_year: int) -> str`
  - `build_hcp_meta_query(customer_source: str, rating_result_final: str) -> str`
  - `term_ilike_predicate(term_texts: list[str]) -> str`
  - `matches_keywords(keyword_blob: str, term_texts: list[str]) -> bool`
  - `normalise_meta_row(row: dict) -> dict`

- [ ] **Step 1: Write the failing tests**

```python
# b_kol_identification/tests/test_01_fetch.py
import importlib.util, os
_S = os.path.join(os.path.dirname(__file__), "..", "01_fetch_and_shortlist.py")
_spec = importlib.util.spec_from_file_location("fetch", _S)
mod = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(mod)

def test_pca_terms_query_filters_on_pca_when_true():
    sql = mod.build_pca_terms_query("DB.V1.CONTENT_FRAME_SPEC", True)
    assert "DB.V1.CONTENT_FRAME_SPEC" in sql
    assert "PCA" in sql.upper() and "'X'" in sql.upper()

def test_pca_terms_query_no_pca_filter_when_false():
    sql = mod.build_pca_terms_query("DB.V1.CONTENT_FRAME_SPEC", False)
    assert "PCA" not in sql.upper().split("WHERE")[-1] if "WHERE" in sql.upper() else True

def test_term_ilike_predicate_ors_each_term_on_both_cols():
    pred = mod.term_ilike_predicate(["obesity", "glp-1"])
    assert "COL_KEYWORDS_ORIG ILIKE '%obesity%'" in pred
    assert "COL_KEYWORDS_EN ILIKE '%glp-1%'" in pred
    assert " OR " in pred

def test_web_candidates_query_has_gate_and_in_relation():
    sql = mod.build_web_candidates_query("DB.F.LLM_VALIDATION", "x ILIKE '%a%'", 29)
    assert "DB.F.LLM_VALIDATION" in sql
    assert "NEAR_BY = 1" in sql and "IS_OLD = 0" in sql and "IS_DOCTOR = 1" in sql
    assert "IN_RELATION > 29" in sql
    assert "S_CUSTOMER_ID" in sql and "WEBSITE_ID" in sql

def test_pubmed_candidates_query_verified_author_and_window():
    sql = mod.build_pubmed_candidates_query(
        "DB.T.PUBMED_ARTICLE_MAPPING", "DB.T.PUBMED_CF", ["CF_OBESITY","CF_GLP1"], 5, 2026)
    assert "MERGE_RESULT > 1" in sql
    assert "DB.T.PUBMED_CF" in sql
    assert "2021" in sql            # current_year - window
    assert "CF_OBESITY" in sql and "CF_GLP1" in sql

def test_hcp_meta_query_joins_and_filters_rating():
    sql = mod.build_hcp_meta_query("DB.V1.CUSTOMER_SOURCE", "DB.F.RATING_RESULT_FINAL")
    assert "DB.V1.CUSTOMER_SOURCE" in sql and "DB.F.RATING_RESULT_FINAL" in sql
    assert "IN ('A','B','C','D')" in sql or "IN ('A', 'B', 'C', 'D')" in sql

def test_matches_keywords_whole_token_only():
    assert mod.matches_keywords("obesity therapy", ["obesity"]) is True
    assert mod.matches_keywords("(SELECT)", ["ele"]) is False

def test_normalise_meta_row_builds_name():
    row = {"S_CUSTOMER_ID":"9","S_FIRSTNAME":"Anna","S_LASTNAME":"Berg",
           "S_CITY":"Berlin","S_HCP_GROUP":"Innere Medizin","RATING":"A"}
    r = mod.normalise_meta_row(row)
    assert r["name"] == "Anna Berg" and r["specialty"] == "Innere Medizin" and r["rating"] == "A"
```

- [ ] **Step 2: Run to verify failure**

Run: `cd b_kol_identification && python -m pytest tests/test_01_fetch.py -v 2>&1 | head`
Expected: import/attribute errors (file/functions not defined).

- [ ] **Step 3: Implement the helpers**

```python
"""
Stage 01: Fetch candidate counts and shortlist the top-N KOL candidates.
Reads:  data/input.json
Writes: data/shortlist.json  (resume-safe — skips if exists, unless --force)
"""
import configparser, json, logging, os, re, sys
from datetime import datetime

logging.basicConfig(format="%(asctime)s [%(levelname)s] %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)
_DIR = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(_DIR, ".."))


def build_pca_terms_query(content_frame_spec: str, use_pca_only: bool) -> str:
    base = f"SELECT COL_MAP AS TERM_KEY, EN_TERM_1 AS TERM_EN FROM {content_frame_spec}"
    return base + " WHERE UPPER(PCA) = 'X'" if use_pca_only else base


def term_ilike_predicate(term_texts: list) -> str:
    parts = []
    for t in term_texts:
        safe = t.replace("'", "''")
        parts.append(f"COL_KEYWORDS_ORIG ILIKE '%{safe}%' OR COL_KEYWORDS_EN ILIKE '%{safe}%'")
    return " OR ".join(parts) if parts else "TRUE"


def build_web_candidates_query(llm_validation: str, term_predicate: str, in_relation_min: int) -> str:
    return f"""
SELECT lv.S_CUSTOMER_ID, lv.WEBSITE_ID,
       lv.COL_KEYWORDS_ORIG, lv.COL_KEYWORDS_EN
FROM {llm_validation} lv
WHERE lv.NEAR_BY = 1 AND lv.IS_OLD = 0 AND lv.IS_DOCTOR = 1
  AND lv.IN_RELATION > {in_relation_min}
  AND ({term_predicate})
""".strip()


def build_pubmed_candidates_query(pubmed_mapping: str, pubmed_cf_flag: str,
                                  cf_cols: list, window_years: int, current_year: int) -> str:
    cutoff = current_year - window_years
    cf_sum = " + ".join(f"COALESCE(cf.{c}, 0)" for c in cf_cols) or "0"
    cf_any = " OR ".join(f"cf.{c} > 0" for c in cf_cols) or "FALSE"
    return f"""
SELECT m.S_CUSTOMER_ID, m.PMID, cf.YEAR AS YEAR_VAL,
       ({cf_sum}) AS CF_TREFFER
FROM {pubmed_mapping} m
JOIN {pubmed_cf_flag} cf ON cf.PMID = m.PMID
WHERE m.MERGE_RESULT > 1
  AND ({cf_any})
  AND cf.YEAR >= {cutoff}
""".strip()


def build_hcp_meta_query(customer_source: str, rating_result_final: str) -> str:
    return f"""
SELECT cs.S_CUSTOMER_ID, cs.S_FIRSTNAME, cs.S_LASTNAME,
       cs.S_CITY, cs.S_HCP_GROUP, r.RATING
FROM {customer_source} cs
JOIN {rating_result_final} r ON cs.S_CUSTOMER_ID = r.S_CUSTOMER_ID
WHERE r.RATING IN ('A','B','C','D')
""".strip()


def matches_keywords(keyword_blob: str, term_texts: list) -> bool:
    blob = (keyword_blob or "").lower()
    tokens = set(re.findall(r"[a-z0-9\-]+", blob))
    for t in term_texts:
        t = t.lower().strip()
        if not t:
            continue
        if " " in t or "-" in t:
            if t in blob:
                return True
        elif t in tokens:
            return True
    return False


def normalise_meta_row(row: dict) -> dict:
    def _g(k):
        v = row.get(k)
        return v if v is not None else row.get(k.lower())
    first = str(_g("S_FIRSTNAME") or "").strip()
    last  = str(_g("S_LASTNAME") or "").strip()
    return {
        "s_customer_id": str(_g("S_CUSTOMER_ID") or ""),
        "firstname": first, "lastname": last,
        "name": " ".join(p for p in [first, last] if p),
        "city": str(_g("S_CITY") or ""),
        "specialty": str(_g("S_HCP_GROUP") or ""),
        "rating": str(_g("RATING") or ""),
    }
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_01_fetch.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add b_kol_identification/01_fetch_and_shortlist.py b_kol_identification/tests/test_01_fetch.py
git commit -m "feat(kol): stage 01 query builders and normalisers"
```

---

## Task 3: Stage 01 — shortlist aggregation + `main()`

**Files:**
- Modify: `b_kol_identification/01_fetch_and_shortlist.py` (add aggregation + `main`)
- Modify: `b_kol_identification/tests/test_01_fetch.py` (add aggregation tests)

**Interfaces:**
- Consumes: query builders (Task 2); `connect_snowflake` (Task 1)
- Produces: `aggregate_candidates(web_rows, pubmed_rows, meta_map, term_texts) -> dict`; `shortlist(hcps: list[dict], top_n: int) -> list[dict]` (sorts by `candidate_score` desc, tie-break `pubmed_cf_treffer` then `rating`, sets `shortlisted` on the first `top_n`).

- [ ] **Step 1: Add aggregation tests**

```python
# append to tests/test_01_fetch.py
def test_aggregate_counts_web_and_pubmed_per_hcp():
    web = [{"S_CUSTOMER_ID":"10","WEBSITE_ID":"w1","COL_KEYWORDS_ORIG":"obesity","COL_KEYWORDS_EN":"obesity"},
           {"S_CUSTOMER_ID":"10","WEBSITE_ID":"w2","COL_KEYWORDS_ORIG":"glp-1","COL_KEYWORDS_EN":"glp-1"}]
    pub = [{"S_CUSTOMER_ID":"10","PMID":"p1","YEAR_VAL":2024,"CF_TREFFER":3}]
    meta = {"10": {"s_customer_id":"10","name":"A B","firstname":"A","lastname":"B",
                   "city":"X","specialty":"Y","rating":"A"}}
    out = mod.aggregate_candidates(web, pub, meta, ["obesity","glp-1"])
    h = out["10"]
    assert h["web_candidate_count"] == 2
    assert h["pubmed_candidate_count"] == 1
    assert h["pubmed_cf_treffer"] == 3
    assert h["candidate_score"] == 3   # 2 web + 1 pubmed
    assert h["pub_by_year"] == {"2024": 1}

def test_aggregate_drops_web_row_failing_token_match():
    web = [{"S_CUSTOMER_ID":"10","WEBSITE_ID":"w1","COL_KEYWORDS_ORIG":"cardiology","COL_KEYWORDS_EN":"cardiology"}]
    meta = {"10":{"s_customer_id":"10","name":"A B","firstname":"A","lastname":"B","city":"X","specialty":"Y","rating":"A"}}
    out = mod.aggregate_candidates(web, [], meta, ["obesity"])
    assert "10" not in out   # no candidate sources at all

def test_aggregate_excludes_hcp_without_meta():
    web = [{"S_CUSTOMER_ID":"99","WEBSITE_ID":"w1","COL_KEYWORDS_ORIG":"obesity","COL_KEYWORDS_EN":"obesity"}]
    out = mod.aggregate_candidates(web, [], {}, ["obesity"])
    assert out == {}

def test_shortlist_flags_top_n_by_score():
    hcps = [{"s_customer_id":str(i),"candidate_score":i,"pubmed_cf_treffer":0,"rating":"C"} for i in range(5)]
    out = mod.shortlist(hcps, top_n=2)
    flagged = [h for h in out if h["shortlisted"]]
    assert len(flagged) == 2
    assert {h["s_customer_id"] for h in flagged} == {"4","3"}
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_01_fetch.py::test_aggregate_counts_web_and_pubmed_per_hcp -v`
Expected: `AttributeError: ... 'aggregate_candidates'`.

- [ ] **Step 3: Implement aggregation, shortlist, and `main()`**

```python
# append to 01_fetch_and_shortlist.py

def aggregate_candidates(web_rows: list, pubmed_rows: list, meta_map: dict, term_texts: list) -> dict:
    def _g(row, k):
        v = row.get(k)
        return v if v is not None else row.get(k.lower())
    acc = {}
    for row in web_rows:
        cid = str(_g(row, "S_CUSTOMER_ID") or "")
        blob = f"{_g(row,'COL_KEYWORDS_ORIG') or ''} {_g(row,'COL_KEYWORDS_EN') or ''}"
        if not matches_keywords(blob, term_texts):
            continue
        h = acc.setdefault(cid, {"web_website_ids": [], "pubmed_articles": [], "pub_by_year": {}})
        h["web_website_ids"].append(str(_g(row, "WEBSITE_ID") or ""))
    for row in pubmed_rows:
        cid = str(_g(row, "S_CUSTOMER_ID") or "")
        h = acc.setdefault(cid, {"web_website_ids": [], "pubmed_articles": [], "pub_by_year": {}})
        yr = str(_g(row, "YEAR_VAL") or "")
        h["pubmed_articles"].append({"pmid": str(_g(row, "PMID") or ""), "year": yr})
        h["pubmed_cf_treffer"] = h.get("pubmed_cf_treffer", 0) + int(_g(row, "CF_TREFFER") or 0)
        if yr:
            h["pub_by_year"][yr] = h["pub_by_year"].get(yr, 0) + 1

    result = {}
    for cid, h in acc.items():
        if cid not in meta_map:
            continue
        web_n = len(h["web_website_ids"])
        pub_n = len(h["pubmed_articles"])
        if web_n == 0 and pub_n == 0:
            continue
        result[cid] = {
            **meta_map[cid],
            "web_candidate_count": web_n, "web_website_ids": h["web_website_ids"],
            "pubmed_candidate_count": pub_n, "pubmed_articles": h["pubmed_articles"],
            "pubmed_cf_treffer": h.get("pubmed_cf_treffer", 0),
            "pub_by_year": h["pub_by_year"],
            "candidate_score": web_n + pub_n,
        }
    return result


_RATING_RANK = {"A": 3, "B": 2, "C": 1, "D": 0, "": -1}

def shortlist(hcps: list, top_n: int) -> list:
    ordered = sorted(
        hcps,
        key=lambda h: (h.get("candidate_score", 0), h.get("pubmed_cf_treffer", 0),
                       _RATING_RANK.get(h.get("rating", ""), -1)),
        reverse=True,
    )
    for i, h in enumerate(ordered):
        h["shortlisted"] = i < top_n
    return ordered


def main():
    import argparse, snowflake.connector
    from pipeline_common import connect_snowflake
    p = argparse.ArgumentParser(); p.add_argument("--force", action="store_true")
    args = p.parse_args()

    cfg = configparser.ConfigParser(); cfg.read(os.path.join(_DIR, "config.ini"))
    sf, tb, fn, tm = cfg["snowflake"], cfg["tables"], cfg["funnel"], cfg["terms"]

    out_path = os.path.join(_DIR, "data", "shortlist.json")
    if os.path.exists(out_path) and not args.force:
        log.info("shortlist.json exists — skipping (use --force)"); return

    with open(os.path.join(_DIR, "data", "input.json"), encoding="utf-8") as f:
        inp = json.load(f)

    conn = connect_snowflake(sf["aws_profile"], sf["warehouse"], sf["database"])
    cur = conn.cursor(snowflake.connector.DictCursor)

    log.info("Q1: PCA terms...")
    cur.execute(build_pca_terms_query(tb["content_frame_spec"], tm.getboolean("use_pca_only")))
    pca_terms = [{"term_key": r["TERM_KEY"], "term_en": r["TERM_EN"]} for r in cur.fetchall()]
    term_texts = [t["term_en"] for t in pca_terms if t["term_en"]]
    cf_cols = [t["term_key"] for t in pca_terms]

    log.info("Q2: web candidates...")
    cur.execute(build_web_candidates_query(tb["llm_validation"],
                term_ilike_predicate(term_texts), int(fn["in_relation_min"])))
    web_rows = cur.fetchall()

    log.info("Q3: pubmed candidates...")
    cur.execute(build_pubmed_candidates_query(tb["pubmed_mapping"], tb["pubmed_cf_flag"],
                cf_cols, int(fn["pubmed_window_years"]), datetime.now().year))
    pubmed_rows = cur.fetchall()

    log.info("Q4: HCP metadata...")
    cur.execute(build_hcp_meta_query(tb["customer_source"], tb["rating_result_final"]))
    meta_map = {str(r["S_CUSTOMER_ID"]): normalise_meta_row(r) for r in cur.fetchall()}

    cur.close(); conn.close()

    hcps = list(aggregate_candidates(web_rows, pubmed_rows, meta_map, term_texts).values())
    hcps = shortlist(hcps, int(fn["top_n_candidates"]))
    n_short = sum(h["shortlisted"] for h in hcps)
    log.info(f"{len(hcps)} candidate HCPs; {n_short} shortlisted")
    for h in [x for x in hcps if x["shortlisted"]]:
        log.info(f"  {h['name']:<30} score={h['candidate_score']} "
                 f"(web={h['web_candidate_count']}, pubmed={h['pubmed_candidate_count']})")

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"indication": inp["indication"], "client_drug": inp["client_drug"],
                   "generated_at": datetime.now().isoformat(timespec="seconds"),
                   "pca_terms": pca_terms, "hcps": hcps}, f, ensure_ascii=False, indent=2)
    log.info(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_01_fetch.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add b_kol_identification/01_fetch_and_shortlist.py b_kol_identification/tests/test_01_fetch.py
git commit -m "feat(kol): stage 01 candidate aggregation, shortlist, and main()"
```

---

## Task 4: Stage 02 — full-text retrieval

**Files:**
- Create: `b_kol_identification/02_retrieve_sources.py`
- Test: `b_kol_identification/tests/test_02_retrieve.py`

**Interfaces:**
- Consumes: `shortlist.json`; `connect_snowflake`
- Produces:
  - `build_web_content_query(llm_validation: str, website_ids: list[str]) -> str`
  - `build_pubmed_article_query(pubmed_article: str, pmids: list[str]) -> str`
  - `assemble_web_sources(rows: list[dict], max_chars: int) -> list[dict]`  (keys: source_id, kind='web', url, full_text)
  - `assemble_pubmed_sources(rows: list[dict], max_chars: int) -> list[dict]`  (keys: source_id, kind='pubmed', pmid, year, url, full_text)
  - `cap_sources(sources: list[dict], max_n: int) -> list[dict]`  (keep newest by `year`, web first-N otherwise)

- [ ] **Step 1: Write failing tests**

```python
# b_kol_identification/tests/test_02_retrieve.py
import importlib.util, os
_S = os.path.join(os.path.dirname(__file__), "..", "02_retrieve_sources.py")
_spec = importlib.util.spec_from_file_location("retr", _S)
mod = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(mod)

def test_web_content_query_has_in_list():
    sql = mod.build_web_content_query("DB.F.LLM_VALIDATION", ["w1","w2"])
    assert "DB.F.LLM_VALIDATION" in sql
    assert "'w1'" in sql and "'w2'" in sql
    assert "CONTENT" in sql and "WEBSITE_ID" in sql

def test_pubmed_article_query_selects_title_abstract():
    sql = mod.build_pubmed_article_query("CORE.PUBMED.ARTICLE", ["39000001"])
    assert "CORE.PUBMED.ARTICLE" in sql
    assert "TITLE" in sql and "ABSTRACT" in sql and "'39000001'" in sql

def test_assemble_pubmed_joins_title_and_abstract_and_truncates():
    rows = [{"PMID":"39000001","TITLE":"T","ABSTRACT":"A"*100,"YEAR_VAL":2024}]
    out = mod.assemble_pubmed_sources(rows, max_chars=10)
    assert out[0]["kind"] == "pubmed" and out[0]["pmid"] == "39000001"
    assert out[0]["full_text"].startswith("T")
    assert len(out[0]["full_text"]) <= 10

def test_assemble_web_sets_source_id_from_website_id():
    rows = [{"WEBSITE_ID":"w1","URL":"http://x","CONTENT":"hello"}]
    out = mod.assemble_web_sources(rows, max_chars=1000)
    assert out[0]["source_id"] == "w1" and out[0]["kind"] == "web" and out[0]["full_text"] == "hello"

def test_cap_sources_keeps_newest():
    src = [{"source_id":str(y),"year":y} for y in [2018,2024,2020,2023]]
    out = mod.cap_sources(src, max_n=2)
    assert {s["source_id"] for s in out} == {"2024","2023"}
```

- [ ] **Step 2: Run to verify failure**

Run: `cd b_kol_identification && python -m pytest tests/test_02_retrieve.py -v 2>&1 | head`
Expected: import error.

- [ ] **Step 3: Implement**

```python
"""
Stage 02: Retrieve full source text for shortlisted HCPs (web + PubMed).
Reads:  data/shortlist.json
Writes: data/sources.json  (resume-safe)
"""
import configparser, json, logging, os, sys
from datetime import datetime

logging.basicConfig(format="%(asctime)s [%(levelname)s] %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)
_DIR = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(_DIR, ".."))


def _in_list(ids: list) -> str:
    return ", ".join("'" + str(i).replace("'", "''") + "'" for i in ids)


def build_web_content_query(llm_validation: str, website_ids: list) -> str:
    return (f"SELECT WEBSITE_ID, URL, CONTENT FROM {llm_validation} "
            f"WHERE WEBSITE_ID IN ({_in_list(website_ids)})")


def build_pubmed_article_query(pubmed_article: str, pmids: list) -> str:
    return (f"SELECT PMID, TITLE, ABSTRACT, YEAR_VAL, JOURNAL_NAME FROM {pubmed_article} "
            f"WHERE PMID IN ({_in_list(pmids)})")


def _g(row, k):
    v = row.get(k)
    return v if v is not None else row.get(k.lower())


def assemble_web_sources(rows: list, max_chars: int) -> list:
    out = []
    for r in rows:
        out.append({"source_id": str(_g(r, "WEBSITE_ID") or ""), "kind": "web",
                    "url": str(_g(r, "URL") or ""),
                    "full_text": str(_g(r, "CONTENT") or "")[:max_chars]})
    return out


def assemble_pubmed_sources(rows: list, max_chars: int) -> list:
    out = []
    for r in rows:
        pmid = str(_g(r, "PMID") or "")
        title = str(_g(r, "TITLE") or ""); abstract = str(_g(r, "ABSTRACT") or "")
        out.append({"source_id": pmid, "kind": "pubmed", "pmid": pmid,
                    "year": str(_g(r, "YEAR_VAL") or ""),
                    "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                    "full_text": (title + "\n\n" + abstract).strip()[:max_chars]})
    return out


def cap_sources(sources: list, max_n: int) -> list:
    def _year(s):
        try:
            return int(s.get("year") or 0)
        except (TypeError, ValueError):
            return 0
    return sorted(sources, key=_year, reverse=True)[:max_n]


def main():
    import argparse, snowflake.connector
    from pipeline_common import connect_snowflake
    p = argparse.ArgumentParser(); p.add_argument("--force", action="store_true")
    args = p.parse_args()
    cfg = configparser.ConfigParser(); cfg.read(os.path.join(_DIR, "config.ini"))
    sf, tb, fn = cfg["snowflake"], cfg["tables"], cfg["funnel"]
    max_chars = int(fn["max_source_chars"]); max_n = int(fn["max_sources_per_hcp"])

    out_path = os.path.join(_DIR, "data", "sources.json")
    if os.path.exists(out_path) and not args.force:
        log.info("sources.json exists — skipping (use --force)"); return

    with open(os.path.join(_DIR, "data", "shortlist.json"), encoding="utf-8") as f:
        sl = json.load(f)
    shortlisted = [h for h in sl["hcps"] if h.get("shortlisted")]
    log.info(f"Fetching sources for {len(shortlisted)} shortlisted HCPs")

    conn = connect_snowflake(sf["aws_profile"], sf["warehouse"], sf["database"])
    cur = conn.cursor(snowflake.connector.DictCursor)
    out_hcps = []
    for h in shortlisted:
        web_sources, pubmed_sources = [], []
        if h.get("web_website_ids"):
            cur.execute(build_web_content_query(tb["llm_validation"], h["web_website_ids"]))
            web_sources = assemble_web_sources(cur.fetchall(), max_chars)
        pmids = [a["pmid"] for a in h.get("pubmed_articles", [])]
        if pmids:
            cur.execute(build_pubmed_article_query(tb["pubmed_article"], pmids))
            pubmed_sources = assemble_pubmed_sources(cur.fetchall(), max_chars)
        out_hcps.append({
            "s_customer_id": h["s_customer_id"], "name": h["name"], "city": h["city"],
            "specialty": h["specialty"], "rating": h["rating"], "pub_by_year": h.get("pub_by_year", {}),
            "web_sources": cap_sources(web_sources, max_n),
            "pubmed_sources": cap_sources(pubmed_sources, max_n),
        })
    cur.close(); conn.close()

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"indication": sl["indication"], "client_drug": sl["client_drug"],
                   "generated_at": datetime.now().isoformat(timespec="seconds"),
                   "pca_terms": sl["pca_terms"], "hcps": out_hcps}, f, ensure_ascii=False, indent=2)
    log.info(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests** — `python -m pytest tests/test_02_retrieve.py -v` → all PASS.

- [ ] **Step 5: Commit**

```bash
git add b_kol_identification/02_retrieve_sources.py b_kol_identification/tests/test_02_retrieve.py
git commit -m "feat(kol): stage 02 full-text retrieval for shortlisted HCPs"
```

---

## Task 5: Stage 03 — LLM wiki-build (ingest → ground → verify → map)

**Files:**
- Create: `b_kol_identification/03_wiki_build.py`
- Test: `b_kol_identification/tests/test_03_wiki.py`

**Interfaces:**
- Consumes: `sources.json`; `pipeline_common` (`call_bedrock_json`, `make_bedrock_client`, `name_matches`)
- Produces:
  - `build_ingest_prompt(kind: str, indication: str, term_list: list[str], hcp_name: str, text: str) -> str`
  - `build_verify_prompt(indication: str, hcp_name: str, quote: str, text: str) -> str`
  - `quote_grounded(quote: str, text: str) -> bool`
  - `normalise_claim(raw: dict) -> dict`  (keys: verbatim_quote, statement, sentiment, themes[list], mentioned_hcps[list], confidence)
  - `resolve_mentions(names: list[str], roster: list[dict]) -> list[dict]`  ({name, s_customer_id})
  - `process_source(source, hcp, indication, term_list, bedrock, cfg) -> dict|None`  (returns kept claims + verified flag or None)
  - `source_is_relevant(claims: list[dict]) -> bool`  (≥1 verified claim)

- [ ] **Step 1: Write failing tests** (pure logic + a mocked end-to-end)

```python
# b_kol_identification/tests/test_03_wiki.py
import importlib.util, os
_S = os.path.join(os.path.dirname(__file__), "..", "03_wiki_build.py")
_spec = importlib.util.spec_from_file_location("wiki", _S)
mod = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(mod)

def test_quote_grounded_true_on_substring_ignoring_ws_case():
    assert mod.quote_grounded("the  PATIENT improved", "... The patient improved a lot ...") is True

def test_quote_grounded_false_when_absent():
    assert mod.quote_grounded("never said this", "some other text") is False

def test_ingest_prompt_web_demands_active_engagement_and_schema():
    p = mod.build_ingest_prompt("web", "Obesity", ["Obesity"], "Anna Berg", "text")
    assert "Obesity" in p and "Anna Berg" in p
    assert "verbatim_quote" in p and "themes" in p and "mentioned_hcps" in p

def test_ingest_prompt_pubmed_frames_article_relevance():
    p = mod.build_ingest_prompt("pubmed", "Obesity", ["Obesity"], "Anna Berg", "text")
    assert "article" in p.lower()

def test_normalise_claim_coerces_lists():
    c = mod.normalise_claim({"verbatim_quote":"q","statement":"s","sentiment":"positive",
                             "themes":"CF_OBESITY","mentioned_hcps":None,"confidence":"high"})
    assert c["themes"] == ["CF_OBESITY"] and c["mentioned_hcps"] == []

def test_resolve_mentions_matches_roster():
    roster = [{"s_customer_id":"77","firstname":"Anna","lastname":"Berg"}]
    out = mod.resolve_mentions(["Prof. Anna Berg","Nobody Here"], roster)
    assert {"name":"Prof. Anna Berg","s_customer_id":"77"} in out
    assert any(m["s_customer_id"] == "" for m in out)   # unmatched kept, no id

def test_source_is_relevant_needs_verified_claim():
    assert mod.source_is_relevant([{"verified":False}]) is False
    assert mod.source_is_relevant([{"verified":True}]) is True

def test_process_source_end_to_end_with_mocks():
    # ingest returns one claim; grounding passes; verify returns true
    class FakeBedrock: pass
    calls = {"n": 0}
    def fake_call(bedrock, model_id, prompt, temperature=0.0, max_tokens=4096):
        calls["n"] += 1
        if "verbatim_quote" in prompt:   # ingest
            return {"claims":[{"verbatim_quote":"patient improved","statement":"engaged",
                    "sentiment":"positive","themes":["CF_OBESITY"],"mentioned_hcps":[],"confidence":"high"}]}
        return {"verified": True}        # verify
    mod.call_bedrock_json = fake_call    # monkeypatch the imported name
    src = {"source_id":"w1","kind":"web","url":"u","full_text":"the patient improved after therapy"}
    hcp = {"name":"Anna Berg","s_customer_id":"10"}
    cfg = {"ingest_model_id":"m1","verify_model_id":"m2","extraction_max_tokens":4096}
    out = mod.process_source(src, hcp, "Obesity", ["Obesity"], FakeBedrock(), cfg)
    assert out is not None and out["claims"][0]["verified"] is True
    assert out["claims"][0]["source_id"] == "w1"
```

- [ ] **Step 2: Run to verify failure**

Run: `cd b_kol_identification && python -m pytest tests/test_03_wiki.py -v 2>&1 | head`
Expected: import error.

- [ ] **Step 3: Implement**

```python
"""
Stage 03: LLM wiki-build — ingest -> ground -> verify -> map, per source.
A source counts as relevant iff it yields >=1 grounded, verified claim.
Reads:  data/sources.json
Writes: data/wiki.json  (+ wiki/<ts>/ tree)  (resume-safe)
"""
import configparser, json, logging, os, re, sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

logging.basicConfig(format="%(asctime)s [%(levelname)s] %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)
_DIR = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(_DIR, ".."))
from pipeline_common import call_bedrock_json, make_bedrock_client, name_matches  # noqa: E402


def _norm_ws(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip().casefold()


def quote_grounded(quote: str, text: str) -> bool:
    q = _norm_ws(quote)
    return bool(q) and q in _norm_ws(text)


def build_ingest_prompt(kind: str, indication: str, term_list: list, hcp_name: str, text: str) -> str:
    terms = ", ".join(term_list)
    if kind == "pubmed":
        role = (f"This is a PubMed article authored by {hcp_name}. Decide whether the ARTICLE "
                f"genuinely concerns the indication '{indication}' (topics: {terms}) — i.e. the "
                f"author is actively contributing to this indication's science.")
    else:
        role = (f"Decide whether the named HCP {hcp_name} is ACTIVELY engaging with / sharing a view "
                f"on the indication '{indication}' (topics: {terms}) in this document — not merely "
                f"named on the page. Ignore financial-disclosure / conflict-of-interest text.")
    return f"""{role}

Return ONLY JSON:
{{"claims":[{{"verbatim_quote":"<exact span copied from the text>",
  "statement":"<one line: how the HCP engages with the indication>",
  "sentiment":"positive|neutral|negative|ambivalent",
  "themes":["<which of: {terms}>"],
  "mentioned_hcps":["<other doctor names in the text>"],
  "confidence":"high|medium|low"}}]}}
If there is no genuine engagement, return {{"claims":[]}}.

TEXT:
{text}
"""


def build_verify_prompt(indication: str, hcp_name: str, quote: str, text: str) -> str:
    return f"""A previous pass claims {hcp_name} genuinely engages with '{indication}',
supported by this quote:
"{quote}"

Is that TRUE given the source below? Answer ONLY {{"verified": true}} or {{"verified": false}}.
Be strict: false if the quote is absent, or does not show {hcp_name} engaging with '{indication}'.

SOURCE:
{text}
"""


def normalise_claim(raw: dict) -> dict:
    def _list(v):
        if v is None:
            return []
        return v if isinstance(v, list) else [v]
    return {
        "verbatim_quote": str(raw.get("verbatim_quote") or ""),
        "statement": str(raw.get("statement") or ""),
        "sentiment": str(raw.get("sentiment") or "neutral"),
        "themes": [str(t) for t in _list(raw.get("themes"))],
        "mentioned_hcps": [str(n) for n in _list(raw.get("mentioned_hcps"))],
        "confidence": str(raw.get("confidence") or "medium"),
    }


def resolve_mentions(names: list, roster: list) -> list:
    out = []
    for nm in names:
        sid = ""
        for r in roster:
            if name_matches(nm, r.get("firstname", ""), r.get("lastname", "")):
                sid = r.get("s_customer_id", ""); break
        out.append({"name": nm, "s_customer_id": sid})
    return out


def source_is_relevant(claims: list) -> bool:
    return any(c.get("verified") for c in claims)


def process_source(source, hcp, indication, term_list, bedrock, cfg):
    text = source["full_text"]
    ingest = call_bedrock_json(bedrock, cfg["ingest_model_id"],
                               build_ingest_prompt(source["kind"], indication, term_list, hcp["name"], text),
                               temperature=0.0, max_tokens=int(cfg["extraction_max_tokens"]))
    raw_claims = (ingest or {}).get("claims", []) or []
    kept = []
    for rc in raw_claims:
        c = normalise_claim(rc)
        if not quote_grounded(c["verbatim_quote"], text):
            continue  # dropped: grounding — before spending a verify call
        vr = call_bedrock_json(bedrock, cfg["verify_model_id"],
                               build_verify_prompt(indication, hcp["name"], c["verbatim_quote"], text),
                               temperature=0.0, max_tokens=256)
        c["verified"] = bool((vr or {}).get("verified"))
        c["source_id"] = source["source_id"]; c["kind"] = source["kind"]; c["url"] = source.get("url", "")
        if source["kind"] == "pubmed":
            c["pmid"] = source.get("pmid", ""); c["year"] = source.get("year", "")
        kept.append(c)
    if not source_is_relevant(kept):
        return None
    return {"claims": kept}


def main():
    import argparse
    p = argparse.ArgumentParser(); p.add_argument("--force", action="store_true")
    args = p.parse_args()
    cfg_ini = configparser.ConfigParser(); cfg_ini.read(os.path.join(_DIR, "config.ini"))
    bc = cfg_ini["bedrock"]
    cfg = {"ingest_model_id": bc["ingest_model_id"], "verify_model_id": bc["verify_model_id"],
           "extraction_max_tokens": bc["extraction_max_tokens"]}

    out_path = os.path.join(_DIR, "data", "wiki.json")
    if os.path.exists(out_path) and not args.force:
        log.info("wiki.json exists — skipping (use --force)"); return

    with open(os.path.join(_DIR, "data", "sources.json"), encoding="utf-8") as f:
        data = json.load(f)
    indication = data["indication"]
    term_list = [t["term_en"] for t in data["pca_terms"] if t["term_en"]]
    roster = [{"s_customer_id": h["s_customer_id"],
               "firstname": h["name"].split(" ")[0] if h["name"] else "",
               "lastname": h["name"].split(" ")[-1] if h["name"] else ""} for h in data["hcps"]]

    bedrock = make_bedrock_client(bc["aws_profile"])
    out_hcps = []
    for h in data["hcps"]:
        all_sources = h.get("web_sources", []) + h.get("pubmed_sources", [])
        results = []
        with ThreadPoolExecutor(max_workers=int(bc["ingest_max_workers"])) as ex:
            futures = [ex.submit(process_source, s, h, indication, term_list, bedrock, cfg)
                       for s in all_sources]
            for fut in futures:
                r = fut.result()
                if r:
                    results.append(r)
        claims = [c for r in results for c in r["claims"] if c.get("verified")]
        web_ids = {c["source_id"] for c in claims if c["kind"] == "web"}
        pmids = {c["source_id"] for c in claims if c["kind"] == "pubmed"}
        years = {}
        for c in claims:
            if c["kind"] == "pubmed" and c.get("year"):
                years[c["year"]] = years.get(c["year"], 0) + 1
        # dedup mentioned hcps -> comention names
        mentioned = []
        for c in claims:
            mentioned += resolve_mentions(c.get("mentioned_hcps", []), roster)
        out_hcps.append({
            "s_customer_id": h["s_customer_id"], "name": h["name"], "city": h["city"],
            "specialty": h["specialty"], "rating": h["rating"], "pub_by_year": h.get("pub_by_year", {}),
            "verified_web_count": len(web_ids), "verified_pubmed_count": len(pmids),
            "verified_pubmed_years": years, "verified_pmids": sorted(pmids),
            "claims": claims, "mentioned": mentioned,
        })
        log.info(f"  {h['name']:<30} web={len(web_ids)} pubmed={len(pmids)}")

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"indication": indication, "client_drug": data["client_drug"],
                   "generated_at": datetime.now().isoformat(timespec="seconds"),
                   "pca_terms": data["pca_terms"], "hcps": out_hcps}, f, ensure_ascii=False, indent=2)
    log.info(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
```

> **Note:** the test monkeypatches `mod.call_bedrock_json`; because `main()` calls the module-level imported name, the patch in the test affects `process_source` correctly. The `wiki/<ts>/` markdown tree is a nice-to-have audit artifact — port `write_wiki_tree` from Service A's `03_wiki_build.py` in a follow-up if desired; it is not required for the report.

- [ ] **Step 4: Run tests** — `python -m pytest tests/test_03_wiki.py -v` → all PASS.

- [ ] **Step 5: Commit**

```bash
git add b_kol_identification/03_wiki_build.py b_kol_identification/tests/test_03_wiki.py
git commit -m "feat(kol): stage 03 LLM ingest-ground-verify wiki build"
```

---

## Task 6: Stage 04 — assemble KOL ranking

**Files:**
- Create: `b_kol_identification/04_assemble_kols.py`
- Test: `b_kol_identification/tests/test_04_assemble.py`

**Interfaces:**
- Consumes: `wiki.json`; `connect_snowflake`
- Produces:
  - `score_hcps(hcps: list[dict]) -> list[dict]`  (adds `kol_score`, `latest_year`; sorts desc)
  - `assign_tiers(hcps, tier_a_pct, tier_b_pct) -> list`  (copy from v1 `02_score_and_tier.assign_tiers`)
  - `flag_rising_stars(hcps, min_pubs, growth) -> list`  (copy from v1; input years = `verified_pubmed_years`)
  - `aggregate_themes(hcp, pca_terms, top_n=5) -> list`
  - `build_coauthor_query(pubmed_author: str, pmids: list[str]) -> str`
  - `build_coauthor_edges(author_rows, verified_by_pmid, roster) -> list`
  - `build_comention_edges(hcps) -> list`

- [ ] **Step 1: Write failing tests**

```python
# b_kol_identification/tests/test_04_assemble.py
import importlib.util, os
_S = os.path.join(os.path.dirname(__file__), "..", "04_assemble_kols.py")
_spec = importlib.util.spec_from_file_location("asm", _S)
mod = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(mod)

def test_score_is_sum_of_verified_counts_sorted():
    hcps = [{"s_customer_id":"1","verified_web_count":1,"verified_pubmed_count":1,"verified_pubmed_years":{"2020":1}},
            {"s_customer_id":"2","verified_web_count":3,"verified_pubmed_count":4,"verified_pubmed_years":{"2024":4}}]
    out = mod.score_hcps(hcps)
    assert out[0]["s_customer_id"] == "2" and out[0]["kol_score"] == 7
    assert out[0]["latest_year"] == 2024

def test_rising_star_new_voice_on_verified_years():
    hcps = [{"verified_pubmed_years":{"2024":4,"2025":0}}]
    out = mod.flag_rising_stars(hcps, min_pubs=3, growth=3.0)
    assert out[0]["rising_star"] is True

def test_aggregate_themes_counts_from_claims():
    hcp = {"claims":[{"themes":["CF_OBESITY","CF_GLP1"]},{"themes":["CF_OBESITY"]}]}
    terms = [{"term_key":"CF_OBESITY","term_en":"Obesity"},{"term_key":"CF_GLP1","term_en":"GLP-1"}]
    out = mod.aggregate_themes(hcp, terms, top_n=5)
    assert out[0]["term_key"] == "CF_OBESITY" and out[0]["count"] == 2

def test_coauthor_query_has_pmid_in_list():
    sql = mod.build_coauthor_query("CORE.PUBMED.AUTHOR", ["39000001"])
    assert "CORE.PUBMED.AUTHOR" in sql and "'39000001'" in sql

def test_coauthor_edges_marks_external():
    author_rows = [{"PMID":"p1","FIRSTNAME":"Anna","LASTNAME":"Berg"},
                   {"PMID":"p1","FIRSTNAME":"Ext","LASTNAME":"Person"}]
    verified_by_pmid = {"p1":["10"]}   # only HCP 10 authored p1 among our KOLs
    roster = [{"s_customer_id":"10","firstname":"Anna","lastname":"Berg","name":"Anna Berg"}]
    edges = mod.build_coauthor_edges(author_rows, verified_by_pmid, roster)
    ext = [e for e in edges if e["b_external"]]
    assert any(e["b_name"] == "Ext Person" for e in ext)

def test_comention_edges_from_mentions():
    hcps = [{"s_customer_id":"10","name":"Anna Berg",
             "mentioned":[{"name":"Karl Neu","s_customer_id":"20"},{"name":"X","s_customer_id":""}]}]
    edges = mod.build_comention_edges(hcps)
    assert {"from":"10","to":"20","from_name":"Anna Berg","to_name":"Karl Neu","count":1} in edges
```

- [ ] **Step 2: Run to verify failure**

Run: `cd b_kol_identification && python -m pytest tests/test_04_assemble.py -v 2>&1 | head`
Expected: import error.

- [ ] **Step 3: Implement**

```python
"""
Stage 04: Assemble the KOL ranking — score, tiers, rising stars, themes, network.
Reads:  data/wiki.json
Writes: data/kol_final.json  (resume-safe)
"""
import configparser, json, logging, os, sys
from datetime import datetime

logging.basicConfig(format="%(asctime)s [%(levelname)s] %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)
_DIR = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(_DIR, ".."))


def score_hcps(hcps: list) -> list:
    out = []
    for h in hcps:
        score = int(h.get("verified_web_count", 0)) + int(h.get("verified_pubmed_count", 0))
        years = [int(y) for y in h.get("verified_pubmed_years", {}).keys() if str(y).isdigit()]
        out.append({**h, "kol_score": score, "latest_year": max(years) if years else 0})
    out.sort(key=lambda h: (h["kol_score"], h["latest_year"]), reverse=True)
    return out


def assign_tiers(hcps: list, tier_a_pct: float, tier_b_pct: float) -> list:
    if not hcps:
        return []
    scores = sorted(h["kol_score"] for h in hcps)
    n = len(scores)
    thresh_a = scores[min(int(n * tier_a_pct / 100), n - 1)]
    thresh_b = scores[min(int(n * tier_b_pct / 100), n - 1)]
    return [{**h, "tier": ("A" if h["kol_score"] >= thresh_a
                           else "B" if h["kol_score"] >= thresh_b else "C")} for h in hcps]


def flag_rising_stars(hcps: list, min_pubs: int, growth: float) -> list:
    out = []
    for h in hcps:
        years = {int(y): int(c) for y, c in h.get("verified_pubmed_years", {}).items() if str(y).isdigit()}
        cur = max(years) if years else datetime.now().year
        recent = sum(c for y, c in years.items() if y >= cur - 2)
        prior = sum(c for y, c in years.items() if y < cur - 2)
        new_voice = recent >= min_pubs and prior == 0
        accel = (recent / max(prior, 1)) >= growth and recent >= min_pubs
        out.append({**h, "rising_star": bool(new_voice or accel)})
    return out


def aggregate_themes(hcp: dict, pca_terms: list, top_n: int = 5) -> list:
    counts = {}
    for c in hcp.get("claims", []):
        for t in c.get("themes", []):
            counts[t] = counts.get(t, 0) + 1
    label = {t["term_key"]: t["term_en"] for t in pca_terms}
    ranked = sorted(({"term_key": k, "term_en": label.get(k, k), "count": v}
                     for k, v in counts.items()), key=lambda x: x["count"], reverse=True)
    return ranked[:top_n]


def top_quotes(hcp: dict, n: int = 3) -> list:
    order = {"high": 0, "medium": 1, "low": 2}
    cs = sorted([c for c in hcp.get("claims", []) if c.get("verified")],
                key=lambda c: order.get(c.get("confidence", "medium"), 1))
    return [{"quote": c["verbatim_quote"], "url": c.get("url", ""), "sentiment": c.get("sentiment", "neutral")}
            for c in cs[:n]]


def _in_list(ids):
    return ", ".join("'" + str(i).replace("'", "''") + "'" for i in ids)


def build_coauthor_query(pubmed_author: str, pmids: list) -> str:
    return (f"SELECT PMID, ORCID, FIRSTNAME, LASTNAME, AFFILIATION FROM {pubmed_author} "
            f"WHERE PMID IN ({_in_list(pmids)})")


def _g(row, k):
    v = row.get(k)
    return v if v is not None else row.get(k.lower())


def build_coauthor_edges(author_rows: list, verified_by_pmid: dict, roster: list) -> list:
    from pipeline_common import name_matches
    by_pmid = {}
    for r in author_rows:
        by_pmid.setdefault(str(_g(r, "PMID") or ""), []).append(
            f"{_g(r,'FIRSTNAME') or ''} {_g(r,'LASTNAME') or ''}".strip())
    rmap = {rr["s_customer_id"]: rr for rr in roster}
    edge_counts = {}
    for pmid, our_ids in verified_by_pmid.items():
        authors = by_pmid.get(pmid, [])
        for aid in our_ids:
            a = rmap.get(aid)
            if not a:
                continue
            for author_name in authors:
                # skip the author that is this HCP
                if name_matches(author_name, a.get("firstname", ""), a.get("lastname", "")):
                    continue
                # is this author another of our KOLs?
                match = next((rr for rr in roster
                              if name_matches(author_name, rr.get("firstname", ""), rr.get("lastname", ""))), None)
                if match:
                    key = tuple(sorted([aid, match["s_customer_id"]])) + (False,)
                    edge_counts[key] = edge_counts.get(key, {"a_name": a["name"], "b_name": match["name"]})
                    edge_counts[key]["n"] = edge_counts[key].get("n", 0) + 1
                else:
                    key = (aid, author_name, True)
                    edge_counts[key] = edge_counts.get(key, {"a_name": a["name"], "b_name": author_name})
                    edge_counts[key]["n"] = edge_counts[key].get("n", 0) + 1
    edges = []
    for key, v in edge_counts.items():
        if key[-1]:  # external
            edges.append({"hcp_a": key[0], "hcp_b": key[1], "shared_pmids": v["n"],
                          "a_name": v["a_name"], "b_name": v["b_name"], "b_external": True})
        else:
            edges.append({"hcp_a": key[0], "hcp_b": key[1], "shared_pmids": v["n"],
                          "a_name": v["a_name"], "b_name": v["b_name"], "b_external": False})
    return edges


def build_comention_edges(hcps: list) -> list:
    edges = []
    for h in hcps:
        counts = {}
        for m in h.get("mentioned", []):
            if m.get("s_customer_id"):
                counts[(m["s_customer_id"], m["name"])] = counts.get((m["s_customer_id"], m["name"]), 0) + 1
        for (to_id, to_name), c in counts.items():
            if to_id == h["s_customer_id"]:
                continue
            edges.append({"from": h["s_customer_id"], "to": to_id,
                          "from_name": h["name"], "to_name": to_name, "count": c})
    return edges


def main():
    import argparse, snowflake.connector
    from pipeline_common import connect_snowflake
    p = argparse.ArgumentParser(); p.add_argument("--force", action="store_true")
    args = p.parse_args()
    cfg = configparser.ConfigParser(); cfg.read(os.path.join(_DIR, "config.ini"))
    sf, tb, sc = cfg["snowflake"], cfg["tables"], cfg["scoring"]

    out_path = os.path.join(_DIR, "data", "kol_final.json")
    if os.path.exists(out_path) and not args.force:
        log.info("kol_final.json exists — skipping (use --force)"); return

    with open(os.path.join(_DIR, "data", "wiki.json"), encoding="utf-8") as f:
        data = json.load(f)
    pca_terms = data["pca_terms"]

    hcps = score_hcps(data["hcps"])
    hcps = assign_tiers(hcps, float(sc["tier_a_percentile"]), float(sc["tier_b_percentile"]))
    hcps = flag_rising_stars(hcps, int(sc["rising_star_min_pubs"]), float(sc["rising_star_growth"]))
    for h in hcps:
        h["theme_labels"] = aggregate_themes(h, pca_terms)
        h["top_quotes"] = top_quotes(h)

    roster = [{"s_customer_id": h["s_customer_id"], "name": h["name"],
               "firstname": h["name"].split(" ")[0] if h["name"] else "",
               "lastname": h["name"].split(" ")[-1] if h["name"] else ""} for h in hcps]

    # collaboration network from verified PubMed
    verified_by_pmid = {}
    for h in hcps:
        for pmid in h.get("verified_pmids", []):
            verified_by_pmid.setdefault(pmid, []).append(h["s_customer_id"])
    coauthor_edges = []
    all_pmids = list(verified_by_pmid.keys())
    if all_pmids:
        conn = connect_snowflake(sf["aws_profile"], sf["warehouse"], sf["database"])
        cur = conn.cursor(snowflake.connector.DictCursor)
        cur.execute(build_coauthor_query(tb["pubmed_author"], all_pmids))
        author_rows = cur.fetchall(); cur.close(); conn.close()
        coauthor_edges = build_coauthor_edges(author_rows, verified_by_pmid, roster)
    comention_edges = build_comention_edges(hcps)

    # strip bulky per-claim payload from the final file (keep top_quotes + counts)
    for h in hcps:
        h.pop("claims", None); h.pop("mentioned", None)

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"indication": data["indication"], "client_drug": data["client_drug"],
                   "generated_at": datetime.now().isoformat(timespec="seconds"),
                   "pca_terms": pca_terms, "hcps": hcps,
                   "coauthor_edges": coauthor_edges, "comention_edges": comention_edges},
                  f, ensure_ascii=False, indent=2)
    log.info(f"Wrote {out_path} — {len(hcps)} KOLs")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests** — `python -m pytest tests/test_04_assemble.py -v` → all PASS.

- [ ] **Step 5: Commit**

```bash
git add b_kol_identification/04_assemble_kols.py b_kol_identification/tests/test_04_assemble.py
git commit -m "feat(kol): stage 04 scoring, tiers, rising stars, network"
```

---

## Task 7: Stage 05 — report renderers (HTML)

**Files:**
- Create: `b_kol_identification/05_generate_report.py`
- Test: `b_kol_identification/tests/test_05_report.py`

**Interfaces:**
- Consumes: `kol_final.json`
- Produces:
  - `PALETTE` dict (the professional multi-hue tokens)
  - `render_stat_cards(data) -> str`, `render_kol_table(hcps, top_n) -> str`, `render_rising_stars(hcps, all_years) -> str`, `render_sparkline(pub_by_year, all_years) -> str`, `render_thematic_heatmap(hcps, pca_terms, top_n) -> str`, `render_regional(hcps) -> str`, `render_network(coauthor_edges, comention_edges, hcps) -> str`, `render_profiles(hcps, all_years, top_n) -> str`, `build_report_html(data) -> str`

**Reuse:** the archived v1 `b_kol_identification/03_generate_report.py` already implements `render_sparkline`, `render_kol_table`, `render_rising_stars`, `render_thematic_heatmap`, `render_regional_chart`, and `render_kol_profiles`. Port each renderer, changing: (a) field names (`composite_score`→`kol_score`, `pub_count_total`→`kol_score`, drop `digi_score`/`norm_*`), (b) the colour tokens to `PALETTE`, (c) the KOL table's score column to show `verified_web_count`+`verified_pubmed_count`. Replace the co-authorship table with `render_network` (below).

- [ ] **Step 1: Write failing tests**

```python
# b_kol_identification/tests/test_05_report.py
import importlib.util, os
_S = os.path.join(os.path.dirname(__file__), "..", "05_generate_report.py")
_spec = importlib.util.spec_from_file_location("rep", _S)
mod = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(mod)

DATA = {"indication":"Obesity","client_drug":"Ozempic","generated_at":"2026-07-09T10:00:00",
  "pca_terms":[{"term_key":"CF_OBESITY","term_en":"Obesity"}],
  "hcps":[{"s_customer_id":"10","name":"Anna Berg","city":"Berlin","specialty":"Innere Medizin",
           "rating":"A","verified_web_count":3,"verified_pubmed_count":4,"kol_score":7,"latest_year":2024,
           "tier":"A","rising_star":False,"pub_by_year":{"2023":2,"2024":2},
           "theme_labels":[{"term_key":"CF_OBESITY","term_en":"Obesity","count":5}],
           "top_quotes":[{"quote":"patient improved","url":"http://x","sentiment":"positive"}]}],
  "coauthor_edges":[{"hcp_a":"10","hcp_b":"ext1","shared_pmids":2,"a_name":"Anna Berg","b_name":"Ext P","b_external":True}],
  "comention_edges":[]}

def test_stat_cards_show_kol_count_and_no_digiscore():
    html = mod.render_stat_cards(DATA)
    assert "1" in html
    assert "digi" not in html.lower()

def test_kol_table_shows_verified_source_counts():
    html = mod.render_kol_table(DATA["hcps"], top_n=25)
    assert "Anna Berg" in html and "7" in html   # kol_score
    assert "Obesity" in html                       # theme

def test_network_lists_external_collaborator():
    html = mod.render_network(DATA["coauthor_edges"], DATA["comention_edges"], DATA["hcps"])
    assert "Ext P" in html

def test_build_report_html_is_selfcontained():
    html = mod.build_report_html(DATA)
    assert html.strip().startswith("<!DOCTYPE html>")
    assert "http://" not in html.split("top_quotes")[0] or "cdn" not in html.lower()
    assert "Anna Berg" in html
```

- [ ] **Step 2: Run to verify failure**

Run: `cd b_kol_identification && python -m pytest tests/test_05_report.py -v 2>&1 | head`
Expected: import error.

- [ ] **Step 3: Implement the palette + renderers + `build_report_html`**

Start the file with the palette and the section order, then port the v1 renderers per the Reuse note. Minimum new code:

```python
"""
Stage 05: Generate the KOL report (HTML top-25) + Excel.
Reads:  data/kol_final.json
Writes: results/kol_report_<ts>.html  and  results/kol_report_<ts>.xlsx
"""
import configparser, json, logging, os, sys
from datetime import datetime

logging.basicConfig(format="%(asctime)s [%(levelname)s] %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)
_DIR = os.path.dirname(__file__)

PALETTE = {
    "ink": "#1b2430", "muted": "#5c6774", "line": "#e2e7ee", "bg": "#f4f6f8", "card": "#fff",
    "accent": "#2f4a7c", "teal": "#0d7d74", "violet": "#6d5ac0", "amber": "#b7791f", "emerald": "#1f8a5b",
    "tierA": "#1f8a5b", "tierB": "#3b5b92", "tierC": "#6b7684",
    "pos": "#0d7d74", "neu": "#6b7684", "neg": "#b4432f",
}

def _esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))

def render_stat_cards(data):
    hcps = data["hcps"]
    tiers = {t: sum(1 for h in hcps if h.get("tier") == t) for t in "ABC"}
    rising = sum(1 for h in hcps if h.get("rising_star"))
    total_sources = sum(h.get("kol_score", 0) for h in hcps)
    cards = [("KOLs", len(hcps)), ("Tier A", tiers["A"]), ("Tier B", tiers["B"]),
             ("Tier C", tiers["C"]), ("Rising Stars", rising), ("Verified sources", total_sources)]
    cells = "".join(
        f'<div class="stat"><div class="v">{v}</div><div class="k">{_esc(k)}</div></div>' for k, v in cards)
    return f'<div class="stats">{cells}</div>'

def render_kol_table(hcps, top_n):
    rows = ""
    for i, h in enumerate(hcps[:top_n], 1):
        themes = ", ".join(_esc(t["term_en"]) for t in h.get("theme_labels", [])[:3])
        badge = f'<span class="pill {h.get("tier","C").lower()}">{h.get("tier","C")}</span>'
        rising = ' <span class="pill rise">Rising</span>' if h.get("rising_star") else ""
        rows += (f'<tr><td>{i}</td><td>{badge}{rising}</td>'
                 f'<td><b>{_esc(h["name"])}</b><br><span class="muted">{_esc(h["specialty"])}</span></td>'
                 f'<td>{_esc(h["city"])}</td>'
                 f'<td><b>{h["kol_score"]}</b> '
                 f'<span class="muted">({h.get("verified_web_count",0)}w / {h.get("verified_pubmed_count",0)}p)</span></td>'
                 f'<td>{h.get("latest_year","")}</td><td>{themes}</td></tr>')
    return (f'<table><thead><tr><th>#</th><th>Tier</th><th>Name / Specialty</th><th>City</th>'
            f'<th>Verified sources</th><th>Latest</th><th>Themes</th></tr></thead><tbody>{rows}</tbody></table>')

def render_network(coauthor_edges, comention_edges, hcps):
    def rows(edges, kind):
        out = ""
        for e in sorted(edges, key=lambda x: x.get("shared_pmids", x.get("count", 0)), reverse=True):
            if kind == "co":
                tag = ' <span class="pill ext">external</span>' if e.get("b_external") else ""
                out += (f'<tr><td>{_esc(e["a_name"])}</td><td>{_esc(e["b_name"])}{tag}</td>'
                        f'<td>{e["shared_pmids"]} shared PMIDs</td></tr>')
            else:
                out += (f'<tr><td>{_esc(e["from_name"])}</td><td>{_esc(e["to_name"])}</td>'
                        f'<td>{e["count"]} web mentions</td></tr>')
        return out or '<tr><td colspan="3" class="muted">none</td></tr>'
    return (f'<h3>PubMed co-authorship</h3><table><tbody>{rows(coauthor_edges,"co")}</tbody></table>'
            f'<h3>Web co-mentions</h3><table><tbody>{rows(comention_edges,"men")}</tbody></table>')

def build_report_html(data):
    all_years = sorted({y for h in data["hcps"] for y in h.get("pub_by_year", {})})
    top_n = 25
    css = f"""
      body{{margin:0;background:{PALETTE['bg']};color:{PALETTE['ink']};
        font:15px/1.55 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif}}
      .wrap{{max-width:1100px;margin:0 auto;padding:28px 22px 64px}}
      h1{{margin:4px 0}} h2{{border-top:1px solid {PALETTE['line']};padding-top:14px;margin-top:34px}}
      table{{border-collapse:collapse;width:100%;font-size:13px;margin:10px 0}}
      th,td{{border:1px solid {PALETTE['line']};padding:7px 10px;text-align:left;vertical-align:top}}
      th{{background:#eef2f7}} .muted{{color:{PALETTE['muted']}}}
      .stats{{display:grid;grid-template-columns:repeat(6,1fr);gap:12px;margin:14px 0}}
      .stat{{background:{PALETTE['card']};border:1px solid {PALETTE['line']};border-radius:10px;padding:14px;text-align:center}}
      .stat .v{{font-size:24px;font-weight:800;color:{PALETTE['accent']}}} .stat .k{{font-size:12px;color:{PALETTE['muted']}}}
      .pill{{font-size:11px;font-weight:700;padding:1px 7px;border-radius:20px}}
      .pill.a{{background:#e7f5ee;color:{PALETTE['tierA']}}} .pill.b{{background:#eaf0f9;color:{PALETTE['tierB']}}}
      .pill.c{{background:#eef1f5;color:{PALETTE['tierC']}}} .pill.rise{{background:#fbf1dd;color:{PALETTE['amber']}}}
      .pill.ext{{background:#efecfa;color:{PALETTE['violet']}}}
      @media(max-width:720px){{.stats{{grid-template-columns:repeat(2,1fr)}}}}
    """
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>KOL Report — {_esc(data['indication'])}</title><style>{css}</style></head>
<body><div class="wrap">
<h1>KOL Identification — {_esc(data['indication'])}</h1>
<p class="muted">Client drug: {_esc(data['client_drug'])} · generated {_esc(data['generated_at'])}</p>
<h2>Executive dashboard</h2>{render_stat_cards(data)}
<h2>KOL Ranking — Top {top_n}</h2>{render_kol_table(data['hcps'], top_n)}
<h2>Collaboration network</h2>{render_network(data['coauthor_edges'], data['comention_edges'], data['hcps'])}
</div></body></html>"""

def main():
    import argparse
    p = argparse.ArgumentParser(); p.add_argument("--force", action="store_true")
    args = p.parse_args()
    cfg = configparser.ConfigParser(); cfg.read(os.path.join(_DIR, "config.ini"))
    with open(os.path.join(_DIR, "data", "kol_final.json"), encoding="utf-8") as f:
        data = json.load(f)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    html_path = os.path.join(_DIR, "results", f"kol_report_{ts}.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(build_report_html(data))
    log.info(f"Wrote {html_path}")
    # Excel written in Task 8

if __name__ == "__main__":
    main()
```

Then port the remaining sections (`render_rising_stars`, `render_sparkline`, `render_thematic_heatmap`, `render_regional`, `render_profiles`) from the v1 file per the Reuse note and insert their calls into `build_report_html` between the ranking and network sections.

- [ ] **Step 4: Run tests** — `python -m pytest tests/test_05_report.py -v` → all PASS.

- [ ] **Step 5: Commit**

```bash
git add b_kol_identification/05_generate_report.py b_kol_identification/tests/test_05_report.py
git commit -m "feat(kol): stage 05 HTML report with multi-hue palette"
```

---

## Task 8: Stage 05 — Excel export + cleanup

**Files:**
- Modify: `b_kol_identification/05_generate_report.py` (add `write_excel`, call in `main`)
- Modify: `b_kol_identification/tests/test_05_report.py` (add Excel test)
- Delete: `b_kol_identification/01_fetch_kol_data.py`, `02_score_and_tier.py`, `03_generate_report.py` (v1, after their renderers are ported)

**Interfaces:**
- Produces: `write_excel(data: dict, path: str) -> None` — one row per KOL.

- [ ] **Step 1: Ensure `openpyxl` is available**

```bash
python -c "import openpyxl" 2>/dev/null || pip install openpyxl
```

- [ ] **Step 2: Add Excel test**

```python
# append to tests/test_05_report.py
def test_write_excel_creates_one_row_per_kol(tmp_path):
    out = tmp_path / "k.xlsx"
    mod.write_excel(DATA, str(out))
    import openpyxl
    wb = openpyxl.load_workbook(out); ws = wb.active
    headers = [c.value for c in ws[1]]
    assert "Name" in headers and "Verified sources" in headers and "Tier" in headers
    assert ws.max_row == 1 + len(DATA["hcps"])
```

- [ ] **Step 3: Run to verify failure**

Run: `python -m pytest tests/test_05_report.py::test_write_excel_creates_one_row_per_kol -v`
Expected: `AttributeError: ... 'write_excel'`.

- [ ] **Step 4: Implement `write_excel` and wire into `main`**

```python
# add to 05_generate_report.py
def write_excel(data: dict, path: str) -> None:
    import openpyxl
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = "KOLs"
    headers = ["Rank", "Name", "Specialty", "City", "Tier", "Rising star",
               "Verified sources", "Web", "PubMed", "Latest year", "Top themes",
               "Representative quote", "Source URL"]
    ws.append(headers)
    for i, h in enumerate(data["hcps"], 1):
        q = (h.get("top_quotes") or [{}])[0]
        ws.append([i, h["name"], h["specialty"], h["city"], h.get("tier", ""),
                   "yes" if h.get("rising_star") else "", h.get("kol_score", 0),
                   h.get("verified_web_count", 0), h.get("verified_pubmed_count", 0),
                   h.get("latest_year", ""),
                   ", ".join(t["term_en"] for t in h.get("theme_labels", [])[:5]),
                   q.get("quote", ""), q.get("url", "")])
    wb.save(path)
```

In `main`, after writing the HTML, add:

```python
    xlsx_path = os.path.join(_DIR, "results", f"kol_report_{ts}.xlsx")
    write_excel(data, xlsx_path)
    log.info(f"Wrote {xlsx_path}")
```

- [ ] **Step 5: Run tests** — `python -m pytest tests/test_05_report.py -v` → all PASS.

- [ ] **Step 6: Delete v1 files and run the full suite**

```bash
git rm b_kol_identification/01_fetch_kol_data.py b_kol_identification/02_score_and_tier.py b_kol_identification/03_generate_report.py
rm -rf b_kol_identification/scoring_review   # discussion artifact, no longer needed
cd b_kol_identification && python -m pytest tests -q
```
Expected: entire suite PASS.

- [ ] **Step 7: Commit**

```bash
git add b_kol_identification
git commit -m "feat(kol): stage 05 Excel export; remove v1 pipeline"
```

---

## Task 9: Service CLAUDE.md + repo table update

**Files:**
- Create: `b_kol_identification/CLAUDE.md`
- Modify: `CLAUDE.md` (repo root — add the service to the Services table)

- [ ] **Step 1: Write `b_kol_identification/CLAUDE.md`**

Mirror the structure of `a_comp_hcp_communication/CLAUDE.md`: what the service does, the score definition (count of verified relevant sources), the funnel, the five stages + run order, the tables, and the reuse of `pipeline_common`. Include the run order:

```
python 01_fetch_and_shortlist.py
python 02_retrieve_sources.py
python 03_wiki_build.py
python 04_assemble_kols.py
python 05_generate_report.py
```

- [ ] **Step 2: Add the service to the repo `CLAUDE.md` Services table**

Add row: `| b_kol_identification/ | 2.1 KOL Identification & Mapping | active |`.

- [ ] **Step 3: Commit**

```bash
git add b_kol_identification/CLAUDE.md CLAUDE.md
git commit -m "docs(kol): service CLAUDE.md and repo services table"
```

---

## Self-Review

**Spec coverage:**
- §2 principle (score = verified relevant sources) → Task 6 `score_hcps`. ✓
- §4 funnel (cheap SQL → top-75 → LLM → top-25) → Tasks 3 (shortlist), 5 (LLM), 6/7 (top-25). ✓
- §5.1 web gate (`IS_OLD=0`, `IN_RELATION>29`, no date window) → Task 2 `build_web_candidates_query`. ✓
- §5.1 PubMed (merge_result>1 ∩ CF-flag, 5-yr window, CF-treffer weight) → Task 2/3. ✓
- §5.2 no vector search, content from LLM_VALIDATION/CORE.PUBMED.ARTICLE → Task 4. ✓
- §5.3 unified relevance ingest/ground/verify, themes, co-mentions → Task 5. ✓
- §5.4 tiers, rising stars on verified PubMed years, network (co-authors + co-mentions) → Task 6. ✓
- §5.5 HTML top-25 + Excel, multi-hue palette → Tasks 7/8. ✓
- §8 reuse pipeline_common → Task 1. ✓

**Placeholder scan:** No "TBD"/"handle edge cases"/"similar to Task N". The one deferred nicety (`wiki/<ts>/` markdown tree) is explicitly optional and not on the report's critical path. Schema names are concrete config values flagged for first-run confirmation (spec §9), not placeholders.

**Type consistency:** `kol_score`, `verified_web_count`, `verified_pubmed_count`, `verified_pubmed_years`, `theme_labels`, `top_quotes`, `coauthor_edges`, `comention_edges` are used identically across Tasks 6/7/8. Claim dict keys (`verbatim_quote`, `themes`, `mentioned_hcps`, `verified`, `source_id`, `kind`) are consistent across Tasks 5/6.

**First-run verification (not code — operational):** confirm the spec §9 items before/at the first live run — cross-DB table locations, `IN_RELATION` range, the CF-flag column layout in `PUBMED_CONTENT_FRAME_SINGLE_TBL`, and `IS_OLD` semantics. Stage 01 logs per-HCP candidate counts for the shortlist so LLM volume is visible before Stage 03 runs.
