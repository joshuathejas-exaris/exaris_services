# KOL Report & Data Enhancements — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Populate web-source URLs, collapse the config's hand-written table list into schema knobs, show a data-anchored 20-year publication-history bar chart, and give the HTML report a sidebar nav — all in `b_kol_identification`.

**Architecture:** Additive changes across the five pipeline stages + `pipeline_common.py` + `config.ini`. A new `resolve_tables()` helper centralises table FQNs. Stage 01 anchors its year windows on `MAX(YEAR_VAL)` from the PubMed CF table and adds a separate 20-year history aggregation for display only. Stage 05 ports Service A's sidebar/tab pattern. Scoring/verification logic is untouched.

**Tech Stack:** Python 3, `snowflake-connector-python` (DictCursor), `openpyxl`, `pytest`. External boundaries (Snowflake/Bedrock) are mocked in tests — tests exercise pure functions only.

## Global Constraints

- Each service is self-contained — no cross-service imports (repo convention).
- HTML report must stay self-contained: no CDN, fonts, external stylesheets/scripts, or network resources (all CSS/JS inline).
- Tests mock all Snowflake/Bedrock access; only pure/query-builder functions are unit-tested. `main()` plumbing is verified by the end-to-end re-run, not unit tests.
- Test runner: `.venv/bin/python -m pytest b_kol_identification/tests -q`
- `pub_by_year` is display-only. Scoring uses the 5-year `pubmed_articles`; rising-star flags use `verified_pubmed_years`. Neither may start reading `pub_by_year`.
- The non-FINAL schema value is `ADIPOS_AMBU_TMP`; its config knob is named `schema_tmp`. `schema_final = ADIPOS_AMBU_FINAL`. `database = CUST_TC`. `CORE.PUBMED.ARTICLE`/`AUTHOR` are constants.
- Commit after each task. Work on branch `kol-report-enhancements`.

---

### Task 1: Config refactor — `resolve_tables` + schema knobs

**Files:**
- Modify: `b_kol_identification/pipeline_common.py` (add `resolve_tables`)
- Modify: `b_kol_identification/config.ini` (`[snowflake]`, remove `[tables]`)
- Modify: `b_kol_identification/01_fetch_and_shortlist.py:157` and `:166,:170,:176,:181,:186` (wire helper)
- Modify: `b_kol_identification/02_retrieve_sources.py:72` (wire helper)
- Modify: `b_kol_identification/04_assemble_kols.py:159,:191` (wire helper)
- Modify: `b_kol_identification/CLAUDE.md` (Snowflake tables section)
- Test: `b_kol_identification/tests/test_pipeline_common.py`

**Interfaces:**
- Produces: `resolve_tables(sf: Mapping) -> dict[str, str]` with keys `llm_validation`, `rating_result_final`, `pubmed_cf_flag`, `websites_vertical_all_source`, `content_frame_spec`, `customer_source`, `pubmed_mapping`, `pubmed_article`, `pubmed_author`. `sf` is a mapping with `database`, `schema_final`, `schema_tmp`.

- [ ] **Step 1: Write the failing test**

Add to `b_kol_identification/tests/test_pipeline_common.py`:

```python
def test_resolve_tables_builds_fqns_from_schema_knobs():
    import pipeline_common as pc
    sf = {"database": "CUST_TC", "schema_final": "ADIPOS_AMBU_FINAL",
          "schema_tmp": "ADIPOS_AMBU_TMP"}
    t = pc.resolve_tables(sf)
    assert t["llm_validation"] == "CUST_TC.ADIPOS_AMBU_FINAL.LLM_VALIDATION"
    assert t["rating_result_final"] == "CUST_TC.ADIPOS_AMBU_FINAL.RATING_RESULT_FINAL"
    assert t["pubmed_cf_flag"] == "CUST_TC.ADIPOS_AMBU_FINAL.PUBMED_CONTENT_FRAME_SINGLE_TBL"
    assert t["websites_vertical_all_source"] == "CUST_TC.ADIPOS_AMBU_FINAL.WEBSITES_VERTICAL_ALL_SOURCE"
    assert t["content_frame_spec"] == "CUST_TC.ADIPOS_AMBU_TMP.CONTENT_FRAME_SPEC"
    assert t["customer_source"] == "CUST_TC.ADIPOS_AMBU_TMP.CUSTOMER_SOURCE"
    assert t["pubmed_mapping"] == "CUST_TC.ADIPOS_AMBU_TMP.PUBMED_ARTICLE_MAPPING"
    # CORE.PUBMED.* are constants, independent of the knobs
    assert t["pubmed_article"] == "CORE.PUBMED.ARTICLE"
    assert t["pubmed_author"] == "CORE.PUBMED.AUTHOR"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest b_kol_identification/tests/test_pipeline_common.py::test_resolve_tables_builds_fqns_from_schema_knobs -v`
Expected: FAIL with `AttributeError: module 'pipeline_common' has no attribute 'resolve_tables'`

- [ ] **Step 3: Add `resolve_tables` to `pipeline_common.py`**

Append to `b_kol_identification/pipeline_common.py`:

