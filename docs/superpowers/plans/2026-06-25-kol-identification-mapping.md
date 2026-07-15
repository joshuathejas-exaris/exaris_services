# KOL Identification & Mapping Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a three-script pipeline (`01_fetch_kol_data.py` → `02_score_and_tier.py` → `03_generate_report.py`) that pulls KOL data from Snowflake, scores and tiers every HCP, and renders a self-contained HTML report.

**Architecture:** Script 01 runs five Snowflake queries and writes `kol_raw.json`. Script 02 reads that file, computes composite scores, assigns tiers A/B/C and Rising Star flags, runs one additional Snowflake query (Q6 co-authorship) with the now-known top-50 IDs, and writes `kol_scored.json`. Script 03 reads the scored file and renders a portable, self-contained HTML report with inline SVG charts.

**Tech Stack:** Python 3.10+, snowflake-connector-python, boto3, cryptography (all already present in the existing comp-HCP pipeline). No new dependencies.

## Global Constraints

- All Snowflake credentials fetched from AWS Secrets Manager via the existing `shared.parameter_manager` + `shared.secret_reader` pattern — never hardcoded.
- All schema names read from `config.ini` — no schema strings in script source.
- Scripts must be resume-safe: skip if output JSON already exists unless `--force` is passed.
- Report HTML must be fully self-contained: no external fonts, no CDN scripts, no network calls at render time. All charts are inline SVG.
- Tests use `importlib.util.spec_from_file_location` to load scripts (numeric filename prefix pattern from existing test suite).
- `weight_sim_score` defaults to 0.00 (no vector search in this pipeline). Active weights must sum to 1.00 — validated at script startup.
- Python files live at `b_kol_identification/`. Tests at `b_kol_identification/tests/`.

---

## File Map

| File | Role |
|---|---|
| `b_kol_identification/config.ini` | All runtime parameters — schemas, weights, thresholds |
| `b_kol_identification/data/input.json` | Run-time input: indication + client drug |
| `b_kol_identification/01_fetch_kol_data.py` | Q1–Q5 Snowflake queries → `kol_raw.json` |
| `b_kol_identification/02_score_and_tier.py` | Scoring math + Q6 co-authorship → `kol_scored.json` |
| `b_kol_identification/03_generate_report.py` | HTML report renderer → `results/kol_report_{ts}.html` |
| `b_kol_identification/tests/__init__.py` | Empty |
| `b_kol_identification/tests/test_01_fetch.py` | Tests for query builders + row normaliser |
| `b_kol_identification/tests/test_02_score.py` | Tests for scoring math |
| `b_kol_identification/tests/test_03_report.py` | Tests for HTML/SVG section renderers |

---

### Data Contracts

**`kol_raw.json`**
```json
{
  "indication": "Obesity",
  "client_drug": "Ozempic",
  "pca_terms": [
    {"term_key": "CF_OBESITY", "term_en": "Obesity"}
  ],
  "hcps": {
    "12345": {
      "s_customer_id": "12345",
      "firstname": "Max",
      "lastname": "Mustermann",
      "name": "Max Mustermann",
      "city": "Berlin",
      "specialty": "Innere Medizin",
      "rating": "A",
      "digi_score": 42.5,
      "otm": 15.0,
      "vs": 10.0,
      "social": 8.0,
      "sentiment_rating": "positive",
      "pub_by_year": {
        "2022": {"pub_count": 3, "CF_OBESITY": 2, "CF_GLP_1_DRUG": 1},
        "2023": {"pub_count": 5, "CF_OBESITY": 4, "CF_GLP_1_DRUG": 3}
      }
    }
  }
}
```

**`kol_scored.json`**
```json
{
  "indication": "Obesity",
  "client_drug": "Ozempic",
  "generated_at": "2026-06-25T10:00:00",
  "pca_terms": [{"term_key": "CF_OBESITY", "term_en": "Obesity"}],
  "hcps": [
    {
      "s_customer_id": "12345",
      "name": "Max Mustermann",
      "city": "Berlin",
      "specialty": "Innere Medizin",
      "rating": "A",
      "digi_score": 42.5,
      "pub_count_total": 8,
      "pub_count_last_2yr": 5,
      "pub_count_pre_2yr": 3,
      "cf_count_total": 10,
      "cf_by_term": {"CF_OBESITY": 6, "CF_GLP_1_DRUG": 4},
      "pub_by_year": {"2022": 3, "2023": 5},
      "composite_score": 0.87,
      "norm_pub": 0.90,
      "norm_cf": 0.75,
      "norm_digi": 0.80,
      "tier": "A",
      "rising_star": false,
      "theme_labels": [
        {"term_key": "CF_OBESITY", "term_en": "Obesity", "count": 6}
      ]
    }
  ],
  "coauth_edges": [
    {"hcp_a": "12345", "hcp_b": "67890", "shared_pmids": 3}
  ]
}
```

---

## Task 1: Scaffold — directory, config, input template, test init

**Files:**
- Create: `b_kol_identification/config.ini`
- Create: `b_kol_identification/data/input.json`
- Create: `b_kol_identification/data/.gitkeep`
- Create: `b_kol_identification/results/.gitkeep`
- Create: `b_kol_identification/tests/__init__.py`

**Interfaces:**
- Produces: `config.ini` read by all three scripts via `configparser.ConfigParser()`

- [ ] **Step 1: Create directory structure**

```bash
mkdir -p b_kol_identification/data b_kol_identification/results b_kol_identification/tests
touch b_kol_identification/data/.gitkeep b_kol_identification/results/.gitkeep
touch b_kol_identification/tests/__init__.py
```

- [ ] **Step 2: Write `config.ini`**

```ini
[snowflake]
aws_profile   = AdministratorAccess-311524101909
warehouse     = COMPUTE_WH
database      = CUST_NOVO
schema_v1     = ADIPOS_AMBU_V1
schema_final  = ADIPOS_AMBU_FINAL
schema_tmp    = ADIPOS_AMBU_V1
top_n_coauth  = 50

[scoring]
# Weights must sum to 1.0. weight_sim_score=0 until vector search is wired in.
weight_pub_count     = 0.45
weight_cf_count      = 0.30
weight_digi_score    = 0.25
weight_sim_score     = 0.00
tier_a_percentile    = 85
tier_b_percentile    = 60
rising_star_min_pubs = 3
rising_star_growth   = 3.0
data_window_years    = 3

[report]
top_n_profiles  = 20
top_n_heatmap   = 20
```

- [ ] **Step 3: Write `data/input.json`**

```json
{
  "indication": "Obesity",
  "client_drug": "Ozempic"
}
```

- [ ] **Step 4: Commit**

```bash
git add b_kol_identification/
git commit -m "feat(kol): scaffold directory, config, and input template"
```

---

## Task 2: 01_fetch — query builders and row normaliser

**Files:**
- Create: `b_kol_identification/01_fetch_kol_data.py` (pure helper functions only — no `main()` yet)
- Create: `b_kol_identification/tests/test_01_fetch.py`

**Interfaces:**
- Produces:
  - `build_pca_query(schema_v1: str) -> str`
  - `build_relevant_pmids_query(schema_final: str) -> str`
  - `build_pub_counts_query(schema_v1: str, schema_final: str, pca_term_keys: list[str]) -> str`
  - `build_digi_query(schema_v1: str) -> str`
  - `build_hcp_meta_query(schema_tmp: str, schema_final: str) -> str`
  - `normalise_digi_row(row: dict) -> dict`  — keys: s_customer_id, digi_score, otm, vs, social, sentiment_rating
  - `normalise_meta_row(row: dict) -> dict`  — keys: s_customer_id, firstname, lastname, name, city, specialty, rating
  - `normalise_pub_row(row: dict, pca_term_keys: list[str]) -> tuple[str, str, int, dict]`  — (s_customer_id, year_val, pub_count, cf_counts)

- [ ] **Step 1: Write the failing tests**

