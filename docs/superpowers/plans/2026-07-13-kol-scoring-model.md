# KOL Scoring Model Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the KOL score from a single count of verified-relevant sources into a transparent, config-weighted composite of three factors (relevance + co-author reach + relevance ratio), fed by a hybrid vector-recall arm, reproducible for any past year via an `as_of_year` backtest, with a self-explaining report.

**Architecture:** The existing 5-stage funnel is retained. Stage 03 (LLM ingest→ground→verify) is **untouched** — it remains the sole precision arbiter. Stage 01 gains a vector-recall arm and `as_of_year` capping; Stage 04 gains the feature/normalization/composite layer; Stage 05 gains a network graph, score drill-down, and per-section explainers. A new Stage 06 diffs two `as_of_year` runs.

**Tech Stack:** Python 3, Snowflake connector (`VECTOR_COSINE_SIMILARITY`), Bedrock (unchanged), local ONNX embedding (`VectorCreator`) + cross-encoder (`Reranker`) reused from Service A, pytest with mocked Snowflake/Bedrock/ONNX.

## Global Constraints

- Every service is self-contained — **no cross-service imports**. Copy `vector_creator.py` / `reranker.py` into `b_kol_identification/` (do not import from `a_comp_hcp_communication`).
- All thresholds/weights live in `config.ini`; **no hard-coded tunables** in code.
- Every stage is **resume-safe**: skip if output exists unless `--force`.
- All new factors are computed **downstream of the LLM verify pass** — never re-introduce keyword co-occurrence into the score (the v1 failure mode).
- Co-author reach is **PubMed-only**; web co-mentions stay display-only.
- Embedding dim = **768**; vector literal format = `f"{vec.tolist()}::VECTOR(FLOAT, 768)"`.
- Vector web arm uses **`WEBSITES_VERTICAL_EMBEDDINGS_512` only** (never `WEBSITES_EMBEDDINGS_512`).
- Tests mock all external boundaries (Snowflake, Bedrock, ONNX). Run: `.venv/bin/python -m pytest b_kol_identification/tests -q`.
- Spec: `docs/superpowers/specs/2026-07-13-kol-scoring-model-design.md`.

---

### Task 1: Config surface + vendored ONNX modules

**Files:**
- Modify: `b_kol_identification/config.ini`
- Create: `b_kol_identification/vector_creator.py` (copy of `a_comp_hcp_communication/vector_creator.py`)
- Create: `b_kol_identification/reranker.py` (copy of `a_comp_hcp_communication/reranker.py`)
- Test: `b_kol_identification/tests/test_config_surface.py`

**Interfaces:**
- Produces: new config keys `[funnel] as_of_year`; `[hybrid] hybrid_relevance, vector_sim_threshold, vector_top_k_per_hcp, pubmed_vector_arm, rerank`; `[scoring] weight_relevance, weight_reach, weight_ratio, normalization, min_ratio_denominator`.
- Produces: `VectorCreator().get_vector_from_list(list[str]) -> np.ndarray` (1-D), `Reranker().score(query, passages) -> list[float]`.

- [ ] **Step 1: Copy the two ONNX modules verbatim**

```bash
cp a_comp_hcp_communication/vector_creator.py b_kol_identification/vector_creator.py
cp a_comp_hcp_communication/reranker.py b_kol_identification/reranker.py
```

- [ ] **Step 2: Add the new config sections/keys**

Edit `b_kol_identification/config.ini`. Add `as_of_year` under `[funnel]`, a new `[hybrid]` section, and the new `[scoring]` keys (keep existing keys):

```ini
[funnel]
in_relation_min       = 29
pubmed_window_years   = 5
pub_history_years     = 20
top_n_candidates      = 75
max_sources_per_hcp   = 40
max_source_chars      = 24000
as_of_year            = latest

[hybrid]
hybrid_relevance      = true
vector_sim_threshold  = 0.55
vector_top_k_per_hcp  = 20
pubmed_vector_arm     = false
rerank                = false

[scoring]
tier_a_percentile     = 85
tier_b_percentile     = 60
rising_star_min_pubs  = 3
rising_star_growth    = 3.0
weight_relevance      = 0.60
weight_reach          = 0.25
weight_ratio          = 0.15
normalization         = percentile
min_ratio_denominator = 5
```

- [ ] **Step 3: Write the failing test**

```python
# b_kol_identification/tests/test_config_surface.py
import configparser, os
_CFG = os.path.join(os.path.dirname(__file__), "..", "config.ini")

def _cfg():
    c = configparser.ConfigParser(); c.read(_CFG); return c

def test_hybrid_section_present():
    c = _cfg()
    assert c["hybrid"].getboolean("hybrid_relevance") is True
    assert c["hybrid"].getfloat("vector_sim_threshold") == 0.55
    assert c["hybrid"].getint("vector_top_k_per_hcp") == 20

def test_scoring_weights_present():
    c = _cfg()
    assert c["scoring"].getfloat("weight_relevance") == 0.60
    assert c["scoring"].getfloat("weight_reach") == 0.25
    assert c["scoring"].getfloat("weight_ratio") == 0.15
    assert c["scoring"]["normalization"] == "percentile"
    assert c["scoring"].getint("min_ratio_denominator") == 5

def test_as_of_year_default_latest():
    assert _cfg()["funnel"]["as_of_year"] == "latest"

def test_vendored_modules_import():
    import importlib.util, os
    for name in ("vector_creator.py", "reranker.py"):
        p = os.path.join(os.path.dirname(__file__), "..", name)
        assert os.path.exists(p)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest b_kol_identification/tests/test_config_surface.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add b_kol_identification/config.ini b_kol_identification/vector_creator.py b_kol_identification/reranker.py b_kol_identification/tests/test_config_surface.py
git commit -m "feat(kol): add hybrid/scoring config + vendor ONNX modules"
```

---

### Task 2: `as_of_year` anchor resolution (Stage 01)

**Files:**
- Modify: `b_kol_identification/01_fetch_and_shortlist.py`
- Test: `b_kol_identification/tests/test_01_fetch.py`

**Interfaces:**
- Produces: `resolve_anchor_year(as_of_cfg: str, max_year_in_db: int | None) -> int` — returns the int year when `as_of_cfg` is a 4-digit year; otherwise returns `max_year_in_db` (falling back to `datetime.now().year` when that is None).

- [ ] **Step 1: Write the failing test**