```python
def resolve_tables(sf):
    """Build fully-qualified table names from the [snowflake] config section.
    Only database + schema_final + schema_tmp change per targeting; the
    CORE.PUBMED.* tables are constants."""
    db, final, tmp = sf["database"], sf["schema_final"], sf["schema_tmp"]
    return {
        "llm_validation":               f"{db}.{final}.LLM_VALIDATION",
        "rating_result_final":          f"{db}.{final}.RATING_RESULT_FINAL",
        "pubmed_cf_flag":               f"{db}.{final}.PUBMED_CONTENT_FRAME_SINGLE_TBL",
        "websites_vertical_all_source": f"{db}.{final}.WEBSITES_VERTICAL_ALL_SOURCE",
        "content_frame_spec":           f"{db}.{tmp}.CONTENT_FRAME_SPEC",
        "customer_source":              f"{db}.{tmp}.CUSTOMER_SOURCE",
        "pubmed_mapping":               f"{db}.{tmp}.PUBMED_ARTICLE_MAPPING",
        "pubmed_article":               "CORE.PUBMED.ARTICLE",
        "pubmed_author":                "CORE.PUBMED.AUTHOR",
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest b_kol_identification/tests/test_pipeline_common.py::test_resolve_tables_builds_fqns_from_schema_knobs -v`
Expected: PASS

- [ ] **Step 5: Rewrite `[snowflake]` and delete `[tables]` in `config.ini`**

Replace lines 1–16 of `b_kol_identification/config.ini` (the `[snowflake]` and `[tables]` sections) with:

```ini
[snowflake]
aws_profile  = AdministratorAccess-311524101909
warehouse    = COMPUTE_WH
database     = CUST_TC
schema_final = ADIPOS_AMBU_FINAL
schema_tmp   = ADIPOS_AMBU_TMP
```

Leave `[terms]`, `[funnel]`, `[bedrock]`, `[scoring]`, `[report]` unchanged.

- [ ] **Step 6: Wire Stage 01 to `resolve_tables`**

In `b_kol_identification/01_fetch_and_shortlist.py`, the import at line 152 already brings in `connect_snowflake`; extend it and replace the `tb` binding.

Change line 152 `from pipeline_common import connect_snowflake` to:
```python
    from pipeline_common import connect_snowflake, resolve_tables
```
Change line 157 from:
```python
    sf, tb, fn, tm = cfg["snowflake"], cfg["tables"], cfg["funnel"], cfg["terms"]
```
to:
```python
    sf, fn, tm = cfg["snowflake"], cfg["funnel"], cfg["terms"]
    tb = resolve_tables(sf)
```
No other line in Stage 01 changes — every `tb["..."]` key still resolves.

- [ ] **Step 7: Wire Stage 02 to `resolve_tables`**

In `b_kol_identification/02_retrieve_sources.py`, change line 68 `from pipeline_common import connect_snowflake` to:
```python
    from pipeline_common import connect_snowflake, resolve_tables
```
Change line 72 from:
```python
    sf, tb, fn = cfg["snowflake"], cfg["tables"], cfg["funnel"]
```
to:
```python
    sf, fn = cfg["snowflake"], cfg["funnel"]
    tb = resolve_tables(sf)
```

- [ ] **Step 8: Wire Stage 04 to `resolve_tables`**

In `b_kol_identification/04_assemble_kols.py`, change line 155 `from pipeline_common import connect_snowflake` to:
```python
    from pipeline_common import connect_snowflake, resolve_tables
```
Change line 159 from:
```python
    sf, tb, sc = cfg["snowflake"], cfg["tables"], cfg["scoring"]
```
to:
```python
    sf, sc = cfg["snowflake"], cfg["scoring"]
    tb = resolve_tables(sf)
```

- [ ] **Step 9: Update the CLAUDE.md Snowflake table doc**

In `b_kol_identification/CLAUDE.md`, in the "Snowflake tables" table, change the `Database.Schema` column so every row reflects reality: `llm_validation`, `rating_result_final`, `pubmed_cf_flag` → `CUST_TC.ADIPOS_AMBU_FINAL`; `content_frame_spec`, `customer_source`, `pubmed_mapping` → `CUST_TC.ADIPOS_AMBU_TMP`; `pubmed_article`/`pubmed_author` → `CORE.PUBMED`. Add a new row:
```
| `websites_vertical_all_source` | `CUST_TC.ADIPOS_AMBU_FINAL` | Web-source URLs (Stage 02 join) | `WEBSITE_ID, URL` |
```

- [ ] **Step 10: Run the full suite (nothing else should break)**

Run: `.venv/bin/python -m pytest b_kol_identification/tests -q`
Expected: PASS (existing tests call query builders with literal strings, so they are unaffected).

- [ ] **Step 11: Commit**

```bash
git add b_kol_identification/pipeline_common.py b_kol_identification/config.ini \
        b_kol_identification/01_fetch_and_shortlist.py b_kol_identification/02_retrieve_sources.py \
        b_kol_identification/04_assemble_kols.py b_kol_identification/CLAUDE.md \
        b_kol_identification/tests/test_pipeline_common.py
git commit -m "refactor(kol): derive table FQNs from schema knobs via resolve_tables"
```