```python
# b_kol_identification/tests/test_01_fetch.py
import importlib.util, os, sys
import pytest

_SCRIPT = os.path.join(os.path.dirname(__file__), "..", "01_fetch_kol_data.py")
_spec = importlib.util.spec_from_file_location("fetch_kol_data", _SCRIPT)
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)

build_pca_query         = mod.build_pca_query
build_relevant_pmids_query = mod.build_relevant_pmids_query
build_pub_counts_query  = mod.build_pub_counts_query
build_digi_query        = mod.build_digi_query
build_hcp_meta_query    = mod.build_hcp_meta_query
normalise_digi_row      = mod.normalise_digi_row
normalise_meta_row      = mod.normalise_meta_row
normalise_pub_row       = mod.normalise_pub_row


def test_build_pca_query_contains_table_and_filter():
    sql = build_pca_query("MY_V1")
    assert "MY_V1.CONTENT_FRAME_SPEC" in sql
    assert "PCA" in sql.upper()
    assert "'X'" in sql.upper() or "= 'X'" in sql.upper()


def test_build_relevant_pmids_query_contains_percentile_and_table():
    sql = build_relevant_pmids_query("MY_FINAL")
    assert "MY_FINAL.PUBMED_SCORED" in sql
    assert "PERCENTILE_CONT" in sql.upper()
    assert "0.75" in sql


def test_build_pub_counts_query_contains_all_pca_columns():
    sql = build_pub_counts_query("MY_V1", "MY_FINAL", ["CF_OBESITY", "CF_GLP_1_DRUG"])
    assert "MY_V1.PUBMED_CONTENT_FRAME_SINGLE" in sql
    assert "MY_FINAL.PUBMED_SCORED" in sql
    assert "SUM(cf.CF_OBESITY)" in sql
    assert "SUM(cf.CF_GLP_1_DRUG)" in sql
    assert "YEAR_VAL" in sql


def test_build_pub_counts_query_empty_pca_terms():
    sql = build_pub_counts_query("MY_V1", "MY_FINAL", [])
    assert "COUNT(DISTINCT cf.PMID)" in sql


def test_build_digi_query_contains_table_and_columns():
    sql = build_digi_query("MY_V1")
    assert "MY_V1.DIGISCORE_RESULT" in sql
    assert "DIGI_SCORE" in sql
    assert "S_CUSTOMER_ID" in sql


def test_build_hcp_meta_query_joins_both_tables():
    sql = build_hcp_meta_query("MY_TMP", "MY_FINAL")
    assert "MY_TMP.CUSTOMER_SOURCE" in sql
    assert "MY_FINAL.RATING_RESULT_FINAL" in sql
    assert "IN ('A','B','C','D')" in sql or "IN ('A', 'B', 'C', 'D')" in sql


def test_normalise_digi_row_handles_uppercase_keys():
    row = {"S_CUSTOMER_ID": "42", "DIGI_SCORE": 38.5, "OTM": 12.0,
           "VS": 8.0, "SOCIAL": 5.0, "SENTIMENT_RATING": "positive"}
    result = normalise_digi_row(row)
    assert result["s_customer_id"] == "42"
    assert result["digi_score"] == 38.5
    assert result["sentiment_rating"] == "positive"


def test_normalise_digi_row_handles_none_scores():
    row = {"S_CUSTOMER_ID": "42", "DIGI_SCORE": None, "OTM": None,
           "VS": None, "SOCIAL": None, "SENTIMENT_RATING": None}
    result = normalise_digi_row(row)
    assert result["digi_score"] == 0.0


def test_normalise_meta_row_builds_full_name():
    row = {"S_CUSTOMER_ID": "99", "S_FIRSTNAME": "Anna", "S_LASTNAME": "Müller",
           "S_CITY": "München", "S_HCP_GROUP": "Innere Medizin", "RATING": "B"}
    result = normalise_meta_row(row)
    assert result["name"] == "Anna Müller"
    assert result["city"] == "München"
    assert result["specialty"] == "Innere Medizin"


def test_normalise_meta_row_handles_missing_firstname():
    row = {"S_CUSTOMER_ID": "99", "S_FIRSTNAME": None, "S_LASTNAME": "Müller",
           "S_CITY": "München", "S_HCP_GROUP": "Chirurgie", "RATING": "A"}
    result = normalise_meta_row(row)
    assert result["name"] == "Müller"


def test_normalise_pub_row_extracts_pub_count_and_cf():
    pca_keys = ["CF_OBESITY", "CF_GLP_1_DRUG"]
    row = {"S_CUSTOMER_ID": "77", "YEAR_VAL": "2023",
           "PUB_COUNT": 5, "CF_OBESITY": 4, "CF_GLP_1_DRUG": 2}
    cid, year, count, cf = normalise_pub_row(row, pca_keys)
    assert cid == "77"
    assert year == "2023"
    assert count == 5
    assert cf == {"CF_OBESITY": 4, "CF_GLP_1_DRUG": 2}


def test_normalise_pub_row_handles_missing_cf_columns():
    pca_keys = ["CF_OBESITY", "CF_SEMAGLUTIDE"]
    row = {"S_CUSTOMER_ID": "77", "YEAR_VAL": "2022", "PUB_COUNT": 3, "CF_OBESITY": 2}
    cid, year, count, cf = normalise_pub_row(row, pca_keys)
    assert cf["CF_OBESITY"] == 2
    assert cf["CF_SEMAGLUTIDE"] == 0
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd b_kol_identification && python -m pytest tests/test_01_fetch.py -v 2>&1 | head -20
```
Expected: `ModuleNotFoundError` or `AttributeError` — file doesn't exist yet.

- [ ] **Step 3: Implement the pure helpers in `01_fetch_kol_data.py`**

```python
"""
Stage 01: Fetch raw KOL data from Snowflake.

Reads:  data/input.json
Writes: data/kol_raw.json  (resume-safe — skips if exists, unless --force)
"""
import configparser, json, logging, os, sys

logging.basicConfig(format="%(asctime)s [%(levelname)s] %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)
_DIR = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(_DIR, ".."))


# ── Query builders ─────────────────────────────────────────────────────────────

def build_pca_query(schema_v1: str) -> str:
    return (
        f"SELECT TERM_KEY, TERM_EN "
        f"FROM {schema_v1}.CONTENT_FRAME_SPEC "
        f"WHERE UPPER(PCA) = 'X'"
    )


def build_relevant_pmids_query(schema_final: str) -> str:
    return f"""
WITH pct AS (
    SELECT PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY MAX_SIM) AS p75
    FROM {schema_final}.PUBMED_SCORED
)
SELECT s.PMID
FROM {schema_final}.PUBMED_SCORED s
CROSS JOIN pct
WHERE s.MAX_SIM >= pct.p75
""".strip()


def build_pub_counts_query(schema_v1: str, schema_final: str,
                            pca_term_keys: list) -> str:
    cf_sums = ("\n       ".join(
        f"SUM(cf.{tk}) AS {tk.lower()}" for tk in pca_term_keys
    ))
    cf_part = f",\n       {cf_sums}" if cf_sums else ""
    return f"""
SELECT cf.S_CUSTOMER_ID,
       cf.YEAR_VAL,
       COUNT(DISTINCT cf.PMID) AS pub_count{cf_part}
FROM {schema_v1}.PUBMED_CONTENT_FRAME_SINGLE cf
INNER JOIN (
    WITH pct AS (
        SELECT PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY MAX_SIM) AS p75
        FROM {schema_final}.PUBMED_SCORED
    )
    SELECT s.PMID FROM {schema_final}.PUBMED_SCORED s
    CROSS JOIN pct WHERE s.MAX_SIM >= pct.p75
) AS relevant ON cf.PMID = relevant.PMID
GROUP BY cf.S_CUSTOMER_ID, cf.YEAR_VAL
""".strip()


def build_digi_query(schema_v1: str) -> str:
    return (
        f"SELECT S_CUSTOMER_ID, DIGI_SCORE, OTM, VS, SOCIAL, SENTIMENT_RATING "
        f"FROM {schema_v1}.DIGISCORE_RESULT"
    )


def build_hcp_meta_query(schema_tmp: str, schema_final: str) -> str:
    return f"""
SELECT cs.S_CUSTOMER_ID, cs.S_FIRSTNAME, cs.S_LASTNAME,
       cs.S_CITY, cs.S_HCP_GROUP, r.RATING
FROM {schema_tmp}.CUSTOMER_SOURCE cs
JOIN {schema_final}.RATING_RESULT_FINAL r
    ON cs.S_CUSTOMER_ID = r.S_CUSTOMER_ID
WHERE r.RATING IN ('A','B','C','D')
""".strip()


# ── Row normalisers ────────────────────────────────────────────────────────────

def normalise_digi_row(row: dict) -> dict:
    def _g(key):
        return row.get(key) or row.get(key.lower())
    return {
        "s_customer_id":    str(_g("S_CUSTOMER_ID") or ""),
        "digi_score":       float(_g("DIGI_SCORE")  or 0.0),
        "otm":              float(_g("OTM")          or 0.0),
        "vs":               float(_g("VS")           or 0.0),
        "social":           float(_g("SOCIAL")       or 0.0),
        "sentiment_rating": str(_g("SENTIMENT_RATING") or ""),
    }


def normalise_meta_row(row: dict) -> dict:
    def _g(key):
        return row.get(key) or row.get(key.lower())
    firstname = str(_g("S_FIRSTNAME") or "").strip()
    lastname  = str(_g("S_LASTNAME")  or "").strip()
    name      = " ".join(p for p in [firstname, lastname] if p)
    return {
        "s_customer_id": str(_g("S_CUSTOMER_ID") or ""),
        "firstname":     firstname,
        "lastname":      lastname,
        "name":          name,
        "city":          str(_g("S_CITY")      or ""),
        "specialty":     str(_g("S_HCP_GROUP") or ""),
        "rating":        str(_g("RATING")      or ""),
    }


def normalise_pub_row(row: dict, pca_term_keys: list) -> tuple:
    def _g(key):
        return row.get(key) or row.get(key.lower())
    cf_counts = {tk: int(_g(tk) or 0) for tk in pca_term_keys}
    return (
        str(_g("S_CUSTOMER_ID") or ""),
        str(_g("YEAR_VAL")      or ""),
        int(_g("PUB_COUNT")     or 0),
        cf_counts,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_01_fetch.py -v
```
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add b_kol_identification/01_fetch_kol_data.py b_kol_identification/tests/test_01_fetch.py
git commit -m "feat(kol): 01_fetch query builders and row normalisers with tests"
```

---

## Task 3: 01_fetch — merge logic and `main()`

**Files:**
- Modify: `b_kol_identification/01_fetch_kol_data.py` (add `merge_hcp_records`, `connect_snowflake`, `main`)
- Modify: `b_kol_identification/tests/test_01_fetch.py` (add merge tests)

**Interfaces:**
- Consumes: query builders and normalisers from Task 2
- Produces:
  - `merge_hcp_records(pub_rows, digi_map, meta_map, pca_term_keys) -> dict`
    - `pub_rows`: list of `(s_customer_id, year_val, pub_count, cf_counts)` tuples
    - `digi_map`: `{s_customer_id: normalised_digi_dict}`
    - `meta_map`: `{s_customer_id: normalised_meta_dict}`
    - Returns `{s_customer_id: hcp_record}` matching `kol_raw.json` schema

- [ ] **Step 1: Add merge tests**

Append to `b_kol_identification/tests/test_01_fetch.py`:

```python
merge_hcp_records = mod.merge_hcp_records