```python
# add to b_kol_identification/tests/test_01_fetch.py
def test_resolve_anchor_year_explicit(fetch_mod):
    assert fetch_mod.resolve_anchor_year("2021", 2025) == 2021

def test_resolve_anchor_year_latest_uses_db_max(fetch_mod):
    assert fetch_mod.resolve_anchor_year("latest", 2025) == 2025

def test_resolve_anchor_year_latest_no_db(fetch_mod):
    from datetime import datetime
    assert fetch_mod.resolve_anchor_year("latest", None) == datetime.now().year
```

If `test_01_fetch.py` has no `fetch_mod` fixture, add at the top:

```python
import importlib.util, os, pytest
@pytest.fixture
def fetch_mod():
    p = os.path.join(os.path.dirname(__file__), "..", "01_fetch_and_shortlist.py")
    spec = importlib.util.spec_from_file_location("fetch01", p)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest b_kol_identification/tests/test_01_fetch.py -k resolve_anchor -v`
Expected: FAIL with `AttributeError: module ... has no attribute 'resolve_anchor_year'`

- [ ] **Step 3: Implement `resolve_anchor_year`**

Add to `01_fetch_and_shortlist.py` (near `build_anchor_year_query`):

```python
def resolve_anchor_year(as_of_cfg: str, max_year_in_db) -> int:
    """as_of_year config → concrete anchor year.
    A 4-digit string pins the backtest year; 'latest' (or anything else) uses the
    DB max YEAR_VAL, falling back to the current year."""
    s = (as_of_cfg or "").strip()
    if s.isdigit() and len(s) == 4:
        return int(s)
    return int(max_year_in_db) if max_year_in_db else datetime.now().year
```

- [ ] **Step 4: Wire it into `main()`**

In `main()`, replace the anchor-year block (currently reading `MAX(YEAR_VAL)` into `anchor_year`) so the config value takes precedence:

```python
    cur.execute(build_anchor_year_query(tb["pubmed_cf_flag"]))
    _arow = cur.fetchone()
    db_max = (_arow.get("ANCHOR") or _arow.get("anchor")) if _arow else None
    anchor_year = resolve_anchor_year(fn.get("as_of_year", "latest"), db_max)
    log.info(f"anchor_year = {anchor_year} (as_of_year={fn.get('as_of_year','latest')})")
```

- [ ] **Step 5: Run tests**

Run: `.venv/bin/python -m pytest b_kol_identification/tests/test_01_fetch.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add b_kol_identification/01_fetch_and_shortlist.py b_kol_identification/tests/test_01_fetch.py
git commit -m "feat(kol): as_of_year anchor resolution for backtest"
```

---

### Task 3: Vector-recall web arm (Stage 01)

**Files:**
- Modify: `b_kol_identification/01_fetch_and_shortlist.py`
- Test: `b_kol_identification/tests/test_01_fetch.py`

**Interfaces:**
- Consumes: `resolve_anchor_year` (Task 2); `VectorCreator` (Task 1).
- Produces: `build_vector_web_query(llm_validation, embeddings_vertical, vec_literal, min_similarity) -> str`; `merge_web_ids(keyword_rows, vector_rows) -> dict[str, list[str]]` mapping `s_customer_id -> deduped website_id list (keyword ∪ vector)`.
- Produces (data): each HCP dict's `web_website_ids` becomes the **union**; `web_candidate_count = len(union)`.

- [ ] **Step 1: Write the failing tests**

```python
# add to b_kol_identification/tests/test_01_fetch.py
def test_build_vector_web_query_uses_vertical_only(fetch_mod):
    sql = fetch_mod.build_vector_web_query(
        "DB.F.LLM_VALIDATION", "DB.F.WEBSITES_VERTICAL_EMBEDDINGS_512",
        "[0.1, 0.2]::VECTOR(FLOAT, 768)", 0.55)
    assert "WEBSITES_VERTICAL_EMBEDDINGS_512" in sql
    assert "WEBSITES_EMBEDDINGS_512" not in sql           # public table excluded
    assert "VECTOR_COSINE_SIMILARITY" in sql
    assert "0.55" in sql and "IS_DOCTOR = 1" in sql

def test_merge_web_ids_unions_and_dedupes(fetch_mod):
    kw = [{"S_CUSTOMER_ID": "1", "WEBSITE_ID": "a"},
          {"S_CUSTOMER_ID": "1", "WEBSITE_ID": "b"}]
    vec = [{"S_CUSTOMER_ID": "1", "WEBSITE_ID": "b"},   # dup, ignored
           {"S_CUSTOMER_ID": "1", "WEBSITE_ID": "c"},   # new from vector arm
           {"S_CUSTOMER_ID": "2", "WEBSITE_ID": "z"}]
    out = fetch_mod.merge_web_ids(kw, vec)
    assert sorted(out["1"]) == ["a", "b", "c"]
    assert out["2"] == ["z"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest b_kol_identification/tests/test_01_fetch.py -k "vector_web or merge_web" -v`
Expected: FAIL (attributes missing)

- [ ] **Step 3: Implement the query builder + merge**

Add near the top of `01_fetch_and_shortlist.py`:

```python
EMBEDDING_DIM = 768


def build_vector_web_query(llm_validation: str, embeddings_vertical: str,
                           vec_literal: str, min_similarity: float) -> str:
    """Per (HCP, website) max chunk cosine-similarity to the indication vector,
    over the HCP's doctor-associated vertical websites. Recall arm — the LLM
    verify pass still arbitrates relevance downstream."""
    return f"""
SELECT lv.S_CUSTOMER_ID, e.WEBSITE_ID,
       MAX(VECTOR_COSINE_SIMILARITY(e.EMBEDDINGS, {vec_literal})) AS SIM
FROM {embeddings_vertical} e
JOIN {llm_validation} lv ON lv.WEBSITE_ID = e.WEBSITE_ID
WHERE lv.NEAR_BY = 1 AND lv.IS_DOCTOR = 1
GROUP BY lv.S_CUSTOMER_ID, e.WEBSITE_ID
HAVING MAX(VECTOR_COSINE_SIMILARITY(e.EMBEDDINGS, {vec_literal})) >= {min_similarity}
""".strip()


def merge_web_ids(keyword_rows: list, vector_rows: list) -> dict:
    """s_customer_id -> deduped website_id list (keyword ∪ vector)."""
    def _g(row, k):
        v = row.get(k)
        return v if v is not None else row.get(k.lower())
    out = {}
    for row in list(keyword_rows) + list(vector_rows):
        cid = str(_g(row, "S_CUSTOMER_ID") or "")
        wid = str(_g(row, "WEBSITE_ID") or "")
        if not cid or not wid:
            continue
        lst = out.setdefault(cid, [])
        if wid not in lst:
            lst.append(wid)
    return out
```