---

### Task 2: Web-source URLs (Stage 02 join)

**Files:**
- Modify: `b_kol_identification/02_retrieve_sources.py:19-23` (`build_web_content_query`), `:90` (caller)
- Test: `b_kol_identification/tests/test_02_retrieve.py`

**Interfaces:**
- Consumes: `resolve_tables()["llm_validation"]`, `resolve_tables()["websites_vertical_all_source"]` (Task 1).
- Produces: `build_web_content_query(llm_validation, websites_vertical_all_source, website_ids, s_customer_id) -> str` — note the new 2nd positional param.

- [ ] **Step 1: Update the failing tests**

In `b_kol_identification/tests/test_02_retrieve.py`, replace `test_web_content_query_has_in_list` and `test_web_content_query_filters_by_customer_id` with the new signature, and extend the web-assemble test to assert the URL:

```python
def test_web_content_query_joins_url_and_has_in_list():
    sql = mod.build_web_content_query(
        "DB.F.LLM_VALIDATION", "DB.F.WEBSITES_VERTICAL_ALL_SOURCE", ["w1", "w2"], "10")
    assert "DB.F.LLM_VALIDATION" in sql
    assert "DB.F.WEBSITES_VERTICAL_ALL_SOURCE" in sql
    assert "LEFT JOIN" in sql.upper()
    assert "URL" in sql
    assert "'w1'" in sql and "'w2'" in sql
    assert "CONTENT" in sql and "WEBSITE_ID" in sql

def test_web_content_query_filters_by_customer_id():
    sql = mod.build_web_content_query(
        "DB.F.LLM_VALIDATION", "DB.F.WEBSITES_VERTICAL_ALL_SOURCE", ["w1"], "O'Brien-10")
    assert "S_CUSTOMER_ID = 'O''Brien-10'" in sql

def test_assemble_web_surfaces_joined_url():
    rows = [{"WEBSITE_ID": "w1", "URL": "http://example.com/p", "CONTENT": "hello"}]
    out = mod.assemble_web_sources(rows, max_chars=1000)
    assert out[0]["source_id"] == "w1" and out[0]["kind"] == "web"
    assert out[0]["url"] == "http://example.com/p" and out[0]["full_text"] == "hello"
```

Delete the now-duplicated `test_assemble_web_sets_source_id_from_website_id` (its assertions are folded into `test_assemble_web_surfaces_joined_url`).

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest b_kol_identification/tests/test_02_retrieve.py -v`
Expected: FAIL — `build_web_content_query()` takes 3 positional args but 4 were given.

- [ ] **Step 3: Rewrite `build_web_content_query`**

Replace `b_kol_identification/02_retrieve_sources.py` lines 19-23 with:

```python
def build_web_content_query(llm_validation: str, websites_vertical_all_source: str,
                            website_ids: list, s_customer_id) -> str:
    escaped_id = str(s_customer_id).replace("'", "''")
    return (f"SELECT lv.WEBSITE_ID, lv.CONTENT, src.URL "
            f"FROM {llm_validation} lv "
            f"LEFT JOIN {websites_vertical_all_source} src ON src.WEBSITE_ID = lv.WEBSITE_ID "
            f"WHERE lv.WEBSITE_ID IN ({_in_list(website_ids)}) "
            f"AND lv.S_CUSTOMER_ID = '{escaped_id}'")
```

- [ ] **Step 4: Update the caller**

Replace `b_kol_identification/02_retrieve_sources.py` line 90:
```python
            cur.execute(build_web_content_query(tb["llm_validation"], h["web_website_ids"], h["s_customer_id"]))
```
with:
```python
            cur.execute(build_web_content_query(tb["llm_validation"], tb["websites_vertical_all_source"],
                                                h["web_website_ids"], h["s_customer_id"]))
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest b_kol_identification/tests/test_02_retrieve.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add b_kol_identification/02_retrieve_sources.py b_kol_identification/tests/test_02_retrieve.py
git commit -m "feat(kol): join WEBSITES_VERTICAL_ALL_SOURCE to populate web-source URLs"
```

---

### Task 3: Data-anchored windows + 20-year history (Stage 01)

**Files:**
- Modify: `b_kol_identification/config.ini` (`[funnel] pub_history_years`)
- Modify: `b_kol_identification/01_fetch_and_shortlist.py` (new builders, aggregation helper, `main()` wiring, JSON write)
- Test: `b_kol_identification/tests/test_01_fetch.py`

**Interfaces:**
- Consumes: `resolve_tables()` keys `pubmed_mapping`, `pubmed_cf_flag` (Task 1).
- Produces:
  - `build_anchor_year_query(pubmed_cf_flag: str) -> str`
  - `build_pubmed_history_query(pubmed_mapping, pubmed_cf_flag, cf_cols, history_years, anchor_year) -> str`
  - `build_pub_history_map(history_rows: list) -> dict[str, dict[str, int]]` (keyed `s_customer_id -> {year: count}`)
  - `apply_pub_history(hcps: list, history_map: dict) -> list` (sets each hcp's `pub_by_year`)
  - `shortlist.json` gains top-level `anchor_year` (int) and `pub_history_years` (int).

- [ ] **Step 1: Add the config param**

In `b_kol_identification/config.ini` `[funnel]` section, add after `pubmed_window_years = 5`:
```ini
pub_history_years     = 20
```

- [ ] **Step 2: Write failing tests**

Add to `b_kol_identification/tests/test_01_fetch.py`:

```python
def test_anchor_year_query_reads_max_year_from_cf_table():
    sql = mod.build_anchor_year_query("DB.F.PUBMED_CF")
    assert "MAX(YEAR_VAL)" in sql.upper()
    assert "DB.F.PUBMED_CF" in sql