PCA_KEYS = ["CF_OBESITY", "CF_GLP_1_DRUG"]

_PUB_ROWS = [
    ("10", "2022", 3, {"CF_OBESITY": 2, "CF_GLP_1_DRUG": 1}),
    ("10", "2023", 5, {"CF_OBESITY": 4, "CF_GLP_1_DRUG": 3}),
    ("20", "2023", 2, {"CF_OBESITY": 1, "CF_GLP_1_DRUG": 0}),
]
_DIGI_MAP = {
    "10": {"s_customer_id": "10", "digi_score": 42.0, "otm": 10.0, "vs": 5.0,
           "social": 3.0, "sentiment_rating": "positive"},
}
_META_MAP = {
    "10": {"s_customer_id": "10", "firstname": "Anna", "lastname": "Berg",
           "name": "Anna Berg", "city": "Berlin", "specialty": "Innere Medizin",
           "rating": "A"},
    "20": {"s_customer_id": "20", "firstname": "Karl", "lastname": "Neu",
           "name": "Karl Neu", "city": "Hamburg", "specialty": "Chirurgie",
           "rating": "B"},
}


def test_merge_builds_pub_by_year_per_hcp():
    result = merge_hcp_records(_PUB_ROWS, _DIGI_MAP, _META_MAP, PCA_KEYS)
    assert "10" in result
    assert "2022" in result["10"]["pub_by_year"]
    assert result["10"]["pub_by_year"]["2022"]["pub_count"] == 3
    assert result["10"]["pub_by_year"]["2023"]["CF_OBESITY"] == 4


def test_merge_includes_digi_fields():
    result = merge_hcp_records(_PUB_ROWS, _DIGI_MAP, _META_MAP, PCA_KEYS)
    assert result["10"]["digi_score"] == 42.0
    assert result["10"]["sentiment_rating"] == "positive"


def test_merge_includes_meta_fields():
    result = merge_hcp_records(_PUB_ROWS, _DIGI_MAP, _META_MAP, PCA_KEYS)
    assert result["10"]["name"] == "Anna Berg"
    assert result["10"]["city"] == "Berlin"


def test_merge_hcp_without_digi_gets_zero_scores():
    result = merge_hcp_records(_PUB_ROWS, {}, _META_MAP, PCA_KEYS)
    assert result["10"]["digi_score"] == 0.0


def test_merge_hcp_without_meta_is_excluded():
    result = merge_hcp_records(_PUB_ROWS, _DIGI_MAP, {"10": _META_MAP["10"]}, PCA_KEYS)
    assert "20" not in result


def test_merge_hcp_without_pub_rows_is_excluded():
    result = merge_hcp_records([], _DIGI_MAP, _META_MAP, PCA_KEYS)
    assert len(result) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_01_fetch.py::test_merge_builds_pub_by_year_per_hcp -v
```
Expected: `AttributeError: module has no attribute 'merge_hcp_records'`

- [ ] **Step 3: Implement `merge_hcp_records` and `main()`**

Append to `b_kol_identification/01_fetch_kol_data.py`:

```python
# ── Merge ─────────────────────────────────────────────────────────────────────

def merge_hcp_records(pub_rows: list, digi_map: dict,
                      meta_map: dict, pca_term_keys: list) -> dict:
    """
    Combine pub, digi and meta rows into one record per HCP.
    HCPs not present in meta_map (not verified) or with no pub rows are excluded.
    """
    # Accumulate pub_by_year per HCP
    pub_by_hcp: dict = {}
    for cid, year, count, cf in pub_rows:
        if cid not in pub_by_hcp:
            pub_by_hcp[cid] = {}
        pub_by_hcp[cid][year] = {"pub_count": count, **cf}

    result = {}
    for cid, pub_by_year in pub_by_hcp.items():
        if cid not in meta_map:
            continue
        meta  = meta_map[cid]
        digi  = digi_map.get(cid, {})
        result[cid] = {
            **meta,
            "digi_score":       digi.get("digi_score",       0.0),
            "otm":              digi.get("otm",               0.0),
            "vs":               digi.get("vs",                0.0),
            "social":           digi.get("social",            0.0),
            "sentiment_rating": digi.get("sentiment_rating",  ""),
            "pub_by_year":      pub_by_year,
        }
    return result


# ── Snowflake connection ───────────────────────────────────────────────────────