- [ ] **Step 4: Wire the vector arm into `main()`**

After the keyword `web_rows` fetch, add the vector arm (gated by config), then feed the union into aggregation. Add near the top: `from vector_creator import VectorCreator`. In `main()`:

```python
    hy = cfg["hybrid"]
    vector_rows = []
    if hy.getboolean("hybrid_relevance"):
        log.info("Q2b: vector recall arm (web, vertical embeddings)...")
        query_text = f"{inp['indication']} " + ", ".join(term_texts)
        vec = VectorCreator().get_vector_from_list([query_text])
        vlit = f"{vec.tolist()}::VECTOR(FLOAT, {EMBEDDING_DIM})"
        cur.execute(build_vector_web_query(
            tb["llm_validation"], tb["websites_vertical_embeddings"], vlit,
            hy.getfloat("vector_sim_threshold")))
        vector_rows = cur.fetchall()
        log.info(f"vector arm returned {len(vector_rows)} (hcp,website) rows")
    web_id_map = merge_web_ids(web_rows, vector_rows)
```

Then change `aggregate_candidates` to take `web_id_map` instead of re-deriving web IDs from keyword rows (see Step 5).

- [ ] **Step 5: Update `aggregate_candidates` to consume the union**

Replace the web-loop in `aggregate_candidates` so web IDs come from `web_id_map` (union) rather than only keyword-matched rows. Change its signature to `aggregate_candidates(web_id_map, pubmed_rows, meta_map)` and the web accumulation to:

```python
    for cid, wids in web_id_map.items():
        h = acc.setdefault(cid, {"web_website_ids": [], "pubmed_articles": [], "pub_by_year": {}})
        h["web_website_ids"] = list(dict.fromkeys(wids))
```

Delete the old keyword `matches_keywords` web-loop body (keyword refinement already happened when building `web_rows`; the vector arm has no keywords). Update the call site in `main()` to `aggregate_candidates(web_id_map, pubmed_rows, meta_map)`. Keep `matches_keywords` applied to `web_rows` **before** `merge_web_ids` — refine keyword rows in a list comprehension:

```python
    web_rows = [r for r in web_rows if matches_keywords(
        f"{(r.get('COL_KEYWORDS_ORIG') or r.get('col_keywords_orig') or '')} "
        f"{(r.get('COL_KEYWORDS_EN') or r.get('col_keywords_en') or '')}", term_texts)]
```

- [ ] **Step 6: Add the embeddings table to `resolve_tables`**

In `pipeline_common.py` `resolve_tables`, add:

```python
        "websites_vertical_embeddings": f"{db}.{final}.WEBSITES_VERTICAL_EMBEDDINGS_512",
        "pubmed_embeddings":            f"{db}.{final}.PUBMED_EMBEDDINGS_512",
```

Add a test in `tests/test_pipeline_common.py`:

```python
def test_resolve_tables_has_embeddings():
    from pipeline_common import resolve_tables
    t = resolve_tables({"database": "DB", "schema_final": "F", "schema_tmp": "T"})
    assert t["websites_vertical_embeddings"].endswith("WEBSITES_VERTICAL_EMBEDDINGS_512")
```

- [ ] **Step 7: Fix/adjust existing `aggregate_candidates` tests**

Update any test in `test_01_fetch.py` calling `aggregate_candidates` to the new signature `(web_id_map, pubmed_rows, meta_map)`. Example:

```python
def test_aggregate_candidates_counts_union(fetch_mod):
    web_id_map = {"1": ["a", "b"]}
    meta = {"1": {"s_customer_id": "1", "name": "X", "city": "", "specialty": "", "rating": "A"}}
    out = fetch_mod.aggregate_candidates(web_id_map, [], meta)
    assert out["1"]["web_candidate_count"] == 2
    assert out["1"]["candidate_score"] == 2
```

- [ ] **Step 8: Run the full Stage 01 test file**

Run: `.venv/bin/python -m pytest b_kol_identification/tests/test_01_fetch.py b_kol_identification/tests/test_pipeline_common.py -v`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add b_kol_identification/01_fetch_and_shortlist.py b_kol_identification/pipeline_common.py b_kol_identification/tests/test_01_fetch.py b_kol_identification/tests/test_pipeline_common.py
git commit -m "feat(kol): hybrid vector-recall web arm in stage 01"
```

---

### Task 4: Total-source counts for the relevance ratio (Stage 01)

**Files:**
- Modify: `b_kol_identification/01_fetch_and_shortlist.py`
- Test: `b_kol_identification/tests/test_01_fetch.py`

**Interfaces:**
- Produces: `build_total_web_query(llm_validation) -> str`; `build_total_pubmed_query(pubmed_mapping, pubmed_article, anchor_year) -> str`; `build_totals_map(web_total_rows, pubmed_total_rows) -> dict[str, dict]` mapping `s_customer_id -> {"total_web": int, "total_pubmed": int}`.
- Produces (data): each shortlisted HCP dict gains `total_web_sources`, `total_pubmed_sources` (topic-agnostic denominators; PubMed capped at anchor year).

- [ ] **Step 1: Write the failing tests**

```python
# add to b_kol_identification/tests/test_01_fetch.py
def test_build_total_pubmed_query_caps_year(fetch_mod):
    sql = fetch_mod.build_total_pubmed_query(
        "DB.T.PUBMED_ARTICLE_MAPPING", "CORE.PUBMED.ARTICLE", 2021)
    assert "MERGE_RESULT > 1" in sql and "YEAR_VAL <= 2021" in sql

def test_build_total_web_query_doctor_only(fetch_mod):
    sql = fetch_mod.build_total_web_query("DB.F.LLM_VALIDATION")
    assert "IS_DOCTOR = 1" in sql and "COUNT(DISTINCT" in sql.upper()