def test_pubmed_history_query_windows_20y_back_from_anchor_and_counts_per_year():
    sql = mod.build_pubmed_history_query(
        "DB.T.PUBMED_ARTICLE_MAPPING", "DB.T.PUBMED_CF", ["CF_OBESITY", "CF_GLP1"], 20, 2023)
    assert "MERGE_RESULT > 1" in sql
    assert "CF_OBESITY" in sql and "CF_GLP1" in sql
    assert "2003" in sql                       # anchor(2023) - history(20)
    assert "GROUP BY" in sql.upper()
    assert "COUNT(" in sql.upper()

def test_build_pub_history_map_counts_per_hcp_per_year():
    rows = [{"S_CUSTOMER_ID": "10", "YEAR_VAL": 2011, "N": 2},
            {"S_CUSTOMER_ID": "10", "YEAR_VAL": 2023, "N": 5},
            {"S_CUSTOMER_ID": "11", "YEAR_VAL": 2020, "N": 1}]
    m = mod.build_pub_history_map(rows)
    assert m["10"] == {"2011": 2, "2023": 5}
    assert m["11"] == {"2020": 1}

def test_apply_pub_history_overrides_pub_by_year_display_field():
    hcps = [{"s_customer_id": "10", "pub_by_year": {"2023": 1}, "candidate_score": 3},
            {"s_customer_id": "99", "pub_by_year": {"2022": 9}, "candidate_score": 1}]
    out = mod.apply_pub_history(hcps, {"10": {"2011": 2, "2023": 5}})
    assert out[0]["pub_by_year"] == {"2011": 2, "2023": 5}   # replaced from history
    assert out[1]["pub_by_year"] == {}                       # no history -> empty
    assert out[0]["candidate_score"] == 3                    # scoring untouched
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest b_kol_identification/tests/test_01_fetch.py -k "anchor or history" -v`
Expected: FAIL with `AttributeError` on the new builder names.

- [ ] **Step 4: Add the builders + helpers**

Insert into `b_kol_identification/01_fetch_and_shortlist.py` after `build_pubmed_candidates_query` (after line 52):

```python
def build_anchor_year_query(pubmed_cf_flag: str) -> str:
    return f"SELECT MAX(YEAR_VAL) AS ANCHOR FROM {pubmed_cf_flag}"


def build_pubmed_history_query(pubmed_mapping: str, pubmed_cf_flag: str,
                               cf_cols: list, history_years: int, anchor_year: int) -> str:
    cutoff = anchor_year - history_years
    cf_any = " OR ".join(f"cf.{c} > 0" for c in cf_cols) or "FALSE"
    return f"""
SELECT m.S_CUSTOMER_ID, cf.YEAR_VAL AS YEAR_VAL, COUNT(*) AS N
FROM {pubmed_mapping} m
JOIN {pubmed_cf_flag} cf ON cf.PMID = m.PMID
WHERE m.MERGE_RESULT > 1
  AND ({cf_any})
  AND cf.YEAR_VAL >= {cutoff}
GROUP BY m.S_CUSTOMER_ID, cf.YEAR_VAL
""".strip()


def build_pub_history_map(history_rows: list) -> dict:
    def _g(row, k):
        v = row.get(k)
        return v if v is not None else row.get(k.lower())
    out = {}
    for r in history_rows:
        cid = str(_g(r, "S_CUSTOMER_ID") or "")
        yr = str(_g(r, "YEAR_VAL") or "")
        n = int(_g(r, "N") or 0)
        if not cid or not yr:
            continue
        out.setdefault(cid, {})[yr] = n
    return out


def apply_pub_history(hcps: list, history_map: dict) -> list:
    """Override each HCP's display-only pub_by_year with the 20-year history counts.
    Scoring (candidate_score / pubmed_articles) is untouched."""
    for h in hcps:
        h["pub_by_year"] = history_map.get(str(h.get("s_customer_id", "")), {})
    return hcps
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest b_kol_identification/tests/test_01_fetch.py -k "anchor or history" -v`
Expected: PASS

- [ ] **Step 6: Wire the anchor + history into `main()`**

In `b_kol_identification/01_fetch_and_shortlist.py` `main()`:

After the PCA-terms block (after line 173 `cf_cols = [...]`), add the anchor query:
```python
    log.info("Q1b: anchor year (max YEAR_VAL in PubMed CF table)...")
    cur.execute(build_anchor_year_query(tb["pubmed_cf_flag"]))
    _arow = cur.fetchone()
    anchor_year = int((_arow.get("ANCHOR") or _arow.get("anchor")) or datetime.now().year) \
        if _arow else datetime.now().year
    log.info(f"anchor_year = {anchor_year}")