def connect_snowflake(aws_profile: str, warehouse: str, database: str):
    import boto3, snowflake.connector
    from cryptography.hazmat.primitives import serialization
    from shared.parameter_manager import ParameterManager
    from shared.secret_reader import SecretReader

    session = boto3.Session(profile_name=aws_profile, region_name="eu-central-1")
    pm = ParameterManager(session)
    secret_name = pm.get_snowflake_secret_name()
    secret = SecretReader().get_secret(secret_name, session)

    private_key_str = secret["private_key"].replace("\\n", "\n")
    private_key = serialization.load_pem_private_key(
        private_key_str.encode("utf-8"), password=None
    )
    private_key_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return snowflake.connector.connect(
        user=secret["user"], account=secret["account"],
        warehouse=warehouse, database=database,
        private_key=private_key_bytes,
    )


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    import argparse
    import snowflake.connector

    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true",
                        help="Overwrite existing kol_raw.json")
    args = parser.parse_args()

    cfg = configparser.ConfigParser()
    cfg.read(os.path.join(_DIR, "config.ini"))
    sf = cfg["snowflake"]

    out_path = os.path.join(_DIR, "data", "kol_raw.json")
    if os.path.exists(out_path) and not args.force:
        log.info("kol_raw.json already exists — skipping (use --force to overwrite)")
        return

    with open(os.path.join(_DIR, "data", "input.json"), encoding="utf-8") as f:
        inp = json.load(f)

    log.info("Connecting to Snowflake...")
    conn = connect_snowflake(sf["aws_profile"], sf["warehouse"], sf["database"])
    cur  = conn.cursor(snowflake.connector.DictCursor)

    log.info("Q1: fetching PCA topic terms...")
    cur.execute(build_pca_query(sf["schema_v1"]))
    pca_terms     = [{"term_key": r["TERM_KEY"], "term_en": r["TERM_EN"]}
                     for r in cur.fetchall()]
    pca_term_keys = [t["term_key"] for t in pca_terms]
    log.info(f"  {len(pca_terms)} PCA terms found")

    log.info("Q3: fetching publication counts (with inline relevance filter)...")
    cur.execute(build_pub_counts_query(sf["schema_v1"], sf["schema_final"], pca_term_keys))
    raw_pub_rows = cur.fetchall()
    pub_rows = [normalise_pub_row(r, pca_term_keys) for r in raw_pub_rows]
    log.info(f"  {len(pub_rows)} HCP×year rows returned")

    log.info("Q4: fetching digital scores...")
    cur.execute(build_digi_query(sf["schema_v1"]))
    digi_map = {r["S_CUSTOMER_ID"]: normalise_digi_row(r) for r in cur.fetchall()}

    log.info("Q5: fetching HCP metadata and quality rating...")
    cur.execute(build_hcp_meta_query(sf["schema_tmp"], sf["schema_final"]))
    meta_map = {r["S_CUSTOMER_ID"]: normalise_meta_row(r) for r in cur.fetchall()}

    cur.close()
    conn.close()

    hcps = merge_hcp_records(pub_rows, digi_map, meta_map, pca_term_keys)
    log.info(f"  {len(hcps)} HCPs merged")

    os.makedirs(os.path.join(_DIR, "data"), exist_ok=True)
    output = {
        "indication":  inp["indication"],
        "client_drug": inp["client_drug"],
        "pca_terms":   pca_terms,
        "hcps":        hcps,
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    log.info(f"Done. {len(hcps)} HCPs written to {out_path}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run all 01_fetch tests**

```bash
python -m pytest tests/test_01_fetch.py -v
```
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add b_kol_identification/01_fetch_kol_data.py b_kol_identification/tests/test_01_fetch.py
git commit -m "feat(kol): 01_fetch merge logic and main() with resume safety"
```

---

## Task 4: 02_score — pure scoring functions

**Files:**
- Create: `b_kol_identification/02_score_and_tier.py` (pure helpers only — no `main()` yet)
- Create: `b_kol_identification/tests/test_02_score.py`

**Interfaces:**
- Produces:
  - `minmax(values: list[float]) -> list[float]`
  - `validate_weights(weights: dict) -> None`  — raises ValueError if sum != 1.0
  - `compute_pub_totals(hcp: dict, current_year: int, window_years: int) -> dict`  — adds pub_count_total, pub_count_last_2yr, pub_count_pre_2yr, pub_by_year_simple
  - `compute_cf_totals(hcp: dict, pca_term_keys: list[str]) -> dict`  — adds cf_count_total, cf_by_term
  - `compute_composite(hcps: list[dict], weights: dict) -> list[dict]`  — adds norm_pub, norm_cf, norm_digi, composite_score
  - `assign_tiers(hcps: list[dict], tier_a_pct: float, tier_b_pct: float) -> list[dict]`  — adds tier
  - `flag_rising_stars(hcps: list[dict], min_pubs: int, growth_factor: float) -> list[dict]`  — adds rising_star bool
  - `assign_themes(hcp: dict, pca_terms: list[dict], top_n: int = 3) -> list[dict]`  — returns top_n theme dicts sorted by count

- [ ] **Step 1: Write the failing tests**

```python
# b_kol_identification/tests/test_02_score.py
import importlib.util, os, pytest

_SCRIPT = os.path.join(os.path.dirname(__file__), "..", "02_score_and_tier.py")
_spec = importlib.util.spec_from_file_location("score_and_tier", _SCRIPT)
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)

minmax            = mod.minmax
validate_weights  = mod.validate_weights
compute_pub_totals = mod.compute_pub_totals
compute_cf_totals = mod.compute_cf_totals
compute_composite = mod.compute_composite
assign_tiers      = mod.assign_tiers
flag_rising_stars = mod.flag_rising_stars
assign_themes     = mod.assign_themes


def test_minmax_normalises_to_unit_range():
    result = minmax([0.0, 5.0, 10.0])
    assert result == [0.0, 0.5, 1.0]


def test_minmax_constant_input_returns_zeros():
    assert minmax([7.0, 7.0, 7.0]) == [0.0, 0.0, 0.0]


def test_minmax_single_element():
    assert minmax([3.0]) == [0.0]


def test_validate_weights_passes_when_sum_is_one():
    validate_weights({"a": 0.45, "b": 0.30, "c": 0.25, "d": 0.00})


def test_validate_weights_raises_when_sum_is_not_one():
    with pytest.raises(ValueError):
        validate_weights({"a": 0.50, "b": 0.30, "c": 0.25, "d": 0.00})


def test_compute_pub_totals_sums_all_years():
    hcp = {
        "pub_by_year": {
            "2022": {"pub_count": 2},
            "2023": {"pub_count": 5},
            "2024": {"pub_count": 3},
        }
    }
    result = compute_pub_totals(hcp, current_year=2025, window_years=3)
    assert result["pub_count_total"] == 10


def test_compute_pub_totals_splits_recent_vs_prior():
    hcp = {
        "pub_by_year": {
            "2021": {"pub_count": 1},
            "2022": {"pub_count": 2},
            "2023": {"pub_count": 4},
            "2024": {"pub_count": 3},
        }
    }
    result = compute_pub_totals(hcp, current_year=2025, window_years=3)
    # recent = 2023, 2024 (last 2 years before current_year)
    assert result["pub_count_last_2yr"] == 7   # 4 + 3
    assert result["pub_count_pre_2yr"]  == 3   # 1 + 2


def test_compute_pub_totals_builds_simple_year_map():
    hcp = {"pub_by_year": {"2023": {"pub_count": 4, "CF_OBESITY": 3}}}
    result = compute_pub_totals(hcp, current_year=2025, window_years=3)
    assert result["pub_by_year_simple"] == {"2023": 4}


def test_compute_cf_totals_sums_across_years():
    pca_keys = ["CF_OBESITY", "CF_GLP_1_DRUG"]
    hcp = {
        "pub_by_year": {
            "2022": {"pub_count": 2, "CF_OBESITY": 2, "CF_GLP_1_DRUG": 1},
            "2023": {"pub_count": 3, "CF_OBESITY": 3, "CF_GLP_1_DRUG": 2},
        }
    }
    result = compute_cf_totals(hcp, pca_keys)
    assert result["cf_by_term"]["CF_OBESITY"] == 5
    assert result["cf_by_term"]["CF_GLP_1_DRUG"] == 3
    assert result["cf_count_total"] == 8


def test_compute_composite_scores_are_in_unit_range():
    weights = {"pub": 0.45, "cf": 0.30, "digi": 0.25, "sim": 0.00}
    hcps = [
        {"pub_count_total": 10, "cf_count_total": 8, "digi_score": 40.0},
        {"pub_count_total": 2,  "cf_count_total": 2, "digi_score": 5.0},
        {"pub_count_total": 5,  "cf_count_total": 5, "digi_score": 20.0},
    ]
    result = compute_composite(hcps, weights)
    for h in result:
        assert 0.0 <= h["composite_score"] <= 1.0


def test_compute_composite_highest_pub_count_ranks_first():
    weights = {"pub": 0.45, "cf": 0.30, "digi": 0.25, "sim": 0.00}
    hcps = [
        {"pub_count_total": 50, "cf_count_total": 1, "digi_score": 1.0},
        {"pub_count_total": 1,  "cf_count_total": 1, "digi_score": 1.0},
    ]
    result = compute_composite(hcps, weights)
    assert result[0]["composite_score"] > result[1]["composite_score"]


def test_assign_tiers_distributes_into_three_groups():
    hcps = [{"composite_score": float(i) / 9} for i in range(10)]
    result = assign_tiers(hcps, tier_a_pct=85, tier_b_pct=60)
    tiers = [h["tier"] for h in result]
    assert "A" in tiers
    assert "B" in tiers
    assert "C" in tiers


def test_assign_tiers_a_has_fewest_members():
    hcps = [{"composite_score": float(i) / 99} for i in range(100)]
    result = assign_tiers(hcps, tier_a_pct=85, tier_b_pct=60)
    assert sum(h["tier"] == "A" for h in result) < sum(h["tier"] == "C" for h in result)


def test_flag_rising_stars_new_voice_criterion():
    hcps = [
        {"pub_count_last_2yr": 4, "pub_count_pre_2yr": 0},
        {"pub_count_last_2yr": 1, "pub_count_pre_2yr": 5},
    ]
    result = flag_rising_stars(hcps, min_pubs=3, growth_factor=3.0)
    assert result[0]["rising_star"] is True
    assert result[1]["rising_star"] is False


def test_flag_rising_stars_growth_criterion():
    hcps = [
        {"pub_count_last_2yr": 9, "pub_count_pre_2yr": 2},
        {"pub_count_last_2yr": 5, "pub_count_pre_2yr": 4},
    ]
    result = flag_rising_stars(hcps, min_pubs=3, growth_factor=3.0)
    assert result[0]["rising_star"] is True
    assert result[1]["rising_star"] is False


def test_assign_themes_returns_top_n_sorted_by_count():
    hcp = {"cf_by_term": {"CF_OBESITY": 6, "CF_GLP_1_DRUG": 4, "CF_WEGOVY": 1, "CF_MOUNJARO": 8}}
    pca_terms = [
        {"term_key": "CF_OBESITY",   "term_en": "Obesity"},
        {"term_key": "CF_GLP_1_DRUG","term_en": "GLP-1 Drug"},
        {"term_key": "CF_WEGOVY",    "term_en": "Wegovy"},
        {"term_key": "CF_MOUNJARO",  "term_en": "Mounjaro"},
    ]
    result = assign_themes(hcp, pca_terms, top_n=3)
    assert len(result) == 3
    assert result[0]["term_key"] == "CF_MOUNJARO"   # highest count
    assert result[1]["term_key"] == "CF_OBESITY"


def test_assign_themes_handles_missing_cf_by_term():
    hcp = {}
    pca_terms = [{"term_key": "CF_OBESITY", "term_en": "Obesity"}]
    result = assign_themes(hcp, pca_terms, top_n=3)
    assert result == []
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_02_score.py -v 2>&1 | head -10
```
Expected: import error — file not yet created.

- [ ] **Step 3: Implement pure scoring helpers in `02_score_and_tier.py`**

```python
"""
Stage 02: Score and tier KOLs.

Reads:  data/kol_raw.json
Writes: data/kol_scored.json
"""
import configparser, json, logging, os, sys
from datetime import datetime

logging.basicConfig(format="%(asctime)s [%(levelname)s] %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)
_DIR = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(_DIR, ".."))


# ── Pure helpers ───────────────────────────────────────────────────────────────

def minmax(values: list) -> list:
    lo, hi = min(values), max(values)
    if hi == lo:
        return [0.0] * len(values)
    return [(v - lo) / (hi - lo) for v in values]


def validate_weights(weights: dict) -> None:
    total = round(sum(weights.values()), 10)
    if abs(total - 1.0) > 1e-6:
        raise ValueError(f"Scoring weights must sum to 1.0, got {total}")


def compute_pub_totals(hcp: dict, current_year: int, window_years: int) -> dict:
    pub_by_year = hcp.get("pub_by_year", {})
    recent_cutoff = current_year - 2   # last 2 full years
    total, last_2, pre_2 = 0, 0, 0
    simple = {}
    for yr, data in pub_by_year.items():
        count = data.get("pub_count", 0) if isinstance(data, dict) else int(data)
        total += count
        simple[yr] = count
        if int(yr) >= recent_cutoff:
            last_2 += count
        else:
            pre_2 += count
    return {
        **hcp,
        "pub_count_total":    total,
        "pub_count_last_2yr": last_2,
        "pub_count_pre_2yr":  pre_2,
        "pub_by_year_simple": simple,
    }


def compute_cf_totals(hcp: dict, pca_term_keys: list) -> dict:
    pub_by_year = hcp.get("pub_by_year", {})
    cf_by_term = {tk: 0 for tk in pca_term_keys}
    for data in pub_by_year.values():
        if not isinstance(data, dict):
            continue
        for tk in pca_term_keys:
            cf_by_term[tk] += int(data.get(tk, 0))
    cf_count_total = sum(cf_by_term.values())
    return {**hcp, "cf_by_term": cf_by_term, "cf_count_total": cf_count_total}


def compute_composite(hcps: list, weights: dict) -> list:
    pubs   = minmax([h.get("pub_count_total", 0) for h in hcps])
    cfs    = minmax([h.get("cf_count_total",  0) for h in hcps])
    digis  = minmax([h.get("digi_score",      0.0) for h in hcps])
    sims   = minmax([h.get("sim_score",       0.0) for h in hcps])
    result = []
    for i, h in enumerate(hcps):
        score = (weights["pub"]  * pubs[i]
               + weights["cf"]   * cfs[i]
               + weights["digi"] * digis[i]
               + weights["sim"]  * sims[i])
        result.append({
            **h,
            "norm_pub":        round(pubs[i],  4),
            "norm_cf":         round(cfs[i],   4),
            "norm_digi":       round(digis[i], 4),
            "composite_score": round(score,    6),
        })
    return result


def assign_tiers(hcps: list, tier_a_pct: float, tier_b_pct: float) -> list:
    if not hcps:
        return []
    scores = sorted(h["composite_score"] for h in hcps)
    n = len(scores)
    thresh_a = scores[int(n * tier_a_pct / 100)]
    thresh_b = scores[int(n * tier_b_pct / 100)]
    result = []
    for h in hcps:
        s = h["composite_score"]
        tier = "A" if s >= thresh_a else ("B" if s >= thresh_b else "C")
        result.append({**h, "tier": tier})
    return result


def flag_rising_stars(hcps: list, min_pubs: int, growth_factor: float) -> list:
    result = []
    for h in hcps:
        last = h.get("pub_count_last_2yr", 0)
        prev = h.get("pub_count_pre_2yr",  0)
        new_voice    = last >= min_pubs and prev == 0
        accelerating = (last / max(prev, 1)) >= growth_factor and last >= min_pubs
        result.append({**h, "rising_star": new_voice or accelerating})
    return result


def assign_themes(hcp: dict, pca_terms: list, top_n: int = 3) -> list:
    cf_by_term = hcp.get("cf_by_term", {})
    if not cf_by_term:
        return []
    ranked = sorted(
        [{"term_key": t["term_key"], "term_en": t["term_en"],
          "count": cf_by_term.get(t["term_key"], 0)}
         for t in pca_terms],
        key=lambda x: x["count"], reverse=True,
    )
    return [t for t in ranked[:top_n] if t["count"] > 0]
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/test_02_score.py -v
```
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add b_kol_identification/02_score_and_tier.py b_kol_identification/tests/test_02_score.py
git commit -m "feat(kol): 02_score pure scoring helpers with tests"
```

---

## Task 5: 02_score — Q6 co-authorship query and `main()`

**Files:**
- Modify: `b_kol_identification/02_score_and_tier.py` (add `build_coauth_query`, `main`)
- Modify: `b_kol_identification/tests/test_02_score.py` (add Q6 SQL test)

**Interfaces:**
- Consumes: scoring helpers from Task 4; `connect_snowflake` from `01_fetch_kol_data.py`
- Produces:
  - `build_coauth_query(schema_v1: str, schema_final: str, top_ids: list[str]) -> str`

- [ ] **Step 1: Add Q6 test**

Append to `b_kol_identification/tests/test_02_score.py`:

```python
build_coauth_query = mod.build_coauth_query


def test_build_coauth_query_requires_at_least_one_top_id():
    sql = build_coauth_query("MY_V1", "MY_FINAL", ["10", "20"])
    assert "MY_V1.PUBMED_CONTENT_FRAME_SINGLE" in sql
    assert "MY_FINAL.PUBMED_SCORED" in sql
    assert "shared_pmids" in sql.lower()
    # Both IDs appear as filter values
    assert "'10'" in sql
    assert "'20'" in sql


def test_build_coauth_query_uses_or_condition():
    sql = build_coauth_query("MY_V1", "MY_FINAL", ["10"])
    # Must use OR so non-top-50 co-authors are included
    assert " OR " in sql.upper()
```

- [ ] **Step 2: Run to verify failure**

```bash
python -m pytest tests/test_02_score.py::test_build_coauth_query_requires_at_least_one_top_id -v
```
Expected: `AttributeError: module has no attribute 'build_coauth_query'`

- [ ] **Step 3: Implement `build_coauth_query` and `main()`**

Append to `b_kol_identification/02_score_and_tier.py`:

```python
# ── Co-authorship query ────────────────────────────────────────────────────────

def build_coauth_query(schema_v1: str, schema_final: str, top_ids: list) -> str:
    ids_sql = ", ".join(f"'{i}'" for i in top_ids)
    return f"""
SELECT a.S_CUSTOMER_ID AS hcp_a,
       b.S_CUSTOMER_ID AS hcp_b,
       COUNT(DISTINCT a.PMID) AS shared_pmids
FROM {schema_v1}.PUBMED_CONTENT_FRAME_SINGLE a
JOIN {schema_v1}.PUBMED_CONTENT_FRAME_SINGLE b
    ON  a.PMID = b.PMID
    AND a.S_CUSTOMER_ID < b.S_CUSTOMER_ID
INNER JOIN (
    WITH pct AS (
        SELECT PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY MAX_SIM) AS p75
        FROM {schema_final}.PUBMED_SCORED
    )
    SELECT s.PMID FROM {schema_final}.PUBMED_SCORED s
    CROSS JOIN pct WHERE s.MAX_SIM >= pct.p75
) AS relevant ON a.PMID = relevant.PMID
WHERE (   a.S_CUSTOMER_ID IN ({ids_sql})
       OR b.S_CUSTOMER_ID IN ({ids_sql}) )
GROUP BY a.S_CUSTOMER_ID, b.S_CUSTOMER_ID
HAVING COUNT(DISTINCT a.PMID) >= 2
""".strip()


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    import argparse, snowflake.connector
    from b_kol_identification.e01_fetch_kol_data import connect_snowflake  # reuse

    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    cfg = configparser.ConfigParser()
    cfg.read(os.path.join(_DIR, "config.ini"))
    sf  = cfg["snowflake"]
    sc  = cfg["scoring"]

    out_path = os.path.join(_DIR, "data", "kol_scored.json")
    if os.path.exists(out_path) and not args.force:
        log.info("kol_scored.json already exists — skipping (use --force to overwrite)")
        return

    raw_path = os.path.join(_DIR, "data", "kol_raw.json")
    with open(raw_path, encoding="utf-8") as f:
        raw = json.load(f)

    pca_terms     = raw["pca_terms"]
    pca_term_keys = [t["term_key"] for t in pca_terms]
    current_year  = datetime.now().year
    window_years  = int(sc.get("data_window_years", 3))

    weights = {
        "pub":  float(sc["weight_pub_count"]),
        "cf":   float(sc["weight_cf_count"]),
        "digi": float(sc["weight_digi_score"]),
        "sim":  float(sc["weight_sim_score"]),
    }
    validate_weights(weights)

    # Build list of HCP dicts, adding all computed fields
    hcps = list(raw["hcps"].values())
    hcps = [compute_pub_totals(h, current_year, window_years) for h in hcps]
    hcps = [compute_cf_totals(h, pca_term_keys) for h in hcps]
    hcps = compute_composite(hcps, weights)
    hcps = assign_tiers(hcps, float(sc["tier_a_percentile"]),
                                float(sc["tier_b_percentile"]))
    hcps = flag_rising_stars(hcps, int(sc["rising_star_min_pubs"]),
                                    float(sc["rising_star_growth"]))
    for h in hcps:
        h["theme_labels"] = assign_themes(h, pca_terms)

    hcps.sort(key=lambda h: h["composite_score"], reverse=True)

    top_n   = int(sf.get("top_n_coauth", 50))
    top_ids = [h["s_customer_id"] for h in hcps[:top_n]]

    log.info(f"Running Q6 co-authorship for top-{top_n} KOLs...")
    conn = connect_snowflake(sf["aws_profile"], sf["warehouse"], sf["database"])
    cur  = conn.cursor(snowflake.connector.DictCursor)
    cur.execute(build_coauth_query(sf["schema_v1"], sf["schema_final"], top_ids))
    coauth_edges = [
        {
            "hcp_a":        str(r.get("HCP_A") or r.get("hcp_a", "")),
            "hcp_b":        str(r.get("HCP_B") or r.get("hcp_b", "")),
            "shared_pmids": int(r.get("SHARED_PMIDS") or r.get("shared_pmids", 0)),
        }
        for r in cur.fetchall()
    ]
    cur.close()
    conn.close()
    log.info(f"  {len(coauth_edges)} co-authorship edges found")

    output = {
        "indication":   raw["indication"],
        "client_drug":  raw["client_drug"],
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "pca_terms":    pca_terms,
        "hcps":         hcps,
        "coauth_edges": coauth_edges,
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    log.info(f"Done. {len(hcps)} KOLs scored → {out_path}")


if __name__ == "__main__":
    main()
```

> **Note on import in `main()`:** Change `from b_kol_identification.e01_fetch_kol_data import connect_snowflake` to the actual relative import path once the directory name is confirmed. Alternatively, copy `connect_snowflake` into a shared `snowflake_utils.py` in the parent `shared/` folder.

- [ ] **Step 4: Run all 02_score tests**

```bash
python -m pytest tests/test_02_score.py -v
```
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add b_kol_identification/02_score_and_tier.py b_kol_identification/tests/test_02_score.py
git commit -m "feat(kol): 02_score Q6 co-authorship query and main()"
```

---

## Task 6: 03_report — section renderers and `main()`

**Files:**
- Create: `b_kol_identification/03_generate_report.py`
- Create: `b_kol_identification/tests/test_03_report.py`

**Interfaces:**
- Consumes: `kol_scored.json` (data contract defined above)
- Produces:
  - `render_stat_cards(data: dict) -> str`  — HTML
  - `render_kol_table(hcps: list, pca_terms: list) -> str`  — HTML table
  - `render_rising_stars(hcps: list, all_years: list) -> str`  — HTML cards with sparklines
  - `render_sparkline(pub_by_year_simple: dict, all_years: list) -> str`  — inline SVG
  - `render_timeline_svg(hcps: list, all_years: list) -> str`  — inline SVG bar + line chart
  - `render_thematic_heatmap(hcps: list, pca_terms: list) -> str`  — HTML table with colour cells
  - `render_regional_table(hcps: list) -> str`  — HTML table
  - `render_coauth_network(edges: list, hcps: list) -> str`  — inline SVG circular layout
  - `render_kol_profiles(hcps: list, all_years: list) -> str`  — HTML profile cards
  - `build_report_html(data: dict) -> str`  — full HTML string

- [ ] **Step 1: Write failing tests**

```python
# b_kol_identification/tests/test_03_report.py
import importlib.util, os
import pytest

_SCRIPT = os.path.join(os.path.dirname(__file__), "..", "03_generate_report.py")
_spec = importlib.util.spec_from_file_location("generate_report", _SCRIPT)
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)