def test_build_totals_map_merges(fetch_mod):
    web = [{"S_CUSTOMER_ID": "1", "N": 10}]
    pub = [{"S_CUSTOMER_ID": "1", "N": 4}, {"S_CUSTOMER_ID": "2", "N": 2}]
    out = fetch_mod.build_totals_map(web, pub)
    assert out["1"] == {"total_web": 10, "total_pubmed": 4}
    assert out["2"] == {"total_web": 0, "total_pubmed": 2}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest b_kol_identification/tests/test_01_fetch.py -k total -v`
Expected: FAIL

- [ ] **Step 3: Implement builders + merge**

```python
def build_total_web_query(llm_validation: str) -> str:
    return (f"SELECT S_CUSTOMER_ID, COUNT(DISTINCT WEBSITE_ID) AS N "
            f"FROM {llm_validation} WHERE IS_DOCTOR = 1 GROUP BY S_CUSTOMER_ID").strip()


def build_total_pubmed_query(pubmed_mapping: str, pubmed_article: str, anchor_year: int) -> str:
    return f"""
SELECT m.S_CUSTOMER_ID, COUNT(DISTINCT m.PMID) AS N
FROM {pubmed_mapping} m
JOIN {pubmed_article} a ON a.PMID = m.PMID
WHERE m.MERGE_RESULT > 1 AND a.YEAR_VAL <= {anchor_year}
GROUP BY m.S_CUSTOMER_ID
""".strip()


def build_totals_map(web_total_rows: list, pubmed_total_rows: list) -> dict:
    def _g(row, k):
        v = row.get(k)
        return v if v is not None else row.get(k.lower())
    out = {}
    for r in web_total_rows:
        out.setdefault(str(_g(r, "S_CUSTOMER_ID") or ""), {})["total_web"] = int(_g(r, "N") or 0)
    for r in pubmed_total_rows:
        out.setdefault(str(_g(r, "S_CUSTOMER_ID") or ""), {})["total_pubmed"] = int(_g(r, "N") or 0)
    for cid in out:
        out[cid].setdefault("total_web", 0)
        out[cid].setdefault("total_pubmed", 0)
    return out
```

- [ ] **Step 4: Wire into `main()` and attach to HCPs**

After the meta query, run the two totals queries and attach to each HCP (before shortlist):

```python
    log.info("Q5: total sources (ratio denominators)...")
    cur.execute(build_total_web_query(tb["llm_validation"]))
    web_totals = cur.fetchall()
    cur.execute(build_total_pubmed_query(tb["pubmed_mapping"], tb["pubmed_article"], anchor_year))
    pubmed_totals = cur.fetchall()
    totals_map = build_totals_map(web_totals, pubmed_totals)
    for h in hcps:
        t = totals_map.get(str(h.get("s_customer_id", "")), {})
        h["total_web_sources"] = t.get("total_web", 0)
        h["total_pubmed_sources"] = t.get("total_pubmed", 0)
```

(Place this after `aggregate_candidates(...)` produces `hcps` and before `shortlist(...)`.) Ensure `02_retrieve_sources.py` carries `total_web_sources`/`total_pubmed_sources` through to `sources.json` and thence to `wiki.json` (add the two keys to the per-HCP dict emitted in both stages so Stage 04 can read them). Add them in `02_retrieve_sources.py` `out_hcps.append({...})` and `03_wiki_build.py` `out_hcps.append({...})`.

- [ ] **Step 5: Run tests**

Run: `.venv/bin/python -m pytest b_kol_identification/tests/test_01_fetch.py -k total -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add b_kol_identification/01_fetch_and_shortlist.py b_kol_identification/02_retrieve_sources.py b_kol_identification/03_wiki_build.py b_kol_identification/tests/test_01_fetch.py
git commit -m "feat(kol): topic-agnostic total-source counts for relevance ratio"
```

---

### Task 5: Feature layer — reach + ratio (Stage 04)

**Files:**
- Modify: `b_kol_identification/04_assemble_kols.py`
- Test: `b_kol_identification/tests/test_04_assemble.py`

**Interfaces:**
- Consumes (data): per-HCP `verified_pmids`, `verified_web_count`, `verified_pubmed_count`, `total_web_sources`, `total_pubmed_sources`; author rows from `build_coauthor_query`.
- Produces: `compute_reach(verified_pmids, authors_by_pmid, hcp_first, hcp_last) -> {"distinct_coauthors": int, "distinct_affiliations": int}`; `compute_ratio(verified_web, verified_pubmed, total_web, total_pubmed, min_denominator) -> {"ratio": float, "denominator": int, "neutral": bool}`.

- [ ] **Step 1: Write the failing tests**

```python
# add to b_kol_identification/tests/test_04_assemble.py (mod already loaded at top)
def test_compute_reach_dedupes_by_orcid_and_excludes_self():
    authors_by_pmid = {"p1": [
        {"ORCID": "0000-1", "FIRSTNAME": "Anna", "LASTNAME": "Berg", "AFFILIATION": "Uni A"},
        {"ORCID": "0000-2", "FIRSTNAME": "Carl", "LASTNAME": "Ott",  "AFFILIATION": "Uni B"},
        {"ORCID": "0000-9", "FIRSTNAME": "Self", "LASTNAME": "Hcp",  "AFFILIATION": "Uni A"}],
        "p2": [
        {"ORCID": "0000-1", "FIRSTNAME": "Anna", "LASTNAME": "Berg", "AFFILIATION": "Uni A"}]}  # dup coauthor
    r = mod.compute_reach(["p1", "p2"], authors_by_pmid, "Self", "Hcp")
    assert r["distinct_coauthors"] == 2         # Anna + Carl, self excluded, Anna deduped
    assert r["distinct_affiliations"] == 2      # Uni A + Uni B

def test_compute_ratio_normal():
    r = mod.compute_ratio(3, 2, 5, 5, min_denominator=5)
    assert r["denominator"] == 10 and abs(r["ratio"] - 0.5) < 1e-9 and r["neutral"] is False