```

Replace the Q3 pubmed-candidates call (lines 181-183) so it anchors on `anchor_year` instead of `datetime.now().year`:
```python
    log.info("Q3: pubmed candidates (5y scoring window)...")
    cur.execute(build_pubmed_candidates_query(tb["pubmed_mapping"], tb["pubmed_cf_flag"],
                cf_cols, int(fn["pubmed_window_years"]), anchor_year))
    pubmed_rows = cur.fetchall()

    log.info("Q3b: pubmed 20y history (display only)...")
    cur.execute(build_pubmed_history_query(tb["pubmed_mapping"], tb["pubmed_cf_flag"],
                cf_cols, int(fn["pub_history_years"]), anchor_year))
    history_rows = cur.fetchall()
```

After `hcps = list(aggregate_candidates(...).values())` (line 191), overlay the history and keep the ordering that follows:
```python
    hcps = list(aggregate_candidates(web_rows, pubmed_rows, meta_map, term_texts).values())
    hcps = apply_pub_history(hcps, build_pub_history_map(history_rows))
    hcps = shortlist(hcps, int(fn["top_n_candidates"]))
```

- [ ] **Step 7: Persist `anchor_year` + `pub_history_years` in `shortlist.json`**

Replace the `json.dump(...)` payload in `main()` (lines 200-202) with:
```python
        json.dump({"indication": inp["indication"], "client_drug": inp["client_drug"],
                   "generated_at": datetime.now().isoformat(timespec="seconds"),
                   "anchor_year": anchor_year, "pub_history_years": int(fn["pub_history_years"]),
                   "pca_terms": pca_terms, "hcps": hcps}, f, ensure_ascii=False, indent=2)
```

- [ ] **Step 8: Run the whole Stage-01 test file**

Run: `.venv/bin/python -m pytest b_kol_identification/tests/test_01_fetch.py -q`
Expected: PASS (the existing `test_pubmed_candidates_query_verified_author_and_window` still passes — that builder is unchanged; only `main()`'s caller now passes `anchor_year`).

- [ ] **Step 9: Commit**

```bash
git add b_kol_identification/config.ini b_kol_identification/01_fetch_and_shortlist.py \
        b_kol_identification/tests/test_01_fetch.py
git commit -m "feat(kol): anchor year windows on max YEAR_VAL; add 20y pub history for chart"
```

---

### Task 4: 20-year axis in the report + propagate anchor through checkpoints

**Files:**
- Modify: `b_kol_identification/02_retrieve_sources.py:105-107` (carry fields)
- Modify: `b_kol_identification/03_wiki_build.py:174-176` (carry fields)
- Modify: `b_kol_identification/04_assemble_kols.py:201-205` (carry fields)
- Modify: `b_kol_identification/05_generate_report.py` (`build_year_axis`, `build_report_html`, sparkline widths)
- Test: `b_kol_identification/tests/test_05_report.py`

**Interfaces:**
- Consumes: `shortlist.json.anchor_year`, `.pub_history_years` (Task 3).
- Produces: `build_year_axis(data: dict) -> list[str]` — a fixed `pub_history_years`-length list of string years ending at `anchor_year`; falls back to the union of present `pub_by_year` keys when `anchor_year` is absent.

- [ ] **Step 1: Propagate the two fields through Stages 02/03/04**

In `b_kol_identification/02_retrieve_sources.py`, in the `json.dump` at lines 105-107, add the two keys sourced from the loaded `sl` dict:
```python
        json.dump({"indication": sl["indication"], "client_drug": sl["client_drug"],
                   "generated_at": datetime.now().isoformat(timespec="seconds"),
                   "anchor_year": sl.get("anchor_year"), "pub_history_years": sl.get("pub_history_years"),
                   "pca_terms": sl["pca_terms"], "hcps": out_hcps}, f, ensure_ascii=False, indent=2)
```

In `b_kol_identification/03_wiki_build.py`, in the `json.dump` at lines 174-176, add:
```python
        json.dump({"indication": indication, "client_drug": data["client_drug"],
                   "generated_at": datetime.now().isoformat(timespec="seconds"),
                   "anchor_year": data.get("anchor_year"), "pub_history_years": data.get("pub_history_years"),
                   "pca_terms": data["pca_terms"], "hcps": out_hcps}, f, ensure_ascii=False, indent=2)
```

In `b_kol_identification/04_assemble_kols.py`, in the `json.dump` at lines 201-205, add:
```python
        json.dump({"indication": data["indication"], "client_drug": data["client_drug"],
                   "generated_at": datetime.now().isoformat(timespec="seconds"),
                   "anchor_year": data.get("anchor_year"), "pub_history_years": data.get("pub_history_years"),
                   "pca_terms": pca_terms, "hcps": hcps,
                   "coauthor_edges": coauthor_edges, "comention_edges": comention_edges},
                  f, ensure_ascii=False, indent=2)