render_stat_cards     = mod.render_stat_cards
render_kol_table      = mod.render_kol_table
render_rising_stars   = mod.render_rising_stars
render_sparkline      = mod.render_sparkline
render_timeline_svg   = mod.render_timeline_svg
render_thematic_heatmap = mod.render_thematic_heatmap
render_regional_table = mod.render_regional_table
render_coauth_network = mod.render_coauth_network
render_kol_profiles   = mod.render_kol_profiles
build_report_html     = mod.build_report_html


_HCPS = [
    {
        "s_customer_id": "1", "name": "Anna Berg", "city": "Berlin",
        "specialty": "Innere Medizin", "rating": "A", "digi_score": 40.0,
        "pub_count_total": 10, "pub_count_last_2yr": 6, "pub_count_pre_2yr": 4,
        "cf_count_total": 8, "cf_by_term": {"CF_OBESITY": 5, "CF_GLP_1_DRUG": 3},
        "pub_by_year_simple": {"2022": 4, "2023": 6},
        "composite_score": 0.91, "norm_pub": 1.0, "norm_cf": 1.0, "norm_digi": 1.0,
        "tier": "A", "rising_star": False,
        "theme_labels": [{"term_key": "CF_OBESITY", "term_en": "Obesity", "count": 5}],
    },
    {
        "s_customer_id": "2", "name": "Karl Neu", "city": "Hamburg",
        "specialty": "Chirurgie", "rating": "B", "digi_score": 10.0,
        "pub_count_total": 3, "pub_count_last_2yr": 3, "pub_count_pre_2yr": 0,
        "cf_count_total": 2, "cf_by_term": {"CF_OBESITY": 2, "CF_GLP_1_DRUG": 0},
        "pub_by_year_simple": {"2023": 3},
        "composite_score": 0.45, "norm_pub": 0.5, "norm_cf": 0.3, "norm_digi": 0.2,
        "tier": "C", "rising_star": True,
        "theme_labels": [{"term_key": "CF_OBESITY", "term_en": "Obesity", "count": 2}],
    },
]
_PCA_TERMS = [
    {"term_key": "CF_OBESITY",    "term_en": "Obesity"},
    {"term_key": "CF_GLP_1_DRUG", "term_en": "GLP-1 Drug"},
]
_YEARS = ["2022", "2023"]
_EDGES = [{"hcp_a": "1", "hcp_b": "2", "shared_pmids": 3}]
_DATA  = {
    "indication": "Obesity", "client_drug": "Ozempic",
    "generated_at": "2026-06-25T10:00:00",
    "pca_terms": _PCA_TERMS, "hcps": _HCPS, "coauth_edges": _EDGES,
}