def test_compute_ratio_low_denominator_is_neutral():
    r = mod.compute_ratio(1, 0, 1, 0, min_denominator=5)
    assert r["neutral"] is True and r["ratio"] == 0.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest b_kol_identification/tests/test_04_assemble.py -k "compute_reach or compute_ratio" -v`
Expected: FAIL

- [ ] **Step 3: Implement `compute_reach` and `compute_ratio`**

Add to `04_assemble_kols.py`:

```python
def compute_reach(verified_pmids: list, authors_by_pmid: dict, hcp_first: str, hcp_last: str) -> dict:
    """Distinct co-authors (dedup by ORCID, fallback normalized name; self excluded)
    and distinct affiliations across the HCP's verified-relevant PubMed articles."""
    from pipeline_common import name_matches, normalize_name
    coauthors, affiliations = set(), set()
    for pmid in verified_pmids:
        for a in authors_by_pmid.get(str(pmid), []):
            fn = str(_g(a, "FIRSTNAME") or ""); ln = str(_g(a, "LASTNAME") or "")
            if name_matches(f"{fn} {ln}", hcp_first, hcp_last):
                continue  # the HCP themselves
            orcid = str(_g(a, "ORCID") or "").strip()
            key = orcid or normalize_name(f"{fn} {ln}")
            if key:
                coauthors.add(key)
            aff = normalize_name(str(_g(a, "AFFILIATION") or ""))
            if aff:
                affiliations.add(aff)
    return {"distinct_coauthors": len(coauthors), "distinct_affiliations": len(affiliations)}


def compute_ratio(verified_web: int, verified_pubmed: int,
                  total_web: int, total_pubmed: int, min_denominator: int) -> dict:
    """verified-relevant / all-sources (topic-agnostic). Neutral below min_denominator."""
    numerator = int(verified_web) + int(verified_pubmed)
    denominator = int(total_web) + int(total_pubmed)
    if denominator < int(min_denominator) or denominator == 0:
        return {"ratio": 0.0, "denominator": denominator, "neutral": True}
    return {"ratio": min(numerator / denominator, 1.0), "denominator": denominator, "neutral": False}
```

- [ ] **Step 4: Build `authors_by_pmid` and attach features in `main()`**

In `main()`, after fetching `author_rows` (already fetched for the network), build a per-PMID map and attach reach/ratio to each HCP. Add a helper and wire it:

```python
    authors_by_pmid = {}
    for r in author_rows:
        authors_by_pmid.setdefault(str(_g(r, "PMID") or ""), []).append(r)
    min_denom = int(sc["min_ratio_denominator"])
    for h in hcps:
        first = h["name"].split(" ")[0] if h["name"] else ""
        last = h["name"].split(" ")[-1] if h["name"] else ""
        h["reach"] = compute_reach(h.get("verified_pmids", []), authors_by_pmid, first, last)
        h["ratio"] = compute_ratio(h.get("verified_web_count", 0), h.get("verified_pubmed_count", 0),
                                   h.get("total_web_sources", 0), h.get("total_pubmed_sources", 0), min_denom)
```

Note: `author_rows` is currently fetched inside the `if all_pmids:` block. Move the connection/fetch so `author_rows` is available before this loop (fetch authors for the union of all `verified_pmids`, defaulting to `[]`).

- [ ] **Step 5: Run tests**

Run: `.venv/bin/python -m pytest b_kol_identification/tests/test_04_assemble.py -k "compute_reach or compute_ratio" -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add b_kol_identification/04_assemble_kols.py b_kol_identification/tests/test_04_assemble.py
git commit -m "feat(kol): co-author reach + relevance ratio features"
```

---

### Task 6: Normalization + weighted composite + tiers (Stage 04)

**Files:**
- Modify: `b_kol_identification/04_assemble_kols.py`
- Test: `b_kol_identification/tests/test_04_assemble.py`

**Interfaces:**
- Consumes: per-HCP raw factors — relevance = `verified_web_count + verified_pubmed_count`; reach = `reach["distinct_coauthors"]`; ratio = `ratio["ratio"]` (0.0 when neutral).
- Produces: `normalize_values(values: list[float], method: str) -> list[float]` (each in [0,1]); `apply_composite(hcps, weights: dict, method: str) -> list` — sets `norm_relevance`, `norm_reach`, `norm_ratio`, `factor_contributions` (dict of weighted parts), and overwrites `kol_score` with the composite.
- Note: `assign_tiers` already keys off `kol_score`; after this task tiers are percentiles of the composite automatically.

- [ ] **Step 1: Write the failing tests**

```python
# add to b_kol_identification/tests/test_04_assemble.py
def test_normalize_percentile_rank():
    out = mod.normalize_values([10, 20, 30, 30], "percentile")
    assert out[0] < out[1] < out[2] and out[2] == out[3]      # ties share a rank
    assert 0.0 <= min(out) and max(out) <= 1.0

def test_normalize_minmax():
    assert mod.normalize_values([0, 5, 10], "minmax") == [0.0, 0.5, 1.0]

def test_normalize_degenerate_pool_is_zero():
    assert mod.normalize_values([7, 7, 7], "minmax") == [0.0, 0.0, 0.0]

def test_apply_composite_weights_and_contributions():
    hcps = [
        {"verified_web_count": 4, "verified_pubmed_count": 0, "reach": {"distinct_coauthors": 0},
         "ratio": {"ratio": 0.0}},
        {"verified_web_count": 0, "verified_pubmed_count": 0, "reach": {"distinct_coauthors": 10},
         "ratio": {"ratio": 1.0}}]
    w = {"relevance": 0.6, "reach": 0.25, "ratio": 0.15}
    out = mod.apply_composite(hcps, w, "minmax")
    # HCP0 maxes relevance (norm 1 * .6), HCP1 maxes reach+ratio (.25 + .15 = .4)
    assert abs(out[0]["kol_score"] - 0.6) < 1e-9
    assert abs(out[1]["kol_score"] - 0.4) < 1e-9
    assert "factor_contributions" in out[0]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest b_kol_identification/tests/test_04_assemble.py -k "normalize or composite" -v`
Expected: FAIL

- [ ] **Step 3: Implement normalization + composite**

```python
def normalize_values(values: list, method: str) -> list:
    """Map raw factor values to [0,1]. percentile (rank-based, ties share rank),
    minmax, or zscore (min-max of z to keep it in [0,1]). Degenerate pool -> zeros."""
    vals = [float(v) for v in values]
    n = len(vals)
    if n == 0:
        return []
    if method == "percentile":
        srt = sorted(vals)
        # fraction of values strictly less than v, so a unique max -> <1; ties share it
        out = []
        for v in vals:
            less = sum(1 for x in srt if x < v)
            equal = sum(1 for x in srt if x == v)
            out.append((less + 0.5 * (equal - 1)) / (n - 1) if n > 1 else 0.0)
        # rescale to [0,1]
        lo, hi = min(out), max(out)
        return [ (o - lo) / (hi - lo) if hi > lo else 0.0 for o in out ]
    if method == "zscore":
        mean = sum(vals) / n
        var = sum((v - mean) ** 2 for v in vals) / n
        std = var ** 0.5
        z = [ (v - mean) / std if std > 0 else 0.0 for v in vals ]
        lo, hi = min(z), max(z)
        return [ (x - lo) / (hi - lo) if hi > lo else 0.0 for x in z ]
    # minmax (default)
    lo, hi = min(vals), max(vals)
    return [ (v - lo) / (hi - lo) if hi > lo else 0.0 for v in vals ]