```

- [ ] **Step 2: Write failing tests for the axis**

Add to `b_kol_identification/tests/test_05_report.py`:

```python
def test_year_axis_is_fixed_span_ending_at_anchor():
    data = {"anchor_year": 2023, "pub_history_years": 20, "hcps": []}
    axis = mod.build_year_axis(data)
    assert axis[0] == "2004" and axis[-1] == "2023"
    assert len(axis) == 20
    assert all(isinstance(y, str) for y in axis)

def test_year_axis_falls_back_to_present_years_without_anchor():
    data = {"hcps": [{"pub_by_year": {"2019": 1, "2021": 2}},
                     {"pub_by_year": {"2020": 1}}]}
    assert mod.build_year_axis(data) == ["2019", "2020", "2021"]

def test_report_uses_20y_axis_in_profile_label():
    data = {**DATA, "anchor_year": 2023, "pub_history_years": 20}
    html = mod.build_report_html(data)
    assert "2004" in html and "2023" in html   # full span rendered in a spark label
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest b_kol_identification/tests/test_05_report.py -k "axis or 20y" -v`
Expected: FAIL — `build_year_axis` not defined.

- [ ] **Step 4: Add `build_year_axis` and use it**

Insert into `b_kol_identification/05_generate_report.py` above `build_report_html` (before line 225):

```python
def build_year_axis(data):
    """Fixed pub_history_years-length axis of string years ending at anchor_year.
    Falls back to the union of years present in pub_by_year for pre-anchor JSON."""
    anchor = data.get("anchor_year")
    span = int(data.get("pub_history_years") or 20)
    if anchor:
        anchor = int(anchor)
        return [str(y) for y in range(anchor - span + 1, anchor + 1)]
    present = sorted({y for h in data.get("hcps", []) for y in h.get("pub_by_year", {})})
    return [str(y) for y in present]
```

Replace line 226 inside `build_report_html`:
```python
    all_years = sorted({y for h in data["hcps"] for y in h.get("pub_by_year", {})})
```
with:
```python
    all_years = build_year_axis(data)
```

- [ ] **Step 5: Widen the sparklines so 20 bars read cleanly**

In `b_kol_identification/05_generate_report.py`, change the `render_sparkline` call in `render_rising_stars` (line 97) from `width=110, height=30` to `width=190, height=34`, and the call in `render_profiles` (line 200) from `width=110, height=30` to `width=190, height=34`. (The bar width auto-thins via `bw = width / n - 1`, giving ~8–9px bars for a 20-year axis.)

- [ ] **Step 6: Run the Stage-05 tests**

Run: `.venv/bin/python -m pytest b_kol_identification/tests/test_05_report.py -q`
Expected: PASS (existing tests pass explicit `all_years` lists to the render functions, so they are unaffected; the two new axis tests and the label test pass).

- [ ] **Step 7: Commit**

```bash
git add b_kol_identification/02_retrieve_sources.py b_kol_identification/03_wiki_build.py \
        b_kol_identification/04_assemble_kols.py b_kol_identification/05_generate_report.py \
        b_kol_identification/tests/test_05_report.py
git commit -m "feat(kol): 20-year data-anchored publication-history bars in the report"
```

---

### Task 5: Sidebar navigation (Stage 05)

**Files:**
- Modify: `b_kol_identification/05_generate_report.py` (imports, `tab_id`, `_render_sidebar`, `TAB_SCRIPT`, CSS, `build_report_html`)
- Test: `b_kol_identification/tests/test_05_report.py`

**Interfaces:**
- Consumes: existing `render_*` functions and `build_year_axis` (Task 4).
- Produces: `tab_id(label) -> str`; `_render_sidebar(groups) -> str` where `groups` is `list[(group_label, list[(item_label, panel_html)])]`.

- [ ] **Step 1: Write failing tests**

Add to `b_kol_identification/tests/test_05_report.py`:

```python
def test_report_has_grouped_sidebar_nav():
    html = mod.build_report_html(DATA)
    assert 'class="sidebar"' in html
    for group in ("OVERVIEW", "ANALYSIS", "PROFILES"):
        assert group in html
    for item in ("Executive Dashboard", "KOL Ranking", "Rising Stars",
                 "Thematic Distribution", "Regional Distribution",
                 "Collaboration Network", "KOL Profiles"):
        assert item in html

def test_report_has_exactly_one_active_panel_and_tab_script():
    html = mod.build_report_html(DATA)
    assert html.count('class="panel active"') == 1   # first panel active on load
    assert "function showTab(" in html
    assert "js-tabs" in html