def test_render_stat_cards_shows_total_kol_count():
    html = render_stat_cards(_DATA)
    assert "2" in html   # total KOLs
    assert "Tier A" in html


def test_render_stat_cards_shows_rising_star_count():
    html = render_stat_cards(_DATA)
    assert "Rising Star" in html
    assert "1" in html


def test_render_kol_table_contains_hcp_names():
    html = render_kol_table(_HCPS, _PCA_TERMS)
    assert "Anna Berg" in html
    assert "Karl Neu" in html


def test_render_kol_table_shows_tier_badges():
    html = render_kol_table(_HCPS, _PCA_TERMS)
    assert "Tier A" in html or ">A<" in html


def test_render_rising_stars_only_shows_flagged_hcps():
    html = render_rising_stars(_HCPS, _YEARS)
    assert "Karl Neu" in html
    assert "Anna Berg" not in html


def test_render_sparkline_returns_valid_svg():
    svg = render_sparkline({"2022": 4, "2023": 6}, _YEARS)
    assert svg.startswith("<svg")
    assert svg.endswith("</svg>")
    assert "<rect" in svg


def test_render_sparkline_empty_data_returns_svg():
    svg = render_sparkline({}, _YEARS)
    assert "<svg" in svg


def test_render_timeline_svg_returns_valid_svg():
    svg = render_timeline_svg(_HCPS, _YEARS)
    assert "<svg" in svg
    assert "<rect" in svg   # bars


def test_render_thematic_heatmap_contains_pca_term_labels():
    html = render_thematic_heatmap(_HCPS, _PCA_TERMS)
    assert "Obesity" in html
    assert "GLP-1 Drug" in html
    assert "Anna Berg" in html


def test_render_regional_table_groups_by_city():
    html = render_regional_table(_HCPS)
    assert "Berlin" in html
    assert "Hamburg" in html


def test_render_coauth_network_returns_svg_with_nodes():
    svg = render_coauth_network(_EDGES, _HCPS)
    assert "<svg" in svg
    assert "<circle" in svg


def test_render_kol_profiles_renders_top_hcps():
    html = render_kol_profiles(_HCPS, _YEARS)
    assert "Anna Berg" in html
    assert "Obesity" in html


def test_build_report_html_is_self_contained():
    html = build_report_html(_DATA)
    assert "<html" in html
    assert "<style" in html
    assert "Anna Berg" in html
    # No external resource references
    assert "cdn." not in html
    assert "fonts.googleapis" not in html
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_03_report.py -v 2>&1 | head -10
```
Expected: import error — file not yet created.

- [ ] **Step 3: Implement `03_generate_report.py`**

```python
"""
Stage 03: Generate self-contained HTML KOL report.

Reads:  data/kol_scored.json
Writes: results/kol_report_{timestamp}.html
"""
import configparser, json, logging, math, os
from datetime import datetime