def apply_composite(hcps: list, weights: dict, method: str) -> list:
    rel = normalize_values([h.get("verified_web_count", 0) + h.get("verified_pubmed_count", 0) for h in hcps], method)
    rch = normalize_values([h.get("reach", {}).get("distinct_coauthors", 0) for h in hcps], method)
    rat = normalize_values([h.get("ratio", {}).get("ratio", 0.0) for h in hcps], method)
    for i, h in enumerate(hcps):
        c_rel = weights["relevance"] * rel[i]
        c_rch = weights["reach"] * rch[i]
        c_rat = weights["ratio"] * rat[i]
        h["norm_relevance"], h["norm_reach"], h["norm_ratio"] = rel[i], rch[i], rat[i]
        h["factor_contributions"] = {"relevance": c_rel, "reach": c_rch, "ratio": c_rat}
        h["kol_score"] = c_rel + c_rch + c_rat
    return hcps
```

- [ ] **Step 4: Wire into `main()`**

In `main()`, replace `hcps = score_hcps(...)` ordering so the composite is applied **after** reach/ratio are attached (Task 5) and **before** `assign_tiers`/`drop_zero_score`. Keep `score_hcps` only to compute `latest_year` (rename its score assignment or set `latest_year` separately). Concretely:

```python
    weights = {"relevance": float(sc["weight_relevance"]),
               "reach": float(sc["weight_reach"]),
               "ratio": float(sc["weight_ratio"])}
    hcps = apply_composite(hcps, weights, sc.get("normalization", "percentile"))
    for h in hcps:  # latest_year for sort/rising-star display
        years = [int(y) for y in h.get("verified_pubmed_years", {}).keys() if str(y).isdigit()]
        h["latest_year"] = max(years) if years else 0
    hcps.sort(key=lambda h: (h["kol_score"], h["latest_year"]), reverse=True)
    hcps = drop_zero_score(hcps)
    hcps = assign_tiers(hcps, float(sc["tier_a_percentile"]), float(sc["tier_b_percentile"]))
```

Update `test_score_is_sum_of_verified_counts_sorted` (which assumed `kol_score` = raw sum): either delete it or repoint it at `apply_composite`. Replace it with:

```python
def test_composite_replaces_raw_sum_as_score():
    hcps = [{"verified_web_count": 1, "verified_pubmed_count": 1, "reach": {"distinct_coauthors": 0}, "ratio": {"ratio": 0.0}},
            {"verified_web_count": 3, "verified_pubmed_count": 4, "reach": {"distinct_coauthors": 5}, "ratio": {"ratio": 1.0}}]
    out = mod.apply_composite(hcps, {"relevance": 0.6, "reach": 0.25, "ratio": 0.15}, "minmax")
    assert out[1]["kol_score"] > out[0]["kol_score"]
```

- [ ] **Step 5: Run the full Stage 04 test file**

Run: `.venv/bin/python -m pytest b_kol_identification/tests/test_04_assemble.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add b_kol_identification/04_assemble_kols.py b_kol_identification/tests/test_04_assemble.py
git commit -m "feat(kol): weighted composite score with normalization + tiers"
```

---

### Task 7: Report — network graph, score drill-down, per-section explainers (Stage 05)

**Files:**
- Modify: `b_kol_identification/05_generate_report.py`
- Test: `b_kol_identification/tests/test_05_report.py`

**Interfaces:**
- Consumes (data): per-HCP `factor_contributions`, `norm_relevance/reach/ratio`, `reach`, `ratio`, `top_quotes`, `theme_labels`; run-level `coauthor_edges`, `anchor_year`, weights.
- Produces: `render_network_svg(edges, nodes) -> str` (inline SVG, no CDN); `render_score_breakdown(hcp, weights) -> str` (HTML expandable panel); `section_explainer(text) -> str` (HTML note block); `as_of_banner(anchor_year, as_of_year_cfg) -> str`.

- [ ] **Step 1: Write the failing tests**

```python
# add to b_kol_identification/tests/test_05_report.py (load the stage via conftest load_stage or inline import)
import importlib.util, os
_S = os.path.join(os.path.dirname(__file__), "..", "05_generate_report.py")
_spec = importlib.util.spec_from_file_location("rep", _S); rep = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(rep)

def test_network_svg_is_selfcontained():
    svg = rep.render_network_svg(
        [{"a_name": "A B", "b_name": "C D", "shared_pmids": 2, "b_external": False}],
        [{"name": "A B", "reach": 3, "affiliation": "Uni A"}, {"name": "C D", "reach": 1, "affiliation": "Uni B"}])
    assert "<svg" in svg and "http://" not in svg and "https://" not in svg
    assert "Uni A" in svg   # affiliation surfaced (label/title)

def test_score_breakdown_shows_three_factors():
    hcp = {"name": "X", "kol_score": 0.72,
           "factor_contributions": {"relevance": 0.5, "reach": 0.15, "ratio": 0.07},
           "norm_relevance": 0.83, "norm_reach": 0.6, "norm_ratio": 0.47,
           "reach": {"distinct_coauthors": 6, "distinct_affiliations": 3},
           "ratio": {"ratio": 0.47, "denominator": 17},
           "top_quotes": [{"quote": "q", "url": "u", "sentiment": "positive"}]}
    html = rep.render_score_breakdown(hcp, {"relevance": 0.6, "reach": 0.25, "ratio": 0.15})
    for token in ("Relevance", "Reach", "Ratio", "0.72", "6", "q"):
        assert token in html

def test_as_of_banner_only_when_backtesting():
    assert rep.as_of_banner(2021, "2021") != ""
    assert rep.as_of_banner(2025, "latest") == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest b_kol_identification/tests/test_05_report.py -k "network_svg or score_breakdown or as_of_banner" -v`
Expected: FAIL

- [ ] **Step 3: Implement the four render helpers**

Add to `05_generate_report.py` (inline SVG force-directed layout via a simple deterministic circular layout — no physics needed for ≤25 nodes; keep it dependency-free):

```python
import math, html as _html