def test_tab_id_slugifies_label():
    assert mod.tab_id("Rising Stars") == "tab-rising-stars"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest b_kol_identification/tests/test_05_report.py -k "sidebar or active_panel or tab_id" -v`
Expected: FAIL — `tab_id`/sidebar markup not present.

- [ ] **Step 3: Add `re` import and the nav helpers**

In `b_kol_identification/05_generate_report.py`, change line 6 `import configparser, json, logging, os, sys` to:
```python
import configparser, json, logging, os, re, sys
```

Add these definitions above `build_report_html` (after `build_year_axis` from Task 4):

```python
def tab_id(label: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")
    return "tab-" + (slug or "x")


TAB_SCRIPT = """
<script>
function showTab(id){
  var ps = document.querySelectorAll('.panel');
  for (var i = 0; i < ps.length; i++){ ps[i].classList.toggle('active', ps[i].id === id); }
  var ns = document.querySelectorAll('.nav-item');
  for (var j = 0; j < ns.length; j++){
    var on = ns[j].getAttribute('href') === '#' + id;
    ns[j].classList.toggle('active', on);
    if (on) { ns[j].setAttribute('aria-current', 'page'); }
    else { ns[j].removeAttribute('aria-current'); }
  }
  return false;
}
document.addEventListener('DOMContentLoaded', function(){
  document.body.classList.add('js-tabs');
  var f = document.querySelector('.panel');
  if (f) { showTab(f.id); }
});
</script>
"""


def _render_sidebar(groups):
    """groups: list of (group_label, [(item_label, panel_html), ...]). Sticky left nav
    beside a content pane of panels; first item active on load; degrades to full scroll
    without JS. Empty groups/items are skipped."""
    nav = ['<nav class="sidebar" role="tablist" aria-label="Report sections">']
    panels = []
    first = True
    for group_label, items in groups:
        items = [it for it in items if it and it[1]]
        if not items:
            continue
        nav.append(f'<div class="nav-group-label">{_esc(group_label)}</div>')
        for item_label, body in items:
            tid = tab_id(item_label)
            active = " active" if first else ""
            current = ' aria-current="page"' if first else ""
            nav.append(f'<a class="nav-item{active}" role="tab" id="{tid}-btn" '
                       f'href="#{tid}" aria-controls="{tid}"{current} '
                       f'onclick="return showTab(\'{tid}\')">{_esc(item_label)}</a>')
            panels.append(f'<section class="panel{active}" role="tabpanel" id="{tid}" '
                          f'aria-labelledby="{tid}-btn">\n{body}\n</section>')
            first = False
    nav.append("</nav>")
    content = '<main class="content">\n' + "\n".join(panels) + "\n</main>"
    return '<div class="layout">\n' + "\n".join(nav) + "\n" + content + "\n</div>"
```

- [ ] **Step 4: Add sidebar CSS**

In `b_kol_identification/05_generate_report.py`, inside the `css` f-string in `build_report_html`, add before the closing `@media(max-width:720px)` line:

```python
      .layout{{display:flex;gap:28px;align-items:flex-start;margin:18px 0 8px}}
      .sidebar{{flex:0 0 210px;position:sticky;top:16px;align-self:flex-start}}
      .content{{flex:1 1 auto;min-width:0}}
      .nav-group-label{{text-transform:uppercase;letter-spacing:.6px;font-size:11px;
        font-weight:700;color:{PALETTE['muted']};margin:16px 0 6px}}
      .nav-group-label:first-child{{margin-top:0}}
      .nav-item{{display:block;padding:6px 10px;margin:2px 0;border-radius:6px;
        color:{PALETTE['ink']};font-size:14px;text-decoration:none;border-left:3px solid transparent}}
      .nav-item:hover{{background:#eef2f7;color:{PALETTE['accent']}}}
      .nav-item.active{{background:#eef4fb;color:{PALETTE['accent']};font-weight:600;
        border-left-color:{PALETTE['accent']}}}
      body.js-tabs .panel{{display:none}} body.js-tabs .panel.active{{display:block}}
      .content h2:first-child{{margin-top:0;border-top:none;padding-top:0}}
```

And change the mobile media query (line 261) to also collapse the sidebar:
```python
      @media(max-width:720px){{.stats{{grid-template-columns:repeat(2,1fr)}}
        .layout{{flex-direction:column;gap:8px}} .sidebar{{position:static;flex-basis:auto;width:100%}}}}
```

- [ ] **Step 5: Restructure `build_report_html` body into grouped panels**

Replace the report-body assembly in `build_report_html` (lines 263-276, from `return f"""<!DOCTYPE html>` onward) with:

```python
    top = data["hcps"]
    groups = [
        ("OVERVIEW", [
            ("Executive Dashboard", f'<h2>Executive dashboard</h2>{render_stat_cards(data)}'),
            ("KOL Ranking", f'<h2>KOL Ranking — Top {top_n}</h2>{render_kol_table(top, top_n)}'),
        ]),
        ("ANALYSIS", [
            ("Rising Stars", render_rising_stars(top, all_years)
                or '<h2>Rising Stars</h2><p class="muted">No rising stars identified.</p>'),
            ("Thematic Distribution", render_thematic_heatmap(top, data.get("pca_terms", []), top_n=top_n)),
            ("Regional Distribution", render_regional(top)),
            ("Collaboration Network",
             f'<h2>Collaboration network</h2>{render_network(data["coauthor_edges"], data["comention_edges"], top)}'),
        ]),
        ("PROFILES", [
            ("KOL Profiles", render_profiles(top, all_years, top_n=top_n)),
        ]),
    ]
    header = (f'<h1>KOL Identification — {_esc(data["indication"])}</h1>'
              f'<p class="muted">Client drug: {_esc(data["client_drug"])} · '
              f'generated {_esc(data["generated_at"])}</p>')
    body = header + "\n" + _render_sidebar(groups) + "\n" + TAB_SCRIPT
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>KOL Report — {_esc(data['indication'])}</title><style>{css}</style></head>
<body><div class="wrap">
{body}
</div></body></html>"""
```

- [ ] **Step 6: Run the whole Stage-05 test file**

Run: `.venv/bin/python -m pytest b_kol_identification/tests/test_05_report.py -q`
Expected: PASS — including the pre-existing `test_build_report_html_is_selfcontained` (the `TAB_SCRIPT` is an inline `<script>` with no `src`) and `test_no_composite_or_digi_fields_leak_into_full_report`.

- [ ] **Step 7: Commit**

```bash
git add b_kol_identification/05_generate_report.py b_kol_identification/tests/test_05_report.py
git commit -m "feat(kol): sidebar section navigation in the HTML report"
```

---

### Task 6: Document publication-year behaviour in the explainer

**Files:**
- Modify: `b_kol_identification/pipeline_explainer.html` (the service-root copy, NOT `results/`)

**Interfaces:** none (static HTML doc). No test — verified by visual read.

- [ ] **Step 1: Update the Stage-01 PubMed-candidates bullet**

In `b_kol_identification/pipeline_explainer.html`, find the list item (~line 177) that reads:
```html
<li><b>PubMed candidates</b> per doctor: verified authorship (<code>merge_result&gt;1</code>) ∩ a topical CF-flag, within 5 years → candidate <code>PMID</code>s + years + a CF-treffer-weighted count.</li>
```
Replace it with:
```html
<li><b>PubMed candidates</b> per doctor: verified authorship (<code>merge_result&gt;1</code>) ∩ a topical CF-flag, within a <b>5-year window</b> → candidate <code>PMID</code>s + a CF-treffer-weighted count. The window is anchored on <b><code>MAX(YEAR_VAL)</code> in <code>PUBMED_CONTENT_FRAME_SINGLE_TBL</code></b> (the latest year the targeting actually covers — e.g. 2023), <em>not</em> the current calendar year, so a 2023 targeting scores 2018–2023.</li>
<li><b>Publication-history bars (display only):</b> a separate query counts each doctor's CF-flagged PubMed articles per year over <b>20 years</b> back from the same anchor. These counts drive the bar charts only — they are <em>never</em> sent to the LLM and never affect the score. <b>Bar height = CF-flag count</b> (keyword-level activity, unverified); only the most-recent-5-years articles go through LLM-wiki verification and contribute to the score, tier, and rising-star flags.</li>
```

- [ ] **Step 2: Update the "Under the hood" cost-control note**

Find the list item (~line 317):
```html
<li><b>Cost control:</b> only ~75 doctors' sources within a 5-year window reach the LLM; grounding drops fabricated quotes before any verify call is spent.</li>
```
Replace with:
```html
<li><b>Cost control:</b> only ~75 doctors' sources within the anchored 5-year window reach the LLM; the 20-year history is cheap SQL counts for the charts and never hits the LLM. Grounding drops fabricated quotes before any verify call is spent.</li>
```

- [ ] **Step 3: Verify the file still opens as valid HTML**

Run: `.venv/bin/python -c "import pathlib,html.parser; html.parser.HTMLParser().feed(pathlib.Path('b_kol_identification/pipeline_explainer.html').read_text()); print('ok')"`
Expected: prints `ok`

- [ ] **Step 4: Commit**

```bash
git add b_kol_identification/pipeline_explainer.html
git commit -m "docs(kol): explain anchored 5y scoring vs 20y display-only pub history"
```

---

## Post-implementation: end-to-end re-run

The changes are exercised together by a full forced re-run (Task 3 reshuffles the shortlist, so everything downstream regenerates and picks up URLs + 20-year history in one pass). Run from `b_kol_identification/`:

```bash
python 01_fetch_and_shortlist.py --force
python 02_retrieve_sources.py    --force
python 03_wiki_build.py          --force
python 04_assemble_kols.py       --force
python 05_generate_report.py     --force
```

Then open the newest `results/kol_report_<ts>.html` and confirm: the sidebar switches sections; profile/rising-star bars span ~20 years with thin bars; web quotes now carry working "source" links; the Excel "Source URL" column is populated for web claims.

## Self-Review notes

- **Spec coverage:** §1 web URLs → Task 2; §2 config → Task 1; §3a/§3b history+anchor → Task 3; §3c axis/thin bars → Task 4; §4 sidebar → Task 5; §5 explainer → Task 6; §6 testing folded into each task; §7 rollout → post-impl section. All covered.
- **Type consistency:** `build_web_content_query` 2nd positional `websites_vertical_all_source` used identically in Task 2 test + caller. `build_year_axis` returns `list[str]`; `render_sparkline` keys `pub_by_year` (str) with these — consistent. `resolve_tables` keys match every `tb["..."]` site.
- **`pub_by_year` invariant:** widened to 20y in Task 3 via `apply_pub_history`; scoring reads `pubmed_articles`, rising stars read `verified_pubmed_years` (Stage 04) — neither reads `pub_by_year`. Preserved.