logging.basicConfig(format="%(asctime)s [%(levelname)s] %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)
_DIR = os.path.dirname(__file__)

_TIER_COLORS = {"A": "#B07D0E", "B": "#3D5E8A", "C": "#5E7858"}
_ACCENT      = "#1558A8"
_RISING_CLR  = "#7530AA"

_CSS = """
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{--accent:#1558A8;--accent-pale:#E4EEFF;--ground:#F4F7FF;
  --surface:#fff;--rule:#C8D5EE;--ink:#18274A;--ink-soft:#445A80;
  --tier-a:#B07D0E;--tier-b:#3D5E8A;--tier-c:#5E7858;--rising:#7530AA}
body{font-family:'Segoe UI',system-ui,sans-serif;background:var(--ground);
  color:var(--ink);padding:2rem;line-height:1.55}
h1{font-size:1.7rem;font-weight:700;margin-bottom:.25rem}
h2{font-size:1.2rem;font-weight:600;margin:2rem 0 .9rem;color:var(--ink);
  border-left:4px solid var(--accent);padding-left:.75rem}
h3{font-size:.95rem;font-weight:600;margin:.5rem 0 .4rem}
.meta{color:var(--ink-soft);font-size:.82rem;margin-bottom:2rem}
.card{background:var(--surface);border:1px solid var(--rule);
  border-radius:4px;padding:1.5rem;margin-bottom:1.5rem}
.stat-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));
  gap:.9rem;margin:1rem 0}
.stat-card{background:var(--accent-pale);border-radius:3px;padding:1rem;text-align:center}
.stat-num{font-size:2rem;font-weight:700;color:var(--accent)}
.stat-lbl{font-size:.75rem;color:var(--ink-soft);margin-top:.2rem}
table{width:100%;border-collapse:collapse;font-size:.82rem}
thead th{background:var(--accent);color:#fff;padding:.55rem .75rem;
  text-align:left;font-size:.7rem;letter-spacing:.07em;text-transform:uppercase;
  white-space:nowrap}
tbody td{padding:.6rem .75rem;border-bottom:1px solid var(--rule);vertical-align:top}
tbody tr:nth-child(even) td{background:var(--ground)}
tbody tr:hover td{background:var(--accent-pale)}
.badge{display:inline-block;padding:.15rem .55rem;border-radius:2px;
  font-size:.72rem;font-weight:700;color:#fff;white-space:nowrap}
.badge-a{background:var(--tier-a)}.badge-b{background:var(--tier-b)}
.badge-c{background:var(--tier-c)}.badge-r{background:var(--rising)}
.tag{display:inline-block;background:var(--accent-pale);color:var(--accent);
  padding:.1rem .45rem;border-radius:2px;font-size:.7rem;margin:.1rem .15rem 0 0}
.score-bar-track{background:var(--rule);border-radius:1px;height:6px;
  display:inline-block;width:80px;vertical-align:middle}
.score-bar-fill{height:100%;border-radius:1px;background:var(--accent)}
.rising-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:1rem}
.rising-card{background:var(--surface);border:1px solid var(--rule);
  border-top:3px solid var(--rising);padding:1rem}
.hmap-wrap{overflow-x:auto}
.hmap-wrap table th{writing-mode:vertical-rl;transform:rotate(180deg);
  font-size:.65rem;padding:.4rem .3rem;min-width:32px}
.profile-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:1rem}
.profile-card{background:var(--surface);border:1px solid var(--rule);padding:1.1rem}
.neo4j-note{background:var(--accent-pale);border:1px solid var(--rule);
  padding:.85rem 1rem;font-size:.83rem;color:var(--ink-soft);margin-top:.75rem}
"""


# ── Section renderers ──────────────────────────────────────────────────────────

def render_stat_cards(data: dict) -> str:
    hcps  = data.get("hcps", [])
    total = len(hcps)
    a     = sum(1 for h in hcps if h.get("tier") == "A")
    b     = sum(1 for h in hcps if h.get("tier") == "B")
    c     = sum(1 for h in hcps if h.get("tier") == "C")
    stars = sum(1 for h in hcps if h.get("rising_star"))
    total_pubs = sum(h.get("pub_count_total", 0) for h in hcps)
    cards = [
        (str(total),      "KOLs Identified"),
        (str(a),          "Tier A"),
        (str(b),          "Tier B"),
        (str(c),          "Tier C"),
        (str(stars),      "Rising Stars"),
        (f"{total_pubs:,}", "Relevant Publications"),
    ]
    inner = "".join(
        f'<div class="stat-card"><div class="stat-num">{n}</div>'
        f'<div class="stat-lbl">{l}</div></div>'
        for n, l in cards
    )
    return f'<div class="card"><h2>Executive Dashboard</h2><div class="stat-grid">{inner}</div></div>'


def render_kol_table(hcps: list, pca_terms: list) -> str:
    rows = []
    for h in hcps:
        tier   = h.get("tier", "C")
        color  = _TIER_COLORS.get(tier, _TIER_COLORS["C"])
        badge  = f'<span class="badge badge-{tier.lower()}">{tier}</span>'
        star   = ' <span class="badge badge-r">⭐ Rising</span>' if h.get("rising_star") else ""
        score  = h.get("composite_score", 0)
        bar_w  = int(score * 100)
        bar    = (f'<div class="score-bar-track"><div class="score-bar-fill" '
                  f'style="width:{bar_w}%"></div></div> <small>{score:.2f}</small>')
        themes = "".join(
            f'<span class="tag">{t["term_en"]}</span>'
            for t in h.get("theme_labels", [])
        )
        rows.append(
            f'<tr><td>{badge}{star}</td>'
            f'<td><b>{h.get("name","")}</b><br><small>{h.get("specialty","")}</small></td>'
            f'<td>{h.get("city","")}</td><td>{bar}</td>'
            f'<td>{h.get("pub_count_total",0)}</td>'
            f'<td>{h.get("digi_score",0):.0f}</td>'
            f'<td>{themes}</td></tr>'
        )
    header = ("<tr><th>Tier</th><th>Name / Specialty</th><th>City</th>"
              "<th>Score</th><th>Pubs</th><th>Digi</th><th>Themes</th></tr>")
    body   = "".join(rows)
    return (f'<div class="card"><h2>KOL Ranking</h2>'
            f'<div style="overflow-x:auto"><table><thead>{header}</thead>'
            f'<tbody>{body}</tbody></table></div></div>')


def render_sparkline(pub_by_year_simple: dict, all_years: list,
                     width: int = 80, height: int = 24) -> str:
    counts = [pub_by_year_simple.get(y, 0) for y in all_years]
    max_v  = max(counts, default=0) or 1
    bw     = width / max(len(all_years), 1) - 1
    bars   = []
    for i, c in enumerate(counts):
        bh = max(2.0, c / max_v * (height - 4))
        x  = i * (width / len(all_years))
        y  = height - bh
        bars.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw:.1f}" '
                    f'height="{bh:.1f}" fill="{_ACCENT}"/>')
    return (f'<svg width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}">{"".join(bars)}</svg>')


def render_rising_stars(hcps: list, all_years: list) -> str:
    stars = [h for h in hcps if h.get("rising_star")]
    if not stars:
        return ""
    cards = []
    for h in stars:
        last  = h.get("pub_count_last_2yr", 0)
        prev  = h.get("pub_count_pre_2yr",  0)
        ratio = f"{last / max(prev, 1):.1f}×" if prev > 0 else "New voice"
        spark = render_sparkline(h.get("pub_by_year_simple", {}), all_years)
        themes = "".join(
            f'<span class="tag">{t["term_en"]}</span>'
            for t in h.get("theme_labels", [])
        )
        cards.append(
            f'<div class="rising-card">'
            f'<b>{h.get("name","")}</b> &nbsp; <span class="badge badge-r">Rising Star</span><br>'
            f'<small style="color:var(--ink-soft)">{h.get("specialty","")} · {h.get("city","")}</small><br>'
            f'<div style="margin:.5rem 0">{spark}</div>'
            f'<small><b>{last}</b> pubs (last 2 yr) vs <b>{prev}</b> prior &nbsp;·&nbsp; {ratio}</small><br>'
            f'<div style="margin-top:.4rem">{themes}</div>'
            f'</div>'
        )
    inner = f'<div class="rising-grid">{"".join(cards)}</div>'
    return f'<div class="card"><h2>Rising Stars</h2>{inner}</div>'


def render_timeline_svg(hcps: list, all_years: list,
                        W: int = 680, H: int = 200) -> str:
    ml, mr, mt, mb = 40, 20, 20, 30
    iw, ih = W - ml - mr, H - mt - mb
    # Corpus totals per year
    totals = {y: sum(h.get("pub_by_year_simple", {}).get(y, 0) for h in hcps)
              for y in all_years}
    max_v  = max(totals.values(), default=1) or 1
    n      = len(all_years)
    bw     = (iw / n) - 2 if n else iw

    bars = []
    for i, yr in enumerate(all_years):
        count = totals.get(yr, 0)
        bh = count / max_v * ih
        x  = ml + i * (iw / n)
        y  = mt + ih - bh
        bars.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw:.1f}" '
                    f'height="{bh:.1f}" fill="#C8D5EE"/>')
        bars.append(f'<text x="{x + bw/2:.1f}" y="{H - 5}" '
                    f'text-anchor="middle" font-size="10" fill="#445A80">{yr}</text>')

    LINE_COLORS = ["#1558A8", "#B07D0E", "#3D5E8A", "#7530AA", "#C0392B"]
    lines = []
    for j, kol in enumerate(hcps[:5]):
        pts = []
        for i, yr in enumerate(all_years):
            c = kol.get("pub_by_year_simple", {}).get(yr, 0)
            x = ml + i * (iw / n) + bw / 2
            y = mt + ih - (c / max_v * ih)
            pts.append(f"{x:.1f},{y:.1f}")
        if pts:
            lines.append(
                f'<polyline points="{" ".join(pts)}" fill="none" '
                f'stroke="{LINE_COLORS[j % 5]}" stroke-width="2" opacity=".8"/>'
            )

    return (f'<svg width="{W}" height="{H}" viewBox="0 0 {W} {H}">'
            + "".join(bars + lines) + "</svg>")


def render_thematic_heatmap(hcps: list, pca_terms: list, top_n: int = 20) -> str:
    top   = hcps[:top_n]
    keys  = [t["term_key"] for t in pca_terms]
    col_max = {k: max((h.get("cf_by_term", {}).get(k, 0) for h in top), default=1) or 1
               for k in keys}
    headers = "".join(
        f'<th title="{t["term_en"]}">{t["term_en"]}</th>'
        for t in pca_terms
    )
    rows = []
    for h in top:
        tier  = h.get("tier", "C")
        badge = f'<span class="badge badge-{tier.lower()}">{tier}</span>'
        cells = ""
        for k in keys:
            count = h.get("cf_by_term", {}).get(k, 0)
            alpha = round(count / col_max[k] * 0.75, 2)
            bg    = f"rgba(21,88,168,{alpha})" if count else "transparent"
            cells += (f'<td style="text-align:center;background:{bg};'
                      f'font-size:.7rem;color:var(--ink)">'
                      f'{"" if count == 0 else count}</td>')
        rows.append(
            f'<tr><td style="white-space:nowrap;font-size:.78rem">'
            f'{h.get("name","")}</td><td>{badge}</td>{cells}</tr>'
        )
    table = (f'<table><thead><tr><th>KOL</th><th>Tier</th>{headers}</tr></thead>'
             f'<tbody>{"".join(rows)}</tbody></table>')
    return f'<div class="card"><h2>Thematic Distribution</h2><div class="hmap-wrap">{table}</div></div>'


def render_regional_table(hcps: list) -> str:
    from collections import Counter, defaultdict
    city_data: dict = defaultdict(lambda: {"total": 0, "A": 0, "B": 0, "C": 0,
                                            "specialties": []})
    for h in hcps:
        city = h.get("city", "Unknown")
        city_data[city]["total"] += 1
        city_data[city][h.get("tier", "C")] += 1
        city_data[city]["specialties"].append(h.get("specialty", ""))

    rows = []
    for city, d in sorted(city_data.items(), key=lambda x: -x[1]["total"]):
        top_spec = Counter(s for s in d["specialties"] if s).most_common(1)
        spec_lbl = top_spec[0][0] if top_spec else "—"
        rows.append(
            f'<tr><td><b>{city}</b></td><td>{d["total"]}</td>'
            f'<td><span class="badge badge-a">{d["A"]}</span> '
            f'<span class="badge badge-b">{d["B"]}</span> '
            f'<span class="badge badge-c">{d["C"]}</span></td>'
            f'<td><small>{spec_lbl}</small></td></tr>'
        )
    header = "<tr><th>City</th><th>KOLs</th><th>Tier A/B/C</th><th>Top Specialty</th></tr>"
    table  = (f'<table><thead>{header}</thead>'
              f'<tbody>{"".join(rows)}</tbody></table>')
    return f'<div class="card"><h2>Regional Distribution</h2>{table}</div>'