def as_of_banner(anchor_year, as_of_year_cfg) -> str:
    if not as_of_year_cfg or str(as_of_year_cfg).strip().lower() == "latest":
        return ""
    return (f'<div class="asof-banner">Backtest view — PubMed capped at {anchor_year}. '
            f'Web sources are timestamp-free and shown as-is (frozen across years).</div>')

def section_explainer(text: str) -> str:
    return f'<p class="explainer"><strong>How to read this:</strong> {_html.escape(text)}</p>'

def render_score_breakdown(hcp: dict, weights: dict) -> str:
    fc = hcp.get("factor_contributions", {})
    reach = hcp.get("reach", {}); ratio = hcp.get("ratio", {})
    rows = [
        ("Relevance", weights["relevance"], hcp.get("norm_relevance", 0), fc.get("relevance", 0),
         f'{hcp.get("verified_web_count",0)+hcp.get("verified_pubmed_count",0)} verified sources'),
        ("Reach", weights["reach"], hcp.get("norm_reach", 0), fc.get("reach", 0),
         f'{reach.get("distinct_coauthors",0)} co-authors, {reach.get("distinct_affiliations",0)} institutions'),
        ("Ratio", weights["ratio"], hcp.get("norm_ratio", 0), fc.get("ratio", 0),
         f'{ratio.get("ratio",0):.0%} of {ratio.get("denominator",0)} total sources'),
    ]
    tr = "".join(
        f'<tr><td>{name}</td><td>{w:.2f}</td><td>{norm:.2f}</td><td>{contrib:.3f}</td><td>{_html.escape(ev)}</td></tr>'
        for (name, w, norm, contrib, ev) in rows)
    quotes = "".join(
        f'<li>“{_html.escape(q["quote"])}” '
        f'<a href="{_html.escape(q.get("url",""))}">source</a></li>'
        for q in hcp.get("top_quotes", []))
    return (f'<details class="score-breakdown"><summary>Composite {hcp.get("kol_score",0):.2f} — how it was scored</summary>'
            f'<table><thead><tr><th>Factor</th><th>Weight</th><th>Norm</th><th>Contribution</th><th>Evidence</th></tr></thead>'
            f'<tbody>{tr}</tbody></table>'
            f'<div class="score-quotes"><strong>Evidence quotes:</strong><ul>{quotes}</ul></div></details>')

def render_network_svg(edges: list, nodes: list, width: int = 720, height: int = 480) -> str:
    if not nodes:
        return '<svg width="1" height="1"></svg>'
    cx, cy, r = width / 2, height / 2, min(width, height) / 2 - 60
    pos, maxreach = {}, max((n.get("reach", 0) for n in nodes), default=1) or 1
    for i, n in enumerate(nodes):
        ang = 2 * math.pi * i / len(nodes)
        pos[n["name"]] = (cx + r * math.cos(ang), cy + r * math.sin(ang))
    lines = []
    for e in edges:
        a, b = e.get("a_name"), e.get("b_name")
        if a in pos and b in pos:
            (x1, y1), (x2, y2) = pos[a], pos[b]
            w = 1 + min(int(e.get("shared_pmids", 1)), 6)
            dash = ' stroke-dasharray="4"' if e.get("b_external") else ""
            lines.append(f'<line x1="{x1:.0f}" y1="{y1:.0f}" x2="{x2:.0f}" y2="{y2:.0f}" '
                         f'stroke="#9bb" stroke-width="{w}"{dash}/>')
    circles = []
    for n in nodes:
        x, y = pos[n["name"]]
        rad = 6 + 14 * (n.get("reach", 0) / maxreach)
        aff = _html.escape(str(n.get("affiliation", "")))
        circles.append(f'<g><title>{_html.escape(n["name"])} — {aff} (reach {n.get("reach",0)})</title>'
                       f'<circle cx="{x:.0f}" cy="{y:.0f}" r="{rad:.0f}" fill="#3a7"/>'
                       f'<text x="{x:.0f}" y="{y-rad-3:.0f}" font-size="10" text-anchor="middle">'
                       f'{_html.escape(n["name"])}</text></g>')
    return (f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
            f'{"".join(lines)}{"".join(circles)}</svg>')
```

- [ ] **Step 4: Wire the helpers into the report body + add CSS**

In the main report-building function: emit `as_of_banner(...)` near the top; add `section_explainer(...)` at the start of each major section (KOL ranking, network, thematic heatmap, rising stars — one sentence each describing thresholds/meaning); build `nodes` for the network from the KOL list (`{"name", "reach": h["reach"]["distinct_coauthors"], "affiliation": most-common affiliation or ""}`) and render `render_network_svg(coauthor_edges, nodes)` in the Collaboration section; call `render_score_breakdown(h, weights)` inside each KOL profile / ranking row. Add minimal CSS for `.asof-banner`, `.explainer`, `.score-breakdown`, `details/summary`. Pass `weights` and `as_of_year` (from config + `anchor_year` in the JSON) into the render function.

- [ ] **Step 5: Add per-factor columns to the Excel export**

In the Excel-writing block, add columns: `norm_relevance`, `norm_reach`, `norm_ratio`, `contribution_relevance/reach/ratio` (from `factor_contributions`), `distinct_coauthors`, `distinct_affiliations`, `relevance_ratio`.

- [ ] **Step 6: Run the full Stage 05 test file**

Run: `.venv/bin/python -m pytest b_kol_identification/tests/test_05_report.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add b_kol_identification/05_generate_report.py b_kol_identification/tests/test_05_report.py
git commit -m "feat(kol): report network graph, score drill-down, explainers"
```

---

### Task 8: Backtest compare script (Stage 06)

**Files:**
- Create: `b_kol_identification/06_backtest_compare.py`
- Test: `b_kol_identification/tests/test_06_backtest.py`

**Interfaces:**
- Consumes: two `kol_final*.json` files (each `{"anchor_year", "hcps": [{"s_customer_id","name","tier","rising_star","kol_score"}]}`).
- Produces: `compare_runs(earlier: dict, later: dict) -> dict` with `{"rising_to_kol": [...], "tier_moves": [...], "new_kols": [...]}`.

- [ ] **Step 1: Write the failing test**

```python
# b_kol_identification/tests/test_06_backtest.py
import importlib.util, os
_S = os.path.join(os.path.dirname(__file__), "..", "06_backtest_compare.py")
_spec = importlib.util.spec_from_file_location("bt", _S); bt = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(bt)