def render_coauth_network(edges: list, hcps: list, top_n: int = 30) -> str:
    top_ids   = {h["s_customer_id"] for h in hcps[:top_n]}
    hcp_lookup = {h["s_customer_id"]: h for h in hcps}

    # Collect all node IDs appearing in edges (top + their collaborators)
    node_ids: set = set()
    for e in edges:
        if e["hcp_a"] in top_ids or e["hcp_b"] in top_ids:
            node_ids.add(e["hcp_a"])
            node_ids.add(e["hcp_b"])
    node_ids |= top_ids
    nodes = list(node_ids)
    n     = len(nodes)
    W, H  = 640, 420
    cx, cy, R = W // 2, H // 2, min(W, H) // 2 - 50

    pos = {}
    for i, nid in enumerate(nodes):
        angle   = 2 * math.pi * i / max(n, 1)
        pos[nid] = (cx + R * math.cos(angle), cy + R * math.sin(angle))

    svgs = []
    for e in edges:
        if e["hcp_a"] not in pos or e["hcp_b"] not in pos:
            continue
        x1, y1 = pos[e["hcp_a"]]
        x2, y2 = pos[e["hcp_b"]]
        sw = min(4, max(1, e["shared_pmids"]))
        svgs.append(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
                    f'stroke="#C8D5EE" stroke-width="{sw}"/>')

    for nid, (x, y) in pos.items():
        h      = hcp_lookup.get(nid)
        tier   = h["tier"] if h else "C"
        color  = _TIER_COLORS.get(tier, _TIER_COLORS["C"])
        r_node = 9 if nid in top_ids else 5
        label  = (h["name"][:20] if h else nid[:12]).replace("&", "&amp;")
        svgs.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r_node}" '
                    f'fill="{color}" stroke="white" stroke-width="1.5">'
                    f'<title>{label}</title></circle>')

    network_svg = (f'<svg width="{W}" height="{H}" viewBox="0 0 {W} {H}">'
                   + "".join(svgs) + "</svg>")
    neo4j_note = (
        '<div class="neo4j-note">'
        '<b>Full network analysis</b> — cluster detection, influence paths, and '
        'community identification across the full HCP graph is planned via the '
        'Neo4j knowledge graph integration. The preview above shows first-order '
        'co-authorship relationships for the top-30 KOLs and their collaborators.'
        '</div>'
    )
    return (f'<div class="card"><h2>Co-authorship Network Preview</h2>'
            f'{network_svg}{neo4j_note}</div>')


def render_kol_profiles(hcps: list, all_years: list, top_n: int = 20) -> str:
    cards = []
    for h in hcps[:top_n]:
        tier   = h.get("tier", "C")
        badge  = f'<span class="badge badge-{tier.lower()}">{tier}</span>'
        star   = ' <span class="badge badge-r">⭐ Rising</span>' if h.get("rising_star") else ""
        spark  = render_sparkline(h.get("pub_by_year_simple", {}), all_years,
                                  width=100, height=28)
        themes = "".join(
            f'<span class="tag">{t["term_en"]}</span>'
            for t in h.get("theme_labels", [])
        )
        score_rows = (
            f'<small style="color:var(--ink-soft)">'
            f'Pub {h.get("norm_pub",0):.2f} · '
            f'CF {h.get("norm_cf",0):.2f} · '
            f'Digi {h.get("norm_digi",0):.2f} · '
            f'Score <b>{h.get("composite_score",0):.2f}</b></small>'
        )
        cards.append(
            f'<div class="profile-card">'
            f'<div style="display:flex;justify-content:space-between;align-items:flex-start">'
            f'<div><b>{h.get("name","")}</b><br>'
            f'<small style="color:var(--ink-soft)">'
            f'{h.get("specialty","")} · {h.get("city","")}</small></div>'
            f'<div>{badge}{star}</div></div>'
            f'<div style="margin:.6rem 0">{spark}</div>'
            f'{score_rows}<br>'
            f'<div style="margin-top:.4rem">{themes}</div>'
            f'</div>'
        )
    return (f'<div class="card"><h2>Individual KOL Profiles</h2>'
            f'<div class="profile-grid">{"".join(cards)}</div></div>')


# ── Full report assembly ───────────────────────────────────────────────────────

def build_report_html(data: dict) -> str:
    hcps      = data.get("hcps", [])
    pca_terms = data.get("pca_terms", [])
    edges     = data.get("coauth_edges", [])
    indication = data.get("indication", "")
    client_drug = data.get("client_drug", "")
    generated  = data.get("generated_at", "")[:10]

    all_years = sorted({
        yr for h in hcps for yr in h.get("pub_by_year_simple", {}).keys()
    })

    body = "\n".join([
        render_stat_cards(data),
        f'<div class="card"><h2>Publication Activity Timeline</h2>'
        f'{render_timeline_svg(hcps, all_years)}</div>',
        render_kol_table(hcps, pca_terms),
        render_rising_stars(hcps, all_years),
        render_thematic_heatmap(hcps, pca_terms),
        render_regional_table(hcps),
        render_coauth_network(edges, hcps),
        render_kol_profiles(hcps, all_years),
    ])

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>KOL Report — {indication}</title>
<style>{_CSS}</style>
</head>
<body>
<h1>KOL Identification &amp; Mapping Report</h1>
<p class="meta">
  Indication: <b>{indication}</b> &nbsp;|&nbsp;
  Client drug: <b>{client_drug}</b> &nbsp;|&nbsp;
  Generated: <b>{generated}</b>
</p>
{body}
</body>
</html>"""


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    scored_path = os.path.join(_DIR, "data", "kol_scored.json")
    with open(scored_path, encoding="utf-8") as f:
        data = json.load(f)

    html = build_report_html(data)

    ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir  = os.path.join(_DIR, "results")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"kol_report_{ts}.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    log.info(f"Report written to {out_path}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run all tests**

```bash
python -m pytest tests/ -v
```
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add b_kol_identification/03_generate_report.py b_kol_identification/tests/test_03_report.py
git commit -m "feat(kol): 03_report all section renderers and main()"
```

---

## Task 7: End-to-end smoke test

**Files:**
- No new files — runs the full pipeline against Snowflake.

**Interfaces:**
- Consumes: all three scripts, `config.ini`, `data/input.json`
- Produces: `data/kol_raw.json`, `data/kol_scored.json`, `results/kol_report_*.html`

- [ ] **Step 1: Run full test suite one final time**

```bash
cd b_kol_identification && python -m pytest tests/ -v
```
Expected: all tests PASS.

- [ ] **Step 2: Run script 01 against Snowflake**

```bash
python 01_fetch_kol_data.py
```
Expected log output:
```
INFO Connecting to Snowflake...
INFO Q1: fetching PCA topic terms...
INFO   N PCA terms found
INFO Q3: fetching publication counts (with inline relevance filter)...
INFO   N HCP×year rows returned
INFO Q4: fetching digital scores...
INFO Q5: fetching HCP metadata and quality rating...
INFO   N HCPs merged
INFO Done. N HCPs written to data/kol_raw.json
```

- [ ] **Step 3: Inspect raw output**

```bash
python -c "
import json
with open('data/kol_raw.json') as f: d = json.load(f)
print('PCA terms:', len(d['pca_terms']))
print('HCPs:',      len(d['hcps']))
first = next(iter(d['hcps'].values()))
print('Sample HCP:', first['name'], '|', first['city'], '| years:', list(first['pub_by_year'].keys())[:3])
"
```

- [ ] **Step 4: Run script 02 to score and tier**

```bash
python 02_score_and_tier.py
```
Expected log output:
```
INFO Running Q6 co-authorship for top-50 KOLs...
INFO   N co-authorship edges found
INFO Done. N KOLs scored → data/kol_scored.json
```

- [ ] **Step 5: Inspect scored output**

```bash
python -c "
import json
with open('data/kol_scored.json') as f: d = json.load(f)
hcps = d['hcps']
a = sum(1 for h in hcps if h['tier']=='A')
stars = sum(1 for h in hcps if h['rising_star'])
print(f'Total KOLs: {len(hcps)} | Tier A: {a} | Rising Stars: {stars}')
print('Top 3:')
for h in hcps[:3]:
    print(f'  {h[\"name\"]} | {h[\"tier\"]} | score {h[\"composite_score\"]:.3f} | themes: {[t[\"term_en\"] for t in h[\"theme_labels\"]]}')
"
```

- [ ] **Step 6: Generate report**

```bash
python 03_generate_report.py
```
Expected: `INFO Report written to results/kol_report_YYYYMMDD_HHMMSS.html`

- [ ] **Step 7: Open report in browser and verify**

Check that the report:
- Opens without errors
- Executive Dashboard shows correct KOL/tier/rising star counts
- KOL Ranking Table is populated and tier badges display correctly
- Rising Stars section appears (if any flagged)
- Publication Timeline SVG renders correctly
- Thematic Heatmap shows all PCA terms
- Co-authorship Network shows nodes and edges

- [ ] **Step 8: Final commit**

```bash
git add b_kol_identification/
git commit -m "feat(kol): complete pipeline — fetch, score, and report verified end-to-end"
```