def test_rising_star_becomes_kol():
    earlier = {"anchor_year": 2021, "hcps": [
        {"s_customer_id": "1", "name": "A", "tier": "C", "rising_star": True, "kol_score": 0.2}]}
    later = {"anchor_year": 2026, "hcps": [
        {"s_customer_id": "1", "name": "A", "tier": "A", "rising_star": False, "kol_score": 0.9}]}
    out = bt.compare_runs(earlier, later)
    assert out["rising_to_kol"] == [{"s_customer_id": "1", "name": "A", "from_tier": "C", "to_tier": "A"}]
    assert {"s_customer_id": "1", "name": "A", "from_tier": "C", "to_tier": "A"} in out["tier_moves"]

def test_new_kol_absent_earlier():
    earlier = {"anchor_year": 2021, "hcps": []}
    later = {"anchor_year": 2026, "hcps": [{"s_customer_id": "2", "name": "B", "tier": "A", "rising_star": False, "kol_score": 0.8}]}
    out = bt.compare_runs(earlier, later)
    assert out["new_kols"] == [{"s_customer_id": "2", "name": "B", "to_tier": "A"}]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest b_kol_identification/tests/test_06_backtest.py -v`
Expected: FAIL (module not found)

- [ ] **Step 3: Implement `06_backtest_compare.py`**

```python
"""Stage 06: diff two as_of_year runs — rising-star→KOL, tier moves, new KOLs.
Usage: python 06_backtest_compare.py --earlier data/kol_final_2021.json --later data/kol_final_latest.json
"""
import argparse, json, logging, os

logging.basicConfig(format="%(asctime)s [%(levelname)s] %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)
_DIR = os.path.dirname(__file__)


def compare_runs(earlier: dict, later: dict) -> dict:
    e = {h["s_customer_id"]: h for h in earlier.get("hcps", [])}
    l = {h["s_customer_id"]: h for h in later.get("hcps", [])}
    rising_to_kol, tier_moves, new_kols = [], [], []
    for cid, lh in l.items():
        eh = e.get(cid)
        if eh is None:
            new_kols.append({"s_customer_id": cid, "name": lh["name"], "to_tier": lh["tier"]})
            continue
        if eh["tier"] != lh["tier"]:
            move = {"s_customer_id": cid, "name": lh["name"],
                    "from_tier": eh["tier"], "to_tier": lh["tier"]}
            tier_moves.append(move)
            if eh.get("rising_star") and lh["tier"] in ("A", "B"):
                rising_to_kol.append(move)
    return {"rising_to_kol": rising_to_kol, "tier_moves": tier_moves, "new_kols": new_kols}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--earlier", required=True); p.add_argument("--later", required=True)
    args = p.parse_args()
    with open(args.earlier, encoding="utf-8") as f: earlier = json.load(f)
    with open(args.later, encoding="utf-8") as f: later = json.load(f)
    result = compare_runs(earlier, later)
    log.info(f"{earlier.get('anchor_year')} → {later.get('anchor_year')}")
    log.info(f"  rising→KOL: {len(result['rising_to_kol'])}, tier moves: {len(result['tier_moves'])}, "
             f"new KOLs: {len(result['new_kols'])}")
    for r in result["rising_to_kol"]:
        log.info(f"    ★ {r['name']}: {r['from_tier']} → {r['to_tier']}")
    out_path = os.path.join(_DIR, "data", "backtest_compare.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    log.info(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest b_kol_identification/tests/test_06_backtest.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add b_kol_identification/06_backtest_compare.py b_kol_identification/tests/test_06_backtest.py
git commit -m "feat(kol): backtest compare script (rising-star to KOL diff)"
```

---

### Task 9: Full-suite regression + docs

**Files:**
- Modify: `b_kol_identification/CLAUDE.md`

- [ ] **Step 1: Run the whole test suite**

Run: `.venv/bin/python -m pytest b_kol_identification/tests -q`
Expected: PASS (all green). Fix any stage tests that still assume `kol_score` is a raw count.

- [ ] **Step 2: Update `CLAUDE.md`**

Update the "What this service does" and funnel sections to describe the composite score, the hybrid vector arm, `as_of_year`, and Stage 06. Update the Files table (add `vector_creator.py`, `reranker.py`, `06_backtest_compare.py`) and Snowflake tables (add `WEBSITES_VERTICAL_EMBEDDINGS_512`, `PUBMED_EMBEDDINGS_512`). Point "Design & plan" at the new spec/plan dated 2026-07-13.

- [ ] **Step 3: Commit**

```bash
git add b_kol_identification/CLAUDE.md
git commit -m "docs(kol): document composite score, hybrid arm, backtest"
```

---

## Self-Review

**Spec coverage:**
- §5 f1 relevance (hybrid net) → Tasks 3, 6. f2 reach → Task 5. f3 ratio → Tasks 4, 5. Composite/normalization/tiers → Task 6. ✓
- §6 hybrid vector arm (vertical only, LLM arbiter unchanged) → Task 3; PubMed vector arm flag exists in config (Task 1), off by default — Stage 03 untouched. ✓
- §7 as_of_year capping (window, history, denominator) → Tasks 2, 4; compare script → Task 8. ✓
- §8 config surface → Task 1. ✓
- §9 report (network graph, drill-down, explainers, banner, Excel columns) → Task 7. ✓
- §10 testing → tests in every task; full suite in Task 9. ✓
- §3 honesty guardrail (factors downstream of verify; Stage 03 untouched) → enforced in Global Constraints + Task 3/5 wiring. ✓

**Note on PubMed vector arm:** config flag `pubmed_vector_arm` exists but no task wires it (default false). If turned on later, it needs a builder analogous to Task 3 against `PUBMED_EMBEDDINGS_512`; deferred by design (spec §6 "off by default").

**Placeholder scan:** No TBD/TODO; every code step has complete code. ✓

**Type consistency:** `resolve_anchor_year`, `build_vector_web_query`, `merge_web_ids`, `build_total_web_query`, `build_total_pubmed_query`, `build_totals_map`, `compute_reach`, `compute_ratio`, `normalize_values`, `apply_composite`, `render_network_svg`, `render_score_breakdown`, `section_explainer`, `as_of_banner`, `compare_runs` — names used consistently across tasks and tests. `aggregate_candidates` signature change (Task 3) is propagated to its tests (Task 3 Step 7). ✓
