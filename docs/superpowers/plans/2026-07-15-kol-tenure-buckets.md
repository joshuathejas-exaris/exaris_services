# KOL Tenure-Partition Scoring & Score-Development Chart — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make KOL and rising-star buckets mutually exclusive via a publication-tenure partition, give "KOL" an absolute meaning through engagement floors, and add per-KOL total-vs-relevant and score-development charts to the report.

**Architecture:** Two axes — *level* (the existing weighted composite, now ranking within a bucket) and *tenure* (career stage from `verified_pubmed_years`). A pure tenure partition assigns rising stars (≤3y relevant tenure + active) vs KOL-eligible (everyone else); KOL-eligible HCPs become KOLs only if they clear four absolute floors; tiers are computed over the KOL pool only. A fixed-yardstick reconstruction replays each factor per historical year to draw a score-development line chart.

**Tech Stack:** Python 3, stdlib only (no new deps); pytest (mock Snowflake/Bedrock); inline-SVG rendering in `05_generate_report.py`; config via `configparser`.

## Global Constraints

- No new third-party dependencies; stdlib + existing project libs only.
- Each service is self-contained — no cross-service imports.
- Reports stay fully self-contained: **no CDN, no external fonts, no network** — all charts are inline SVG.
- LLM ground→verify funnel (Stages 01–03) and the honesty guardrail are **unchanged** in intent — relevance is still decided per source by the verify pass; the composite only reweights already-verified HCPs.
- Ratio is used **raw** (0–1); relevance and reach remain percentile-normalized.
- Rising-star tenure line: `rising_star_max_tenure_years` (default 3). KOL floors: `kol_floor_min_verified=5`, `kol_floor_min_ratio=0.10`, `kol_floor_active_within_yrs=5`, `kol_floor_min_coauthors=3` (waived if `verified_pubmed_count==0`).
- Config windows: `pubmed_window_years=10`, `pub_history_years=10`, `top_n_candidates=100`.
- Tests: `.venv/bin/python -m pytest b_kol_identification/tests -q`. Run from repo root.
- Commit after every task (frequent commits). Branch: `kol-report-enhancements`.

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `b_kol_identification/config.ini` | tunable params | new `[scoring]` floor/tenure keys; changed `[funnel]` windows |
| `b_kol_identification/01_fetch_and_shortlist.py` | candidate counts + per-year series | new all-pubs-per-year query + map |
| `b_kol_identification/02_retrieve_sources.py` | fetch full text | thread `total_pub_by_year` passthrough |
| `b_kol_identification/03_wiki_build.py` | verify + emit per-HCP verified data | thread `total_pub_by_year`; emit `verified_pmid_years` |
| `b_kol_identification/04_assemble_kols.py` | scoring, buckets, tiers, trajectory | ratio-raw; tenure; floors; KOL-pool tiers; breakout; `build_score_trajectory` |
| `b_kol_identification/05_generate_report.py` | HTML/Excel report | stacked year bars; score-dev chart; total pubs; career labels; disjoint counts |
| `b_kol_identification/CLAUDE.md` | service docs | describe the new model |
| `b_kol_identification/tests/test_*.py` | unit tests | new + updated tests per task |

---

### Task 1: Config knobs

**Files:**
- Modify: `b_kol_identification/config.ini`
- Test: `b_kol_identification/tests/test_config_surface.py`

**Interfaces:**
- Produces: config keys `[funnel].pubmed_window_years=10`, `[funnel].pub_history_years=10`, `[funnel].top_n_candidates=100`, `[scoring].rising_star_max_tenure_years=3`, `[scoring].kol_floor_min_verified=5`, `[scoring].kol_floor_min_ratio=0.10`, `[scoring].kol_floor_active_within_yrs=5`, `[scoring].kol_floor_min_coauthors=3`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_config_surface.py`:

```python
def test_new_tenure_and_floor_keys_present():
    import configparser, os
    cfg = configparser.ConfigParser()
    cfg.read(os.path.join(os.path.dirname(__file__), "..", "config.ini"))
    assert cfg["funnel"].getint("pubmed_window_years") == 10
    assert cfg["funnel"].getint("pub_history_years") == 10
    assert cfg["funnel"].getint("top_n_candidates") == 100
    sc = cfg["scoring"]
    assert sc.getint("rising_star_max_tenure_years") == 3
    assert sc.getint("kol_floor_min_verified") == 5
    assert abs(sc.getfloat("kol_floor_min_ratio") - 0.10) < 1e-9
    assert sc.getint("kol_floor_active_within_yrs") == 5
    assert sc.getint("kol_floor_min_coauthors") == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest b_kol_identification/tests/test_config_surface.py::test_new_tenure_and_floor_keys_present -v`
Expected: FAIL (`pubmed_window_years == 10` assertion fails; current value is 5).

- [ ] **Step 3: Edit config.ini**

In `[funnel]` set `pubmed_window_years = 10`, `pub_history_years = 10`, `top_n_candidates = 100`.
In `[scoring]` append:

```ini
rising_star_max_tenure_years = 3
kol_floor_min_verified       = 5
kol_floor_min_ratio          = 0.10
kol_floor_active_within_yrs  = 5
kol_floor_min_coauthors      = 3
```

Leave `as_of_year = 2018` as-is (backtest anchor for the current live run).

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest b_kol_identification/tests/test_config_surface.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add b_kol_identification/config.ini b_kol_identification/tests/test_config_surface.py
git commit -m "feat(kol): add tenure-partition + KOL-floor config knobs, widen windows"
```

---

### Task 2: Stage 01 — all-publications-per-year query

**Files:**
- Modify: `b_kol_identification/01_fetch_and_shortlist.py`
- Test: `b_kol_identification/tests/test_01_fetch.py`

**Interfaces:**
- Produces: `build_total_pub_by_year_query(pubmed_mapping: str, pubmed_article: str, history_years: int, anchor_year: int) -> str`; `build_total_pub_by_year_map(rows: list) -> dict` returning `{s_customer_id: {year_str: count}}`. Each shortlisted HCP dict gains `total_pub_by_year: dict`.
- Consumes: existing `_g` row-accessor pattern (defined locally in each builder).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_01_fetch.py`:

```python
def test_total_pub_by_year_query_has_no_cf_filter_and_windows():
    sql = mod.build_total_pub_by_year_query("MAP", "ART", history_years=10, anchor_year=2018)
    assert "MAP" in sql and "ART" in sql
    assert "GROUP BY" in sql.upper() and "YEAR_VAL" in sql.upper()
    assert "2008" in sql and "2018" in sql          # anchor-10 .. anchor
    assert "cf." not in sql.lower()                  # CF filter removed (all pubs)

def test_total_pub_by_year_map_groups_counts_by_year():
    rows = [{"S_CUSTOMER_ID": "1", "YEAR_VAL": 2017, "N": 3},
            {"S_CUSTOMER_ID": "1", "YEAR_VAL": 2018, "N": 2},
            {"S_CUSTOMER_ID": "2", "YEAR_VAL": 2018, "N": 5}]
    m = mod.build_total_pub_by_year_map(rows)
    assert m["1"] == {"2017": 3, "2018": 2}
    assert m["2"] == {"2018": 5}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest b_kol_identification/tests/test_01_fetch.py::test_total_pub_by_year_query_has_no_cf_filter_and_windows -v`
Expected: FAIL (`build_total_pub_by_year_query` not defined).

- [ ] **Step 3: Add the builder + map functions**

In `01_fetch_and_shortlist.py`, after `build_total_pubmed_query`:

```python
def build_total_pub_by_year_query(pubmed_mapping: str, pubmed_article: str,
                                  history_years: int, anchor_year: int) -> str:
    """All of the HCP's publications per year (NO CF/topic filter), over the
    history window ending at the anchor. Feeds the total-vs-relevant chart and
    the score-development denominator."""
    cutoff = anchor_year - history_years
    return f"""
SELECT m.S_CUSTOMER_ID, a.YEAR_VAL AS YEAR_VAL, COUNT(DISTINCT m.PMID) AS N
FROM {pubmed_mapping} m
JOIN {pubmed_article} a ON a.PMID = m.PMID
WHERE m.MERGE_RESULT > 1
  AND a.YEAR_VAL >= {cutoff}
  AND a.YEAR_VAL <= {anchor_year}
GROUP BY m.S_CUSTOMER_ID, a.YEAR_VAL
""".strip()


def build_total_pub_by_year_map(rows: list) -> dict:
    def _g(row, k):
        v = row.get(k)
        return v if v is not None else row.get(k.lower())
    out = {}
    for r in rows:
        cid = str(_g(r, "S_CUSTOMER_ID") or "")
        yr = str(_g(r, "YEAR_VAL") or "")
        n = int(_g(r, "N") or 0)
        if not cid or not yr:
            continue
        out.setdefault(cid, {})[yr] = n
    return out
```

- [ ] **Step 4: Wire the query into `main()`**

In `main()`, right after the `Q3b` history query block (after `history_rows = cur.fetchall()`), add:

```python
    log.info("Q3c: pubmed total-per-year (all pubs, no CF filter)...")
    cur.execute(build_total_pub_by_year_query(tb["pubmed_mapping"], tb["pubmed_article"],
                int(fn["pub_history_years"]), anchor_year))
    total_year_rows = cur.fetchall()
```

Then after the `for h in hcps:` totals loop (where `total_web_sources` is set), attach the map:

```python
    total_year_map = build_total_pub_by_year_map(total_year_rows)
    for h in hcps:
        h["total_pub_by_year"] = total_year_map.get(str(h.get("s_customer_id", "")), {})
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest b_kol_identification/tests/test_01_fetch.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add b_kol_identification/01_fetch_and_shortlist.py b_kol_identification/tests/test_01_fetch.py
git commit -m "feat(kol): stage01 all-pubs-per-year series for total-vs-relevant chart"
```

---

### Task 3: Thread `total_pub_by_year` + `verified_pmid_years` through Stages 02–03

**Files:**
- Modify: `b_kol_identification/02_retrieve_sources.py:101-108`
- Modify: `b_kol_identification/03_wiki_build.py:156-172`
- Test: `b_kol_identification/tests/test_03_wiki.py`

**Interfaces:**
- Produces: each HCP emitted by Stage 03 gains `total_pub_by_year: dict` (passthrough from Stage 01) and `verified_pmid_years: dict` (`{pmid_str: year_int}` for verified PubMed claims). Consumed by Task 9's `build_score_trajectory`.

**Note on testing:** Stages 02/03 `main()` are Snowflake/Bedrock-driven and not unit-tested; the passthrough is a plain field copy. Task 3 adds a small pure helper `build_pmid_years(claims)` in Stage 03 so the `verified_pmid_years` logic *is* unit-tested; the two passthrough lines are verified end-to-end by Task 9's fixtures.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_03_wiki.py`:

```python
def test_build_pmid_years_maps_verified_pubmed_claims_only():
    claims = [
        {"kind": "pubmed", "source_id": "p1", "year": 2015},
        {"kind": "pubmed", "source_id": "p2", "year": 2018},
        {"kind": "web",    "source_id": "w1", "year": None},
        {"kind": "pubmed", "source_id": "p3"},               # no year -> skipped
    ]
    assert mod.build_pmid_years(claims) == {"p1": 2015, "p2": 2018}
```

(Match the existing importlib loader at the top of `test_03_wiki.py`; it exposes the module as `mod`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest b_kol_identification/tests/test_03_wiki.py::test_build_pmid_years_maps_verified_pubmed_claims_only -v`
Expected: FAIL (`build_pmid_years` not defined).

- [ ] **Step 3: Add the helper + emit the fields in Stage 03**

In `03_wiki_build.py`, add a module-level helper (near the other top-level functions):

```python
def build_pmid_years(claims: list) -> dict:
    """{pmid: year} for verified PubMed claims that carry a year."""
    out = {}
    for c in claims:
        if c.get("kind") == "pubmed" and c.get("year"):
            out[str(c["source_id"])] = int(c["year"])
    return out
```

In the `out_hcps.append({...})` dict (currently around line 164–172), add two keys after `"pub_by_year": h.get("pub_by_year", {})`:

```python
            "total_pub_by_year": h.get("total_pub_by_year", {}),
            "verified_pmid_years": build_pmid_years(claims),
```

- [ ] **Step 4: Add the passthrough in Stage 02**

In `02_retrieve_sources.py`, in the `out_hcps.append({...})` dict (line ~101–108), add after `"pub_by_year": h.get("pub_by_year", {})`:

```python
            "total_pub_by_year": h.get("total_pub_by_year", {}),
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest b_kol_identification/tests/test_03_wiki.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add b_kol_identification/02_retrieve_sources.py b_kol_identification/03_wiki_build.py b_kol_identification/tests/test_03_wiki.py
git commit -m "feat(kol): thread total_pub_by_year + verified_pmid_years to stage 04"
```

---

### Task 4: Ratio used raw in the composite

**Files:**
- Modify: `b_kol_identification/04_assemble_kols.py:45-58` (`apply_composite`)
- Test: `b_kol_identification/tests/test_04_assemble.py`

**Interfaces:**
- Produces: `apply_composite(hcps, weights, method)` unchanged signature; ratio contribution is now `weights["ratio"] * ratio_raw` where `ratio_raw = h["ratio"]["ratio"]` (no normalization). `norm_ratio` is set to the raw ratio for display continuity.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_04_assemble.py`:

```python
def test_ratio_is_used_raw_not_normalized():
    # Two HCPs with equal raw ratios 0.5: percentile/minmax would flatten both to 0
    # (degenerate), but RAW keeps each ratio contribution at weight*0.5.
    hcps = [{"verified_web_count": 0, "verified_pubmed_count": 0,
             "reach": {"distinct_coauthors": 0}, "ratio": {"ratio": 0.5}},
            {"verified_web_count": 0, "verified_pubmed_count": 0,
             "reach": {"distinct_coauthors": 0}, "ratio": {"ratio": 0.5}}]
    out = mod.apply_composite(hcps, {"relevance": 0.6, "reach": 0.25, "ratio": 0.15}, "minmax")
    assert abs(out[0]["factor_contributions"]["ratio"] - 0.15 * 0.5) < 1e-9
    assert abs(out[0]["kol_score"] - 0.075) < 1e-9
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest b_kol_identification/tests/test_04_assemble.py::test_ratio_is_used_raw_not_normalized -v`
Expected: FAIL (ratio currently normalized → degenerate pool gives 0.0).

- [ ] **Step 3: Edit `apply_composite`**

Replace the body of `apply_composite`:

```python
def apply_composite(hcps: list, weights: dict, method: str) -> list:
    """Normalize relevance and reach across the pool; use ratio RAW (it is already an
    intrinsic 0-1 quantity, indication-independent). Combine into the weighted composite
    that OVERWRITES kol_score."""
    rel = normalize_values([h.get("verified_web_count", 0) + h.get("verified_pubmed_count", 0) for h in hcps], method)
    rch = normalize_values([h.get("reach", {}).get("distinct_coauthors", 0) for h in hcps], method)
    for i, h in enumerate(hcps):
        rat_raw = float(h.get("ratio", {}).get("ratio", 0.0))
        c_rel = weights["relevance"] * rel[i]
        c_rch = weights["reach"] * rch[i]
        c_rat = weights["ratio"] * rat_raw
        h["norm_relevance"], h["norm_reach"], h["norm_ratio"] = rel[i], rch[i], rat_raw
        h["factor_contributions"] = {"relevance": c_rel, "reach": c_rch, "ratio": c_rat}
        h["kol_score"] = c_rel + c_rch + c_rat
    return hcps
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest b_kol_identification/tests/test_04_assemble.py -q`
Expected: PASS (existing composite tests still pass — they use ratios 0.0/1.0 where raw and normalized coincide).

- [ ] **Step 5: Commit**

```bash
git add b_kol_identification/04_assemble_kols.py b_kol_identification/tests/test_04_assemble.py
git commit -m "feat(kol): use ratio raw in composite (keep its absolute meaning)"
```

---

### Task 5: `compute_tenure`

**Files:**
- Modify: `b_kol_identification/04_assemble_kols.py`
- Test: `b_kol_identification/tests/test_04_assemble.py`

**Interfaces:**
- Produces: `compute_tenure(verified_pubmed_years: dict, anchor_year: int) -> dict` returning `{"relevant_tenure": int|None, "first_relevant_year": int|None}`. `None` when there are no verified PubMed years (web-only HCP).

- [ ] **Step 1: Write the failing test**

```python
def test_compute_tenure_span_from_first_verified_year():
    t = mod.compute_tenure({"2016": 2, "2018": 3}, anchor_year=2018)
    assert t["first_relevant_year"] == 2016 and t["relevant_tenure"] == 3   # 2018-2016+1

def test_compute_tenure_none_when_no_pubmed_years():
    t = mod.compute_tenure({}, anchor_year=2018)
    assert t["relevant_tenure"] is None and t["first_relevant_year"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest b_kol_identification/tests/test_04_assemble.py::test_compute_tenure_span_from_first_verified_year -v`
Expected: FAIL (`compute_tenure` not defined).

- [ ] **Step 3: Implement**

Add to `04_assemble_kols.py`:

```python
def compute_tenure(verified_pubmed_years: dict, anchor_year: int) -> dict:
    """Relevant-publication career stage. tenure = anchor - first_verified_year + 1.
    None when the HCP has no verified PubMed years (web-only voice)."""
    years = [int(y) for y in verified_pubmed_years.keys() if str(y).isdigit()]
    if not years:
        return {"relevant_tenure": None, "first_relevant_year": None}
    first = min(years)
    return {"relevant_tenure": int(anchor_year) - first + 1, "first_relevant_year": first}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest b_kol_identification/tests/test_04_assemble.py -k tenure -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add b_kol_identification/04_assemble_kols.py b_kol_identification/tests/test_04_assemble.py
git commit -m "feat(kol): compute relevant-publication tenure"
```

---

### Task 6: Rising-star rule = tenure partition

**Files:**
- Modify: `b_kol_identification/04_assemble_kols.py:72-82` (`flag_rising_stars`)
- Test: `b_kol_identification/tests/test_04_assemble.py`

**Interfaces:**
- Produces: `flag_rising_stars(hcps, min_pubs: int, max_tenure_years: int, anchor_year: int) -> list` — sets `rising_star=True` when `relevant_tenure` is not None, `relevant_tenure <= max_tenure_years`, and total verified PubMed pubs `>= min_pubs`. Signature **changes** (drops `growth`, adds `max_tenure_years`, `anchor_year`).
- Consumes: `compute_tenure` (Task 5).

- [ ] **Step 1: Update the two existing rising-star tests + add new ones**

Replace `test_rising_star_new_voice_on_verified_years` and `test_rising_star_not_flagged_for_established_author`, and add cases:

```python
def test_rising_star_short_tenure_and_active():
    # first verified year 2016, anchor 2018 -> tenure 3 (<=3), 5 pubs (>=3) -> rising
    hcps = [{"verified_pubmed_years": {"2016": 2, "2018": 3}}]
    out = mod.flag_rising_stars(hcps, min_pubs=3, max_tenure_years=3, anchor_year=2018)
    assert out[0]["rising_star"] is True

def test_not_rising_when_tenure_exceeds_limit():
    # first year 2010, anchor 2018 -> tenure 9 (>3) -> established, not rising
    hcps = [{"verified_pubmed_years": {"2010": 1, "2018": 4}}]
    out = mod.flag_rising_stars(hcps, min_pubs=3, max_tenure_years=3, anchor_year=2018)
    assert out[0]["rising_star"] is False

def test_not_rising_when_inactive_one_off():
    # tenure 1 but only 1 pub (< min_pubs) -> a one-off, not a rising star
    hcps = [{"verified_pubmed_years": {"2018": 1}}]
    out = mod.flag_rising_stars(hcps, min_pubs=3, max_tenure_years=3, anchor_year=2018)
    assert out[0]["rising_star"] is False

def test_not_rising_when_no_pubmed_years():
    hcps = [{"verified_pubmed_years": {}}]
    out = mod.flag_rising_stars(hcps, min_pubs=3, max_tenure_years=3, anchor_year=2018)
    assert out[0]["rising_star"] is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest b_kol_identification/tests/test_04_assemble.py -k rising -v`
Expected: FAIL (old signature/logic).

- [ ] **Step 3: Rewrite `flag_rising_stars`**

```python
def flag_rising_stars(hcps: list, min_pubs: int, max_tenure_years: int, anchor_year: int) -> list:
    """Rising star = short relevant-publication tenure AND genuinely active.
    Tenure-based partition: a rising star is, by construction, not yet established."""
    out = []
    for h in hcps:
        years = h.get("verified_pubmed_years", {})
        ten = compute_tenure(years, anchor_year)["relevant_tenure"]
        total = sum(int(c) for c in years.values())
        rising = (ten is not None and ten <= max_tenure_years and total >= min_pubs)
        out.append({**h, "rising_star": bool(rising)})
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest b_kol_identification/tests/test_04_assemble.py -k rising -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add b_kol_identification/04_assemble_kols.py b_kol_identification/tests/test_04_assemble.py
git commit -m "feat(kol): rising-star = short-tenure + active (tenure partition)"
```

---

### Task 7: KOL absolute floors

**Files:**
- Modify: `b_kol_identification/04_assemble_kols.py`
- Test: `b_kol_identification/tests/test_04_assemble.py`

**Interfaces:**
- Produces: `passes_kol_floors(hcp, anchor_year, min_verified, min_ratio, active_within_yrs, min_coauthors) -> bool`. A tenure-eligible HCP is a KOL iff it passes all four floors (co-author floor waived when `verified_pubmed_count == 0`).
- Consumes: `hcp` fields `verified_web_count`, `verified_pubmed_count`, `ratio` (`{"ratio","neutral"}`), `verified_pubmed_years`, `reach` (`{"distinct_coauthors"}`).

- [ ] **Step 1: Write the failing tests**

```python
def _floored_hcp(**kw):
    base = {"verified_web_count": 3, "verified_pubmed_count": 3,
            "ratio": {"ratio": 0.5, "neutral": False},
            "verified_pubmed_years": {"2018": 3}, "reach": {"distinct_coauthors": 5}}
    base.update(kw); return base

def test_kol_floors_pass_when_all_met():
    assert mod.passes_kol_floors(_floored_hcp(), 2018, 5, 0.10, 5, 3) is True

def test_kol_floors_fail_min_verified():
    assert mod.passes_kol_floors(_floored_hcp(verified_web_count=1, verified_pubmed_count=1),
                                 2018, 5, 0.10, 5, 3) is False

def test_kol_floors_fail_low_ratio():
    assert mod.passes_kol_floors(_floored_hcp(ratio={"ratio": 0.05, "neutral": False}),
                                 2018, 5, 0.10, 5, 3) is False

def test_kol_floors_fail_neutral_thin_ratio():
    assert mod.passes_kol_floors(_floored_hcp(ratio={"ratio": 0.0, "neutral": True}),
                                 2018, 5, 0.10, 5, 3) is False

def test_kol_floors_fail_inactive():
    # last verified year 2010, anchor 2018, window 5 -> no recent pubmed, no web -> inactive
    assert mod.passes_kol_floors(_floored_hcp(verified_web_count=0,
                                 verified_pubmed_years={"2010": 3}),
                                 2018, 5, 0.10, 5, 3) is False

def test_kol_floors_recent_activity_satisfied_by_web():
    # no pubmed years at all but has web sources (timestamp-free -> treated as current)
    h = _floored_hcp(verified_pubmed_count=0, verified_pubmed_years={},
                     reach={"distinct_coauthors": 0})
    assert mod.passes_kol_floors(h, 2018, 5, 0.10, 5, 3) is True   # coauthor floor waived, active via web

def test_kol_floors_coauthor_floor_applies_with_pubmed():
    assert mod.passes_kol_floors(_floored_hcp(reach={"distinct_coauthors": 1}),
                                 2018, 5, 0.10, 5, 3) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest b_kol_identification/tests/test_04_assemble.py -k floors -v`
Expected: FAIL (`passes_kol_floors` not defined).

- [ ] **Step 3: Implement**

```python
def passes_kol_floors(hcp: dict, anchor_year: int, min_verified: int, min_ratio: float,
                      active_within_yrs: int, min_coauthors: int) -> bool:
    """Absolute engagement floors that give 'KOL' meaning independent of the pool.
    All must hold. The co-author floor is waived for HCPs with no PubMed activity."""
    verified = hcp.get("verified_web_count", 0) + hcp.get("verified_pubmed_count", 0)
    if verified < min_verified:
        return False
    r = hcp.get("ratio", {})
    if r.get("neutral") or float(r.get("ratio", 0.0)) < min_ratio:
        return False
    # recent activity: any web source (timestamp-free, treated as current) OR a recent pub
    has_web = hcp.get("verified_web_count", 0) > 0
    years = [int(y) for y in hcp.get("verified_pubmed_years", {}).keys() if str(y).isdigit()]
    recent_pub = any(y >= int(anchor_year) - int(active_within_yrs) for y in years)
    if not (has_web or recent_pub):
        return False
    # co-author reach floor — waived if the HCP has no PubMed at all
    if hcp.get("verified_pubmed_count", 0) > 0:
        if hcp.get("reach", {}).get("distinct_coauthors", 0) < min_coauthors:
            return False
    return True
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest b_kol_identification/tests/test_04_assemble.py -k floors -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add b_kol_identification/04_assemble_kols.py b_kol_identification/tests/test_04_assemble.py
git commit -m "feat(kol): absolute KOL floors (verified, ratio, recency, reach)"
```

---

### Task 8: Tiers over the KOL pool only + breakout badge

**Files:**
- Modify: `b_kol_identification/04_assemble_kols.py:61-69` (`assign_tiers`)
- Test: `b_kol_identification/tests/test_04_assemble.py`

**Interfaces:**
- Produces: `assign_tiers(hcps, tier_a_pct, tier_b_pct) -> list` — computes percentile thresholds over the `is_kol==True` subset only; sets `tier` in {A,B,C} for KOLs and `tier=None` for non-KOLs; sets `breakout=True` for a `rising_star` whose `kol_score >= thresh_a`.
- Consumes: `is_kol` (Task 7 wiring, Task 10), `rising_star` (Task 6), `kol_score` (Task 4).

- [ ] **Step 1: Write the failing test**

```python
def test_tiers_computed_over_kol_pool_only_and_breakout():
    hcps = [
        {"s_customer_id": "k1", "is_kol": True,  "rising_star": False, "kol_score": 0.9},
        {"s_customer_id": "k2", "is_kol": True,  "rising_star": False, "kol_score": 0.5},
        {"s_customer_id": "k3", "is_kol": True,  "rising_star": False, "kol_score": 0.1},
        {"s_customer_id": "r1", "is_kol": False, "rising_star": True,  "kol_score": 0.95},  # breakout
        {"s_customer_id": "r2", "is_kol": False, "rising_star": True,  "kol_score": 0.05},
    ]
    out = {h["s_customer_id"]: h for h in mod.assign_tiers(hcps, 85, 60)}
    assert out["k1"]["tier"] == "A"
    assert out["r1"]["tier"] is None            # rising stars are not tiered
    assert out["r1"]["breakout"] is True        # would have reached tier-A level
    assert out["r2"]["breakout"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest b_kol_identification/tests/test_04_assemble.py::test_tiers_computed_over_kol_pool_only_and_breakout -v`
Expected: FAIL (current `assign_tiers` tiers all HCPs and has no breakout).

- [ ] **Step 3: Rewrite `assign_tiers`**

```python
def assign_tiers(hcps: list, tier_a_pct: float, tier_b_pct: float) -> list:
    """Percentile A/B/C thresholds computed over the KOL pool only (is_kol==True).
    Non-KOLs get tier=None. Rising stars whose score clears the tier-A threshold are
    flagged breakout (exceptional emerging voices)."""
    kol_scores = sorted(h["kol_score"] for h in hcps if h.get("is_kol"))
    if kol_scores:
        n = len(kol_scores)
        thresh_a = kol_scores[min(int(n * tier_a_pct / 100), n - 1)]
        thresh_b = kol_scores[min(int(n * tier_b_pct / 100), n - 1)]
    else:
        thresh_a = thresh_b = float("inf")   # empty pool -> nobody tiers
    out = []
    for h in hcps:
        if h.get("is_kol"):
            tier = ("A" if h["kol_score"] >= thresh_a
                    else "B" if h["kol_score"] >= thresh_b else "C")
        else:
            tier = None
        breakout = bool(h.get("rising_star") and h["kol_score"] >= thresh_a)
        out.append({**h, "tier": tier, "breakout": breakout})
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest b_kol_identification/tests/test_04_assemble.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add b_kol_identification/04_assemble_kols.py b_kol_identification/tests/test_04_assemble.py
git commit -m "feat(kol): tier over KOL pool only; flag breakout rising stars"
```

---

### Task 9: `build_score_trajectory` (fixed-yardstick reconstruction)

**Files:**
- Modify: `b_kol_identification/04_assemble_kols.py`
- Test: `b_kol_identification/tests/test_04_assemble.py`

**Interfaces:**
- Produces:
  - `pctile_in(sorted_ref: list, value: float) -> float` — fixed-yardstick percentile (fraction of ref strictly less + half the ties) / (n-1), clamped to [0,1]; 0.0 if `len(ref)<2`.
  - `build_score_trajectory(hcp, anchor_year, span, window_years, ref_relevance, ref_reach, weights, thresh_a, thresh_b, authors_by_pmid) -> list` of `{"year", "relevance", "reach", "ratio", "tenure", "score", "tier"}` for each year in `[anchor-span+1 .. anchor]`. Web counts held constant; reach(Y) from co-authors of verified pmids with year ≤ Y; ratio(Y) numerator windowed, denominator cumulative from `total_pub_by_year`.
- Consumes: `verified_pmid_years` (Task 3), `total_pub_by_year` (Tasks 1–3), `compute_reach` (existing), `verified_web_count`, `total_web_sources`.

- [ ] **Step 1: Write the failing tests**

```python
def test_pctile_in_basic():
    assert mod.pctile_in([0, 10, 20, 30], 20) == 20 / 30  # placeholder guard, see below
```

Replace the placeholder with the real expectation once you read the formula; use these instead:

```python
def test_pctile_in_ranks_within_fixed_reference():
    ref = [0.0, 1.0, 2.0, 3.0]        # n=4
    assert mod.pctile_in(ref, -5.0) == 0.0
    assert mod.pctile_in(ref, 5.0) == 1.0
    assert abs(mod.pctile_in(ref, 1.0) - (1 / 3)) < 1e-9   # 1 strictly-less of 3 gaps

def test_pctile_in_degenerate_reference_is_zero():
    assert mod.pctile_in([5.0], 5.0) == 0.0

def test_score_trajectory_grows_and_marks_tenure():
    hcp = {
        "verified_web_count": 0, "total_web_sources": 0,
        "verified_pubmed_count": 3,
        "verified_pmid_years": {"p1": 2016, "p2": 2017, "p3": 2018},
        "verified_pubmed_years": {"2016": 1, "2017": 1, "2018": 1},
        "total_pub_by_year": {"2016": 1, "2017": 1, "2018": 1},
    }
    authors_by_pmid = {"p1": [{"ORCID": "a", "FIRSTNAME": "Co", "LASTNAME": "One", "AFFILIATION": "U"}],
                       "p2": [{"ORCID": "b", "FIRSTNAME": "Co", "LASTNAME": "Two", "AFFILIATION": "U"}],
                       "p3": [{"ORCID": "c", "FIRSTNAME": "Co", "LASTNAME": "Three", "AFFILIATION": "U"}]}
    ref_rel = [0.0, 1.0, 2.0, 3.0]; ref_rch = [0.0, 1.0, 2.0, 3.0]
    traj = mod.build_score_trajectory(hcp, anchor_year=2018, span=3, window_years=10,
                ref_relevance=ref_rel, ref_reach=ref_rch,
                weights={"relevance": 0.6, "reach": 0.25, "ratio": 0.15},
                thresh_a=0.8, thresh_b=0.4, authors_by_pmid=authors_by_pmid)
    assert [p["year"] for p in traj] == [2016, 2017, 2018]
    assert traj[0]["relevance"] == 1 and traj[2]["relevance"] == 3      # windowed cumulative
    assert traj[0]["reach"] == 1 and traj[2]["reach"] == 3              # co-authors accrue
    assert traj[2]["score"] >= traj[0]["score"]                        # climbs
    assert traj[0]["tenure"] == 1 and traj[2]["tenure"] == 3
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest b_kol_identification/tests/test_04_assemble.py -k "trajectory or pctile" -v`
Expected: FAIL (functions not defined).

- [ ] **Step 3: Implement**

```python
def pctile_in(sorted_ref: list, value: float) -> float:
    """Fixed-yardstick percentile of `value` against a precomputed sorted reference
    distribution (the final pool's raw factor values). Mirrors normalize_values'
    percentile core: (strictly-less + half the ties) / (n-1), clamped to [0,1]."""
    n = len(sorted_ref)
    if n < 2:
        return 0.0
    less = sum(1 for x in sorted_ref if x < value)
    equal = sum(1 for x in sorted_ref if x == value)
    p = (less + 0.5 * max(equal - 1, 0)) / (n - 1)
    return max(0.0, min(1.0, p))


def build_score_trajectory(hcp: dict, anchor_year: int, span: int, window_years: int,
                           ref_relevance: list, ref_reach: list, weights: dict,
                           thresh_a: float, thresh_b: float, authors_by_pmid: dict) -> list:
    """Replay the composite as of each year in [anchor-span+1 .. anchor], holding web
    constant (no timestamps), against a FIXED reference distribution so the line shows
    the individual's growth, not pool churn."""
    web_v = hcp.get("verified_web_count", 0)
    web_tot = hcp.get("total_web_sources", 0)
    pmid_years = {str(k): int(v) for k, v in hcp.get("verified_pmid_years", {}).items()}
    tot_by_year = {int(y): int(c) for y, c in hcp.get("total_pub_by_year", {}).items() if str(y).isdigit()}
    ref_rel = sorted(ref_relevance); ref_rch = sorted(ref_reach)
    years_present = [int(y) for y in hcp.get("verified_pubmed_years", {}).keys() if str(y).isdigit()]
    first_year = min(years_present) if years_present else None

    out = []
    for y in range(int(anchor_year) - int(span) + 1, int(anchor_year) + 1):
        in_window = [p for p, yr in pmid_years.items() if y - window_years <= yr <= y]
        relevance = web_v + len(in_window)
        reach = compute_reach(in_window, authors_by_pmid, "", "")["distinct_coauthors"]
        cum_pub = sum(c for yr, c in tot_by_year.items() if yr <= y)
        denom = web_tot + cum_pub
        ratio = min(relevance / denom, 1.0) if denom > 0 else 0.0
        score = (weights["relevance"] * pctile_in(ref_rel, relevance)
                 + weights["reach"] * pctile_in(ref_rch, reach)
                 + weights["ratio"] * ratio)
        tier = "A" if score >= thresh_a else "B" if score >= thresh_b else "C"
        tenure = (y - first_year + 1) if (first_year is not None and y >= first_year) else 0
        out.append({"year": y, "relevance": relevance, "reach": reach, "ratio": round(ratio, 4),
                    "tenure": tenure, "score": round(score, 4), "tier": tier})
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest b_kol_identification/tests/test_04_assemble.py -k "trajectory or pctile" -q`
Expected: PASS. (Delete the placeholder `test_pctile_in_basic` if it was pasted.)

- [ ] **Step 5: Commit**

```bash
git add b_kol_identification/04_assemble_kols.py b_kol_identification/tests/test_04_assemble.py
git commit -m "feat(kol): fixed-yardstick per-year score trajectory reconstruction"
```

---

### Task 10: Wire Stage 04 `main()` together

**Files:**
- Modify: `b_kol_identification/04_assemble_kols.py:244-324` (`main()`)
- Test: manual pipeline dry-run guidance below (no unit hook for `main()`; logic is covered by Tasks 4–9).

**Interfaces:**
- Consumes: all functions from Tasks 4–9. Produces `kol_final.json` HCPs with new fields: `relevant_tenure`, `first_relevant_year`, `rising_star`, `is_kol`, `tier`, `breakout`, `total_pub_by_year`, `score_trajectory` (top-`top_n_report` only).

- [ ] **Step 1: Edit `main()` — read new params**

After `sf, sc = cfg["snowflake"], cfg["scoring"]` add:

```python
    fn = cfg["funnel"]
    anchor_year = int(data.get("anchor_year") or 0)
    rep_n = int(cfg["report"]["top_n_report"])
```

(`data` is loaded a few lines below; move the `anchor_year` assignment to right after `data = json.load(f)`.)

- [ ] **Step 2: Replace the rising-star call + add tenure/buckets**

Replace the current `hcps = flag_rising_stars(hcps, int(sc["rising_star_min_pubs"]), float(sc["rising_star_growth"]))` line with the new signature, and add tenure/floor tagging **after** reach+ratio are attached and **after** `apply_composite` (buckets need `ratio` and `kol_score`). Concretely, the ordering in `main()` becomes:

1. attach `reach`, `ratio`, `affiliations` (existing loop) — unchanged.
2. `hcps = apply_composite(...)` (existing) — unchanged.
3. New block:

```python
    # tenure + buckets (mutually exclusive by construction)
    hcps = flag_rising_stars(hcps, int(sc["rising_star_min_pubs"]),
                             int(sc["rising_star_max_tenure_years"]), anchor_year)
    for h in hcps:
        ten = compute_tenure(h.get("verified_pubmed_years", {}), anchor_year)
        h["relevant_tenure"] = ten["relevant_tenure"]
        h["first_relevant_year"] = ten["first_relevant_year"]
        h["is_kol"] = (not h["rising_star"]) and passes_kol_floors(
            h, anchor_year, int(sc["kol_floor_min_verified"]), float(sc["kol_floor_min_ratio"]),
            int(sc["kol_floor_active_within_yrs"]), int(sc["kol_floor_min_coauthors"]))
```

- [ ] **Step 3: Tiers over KOL pool, sort, trajectory**

Keep the existing `latest_year`/sort/`drop_zero_score` lines. Replace the `assign_tiers` call site so tiers use the new KOL-pool logic (signature unchanged, behavior from Task 8), and add trajectory build for the reported top-N. After `hcps = assign_tiers(hcps, float(sc["tier_a_percentile"]), float(sc["tier_b_percentile"]))` add:

```python
    # score-development trajectories for the reported KOLs (fixed yardstick = final pool)
    ref_rel = [h.get("verified_web_count", 0) + h.get("verified_pubmed_count", 0) for h in hcps]
    ref_rch = [h.get("reach", {}).get("distinct_coauthors", 0) for h in hcps]
    kol_scores = sorted((h["kol_score"] for h in hcps if h.get("is_kol")))
    if kol_scores:
        n = len(kol_scores)
        t_a = kol_scores[min(int(n * float(sc["tier_a_percentile"]) / 100), n - 1)]
        t_b = kol_scores[min(int(n * float(sc["tier_b_percentile"]) / 100), n - 1)]
    else:
        t_a = t_b = float("inf")
    span = int(data.get("pub_history_years") or int(fn["pub_history_years"]))
    weights = {"relevance": float(sc["weight_relevance"]), "reach": float(sc["weight_reach"]),
               "ratio": float(sc["weight_ratio"])}
    for h in hcps[:rep_n]:
        h["score_trajectory"] = build_score_trajectory(
            h, anchor_year, span, int(fn["pubmed_window_years"]), ref_rel, ref_rch,
            weights, t_a, t_b, authors_by_pmid)
```

(`authors_by_pmid` is already built earlier in `main()` for reach; reuse it.)

- [ ] **Step 4: Run the full Stage-04 test file**

Run: `.venv/bin/python -m pytest b_kol_identification/tests/test_04_assemble.py -q`
Expected: PASS (all unit tests green).

- [ ] **Step 5: Syntax/smoke check `main()` wiring**

Run: `.venv/bin/python -c "import importlib.util,os; s=importlib.util.spec_from_file_location('asm','b_kol_identification/04_assemble_kols.py'); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); print('import ok', hasattr(m,'build_score_trajectory'))"`
Expected: `import ok True`.

- [ ] **Step 6: Commit**

```bash
git add b_kol_identification/04_assemble_kols.py
git commit -m "feat(kol): wire stage04 — tenure buckets, KOL-pool tiers, trajectories"
```

---

### Task 11: Report — stacked total-vs-relevant per-year bars

**Files:**
- Modify: `b_kol_identification/05_generate_report.py`
- Test: `b_kol_identification/tests/test_05_report.py`

**Interfaces:**
- Produces: `render_year_bars(total_by_year: dict, relevant_by_year: dict, all_years: list, width=190, height=44) -> str` — inline SVG, one bar per year: full-height segment = total pubs, darker inner segment = verified-relevant. Returns `''` when there is no data.
- Consumes: `total_pub_by_year` and `verified_pubmed_years` on each HCP; existing `_year_axis`/`all_years` helper.

- [ ] **Step 1: Write the failing test**

Match the importlib loader at the top of `test_05_report.py` (module exposed as `mod`). Add:

```python
def test_render_year_bars_stacks_total_and_relevant():
    svg = mod.render_year_bars({"2017": 4, "2018": 6}, {"2017": 1, "2018": 3},
                               ["2016", "2017", "2018"])
    assert svg.startswith("<svg") and svg.count("<rect") >= 4      # total + relevant per active year
    assert "</svg>" in svg

def test_render_year_bars_empty_when_no_data():
    assert mod.render_year_bars({}, {}, ["2016", "2017"]) == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest b_kol_identification/tests/test_05_report.py -k year_bars -v`
Expected: FAIL (`render_year_bars` not defined).

- [ ] **Step 3: Implement**

Add near `render_sparkline` in `05_generate_report.py`:

```python
def render_year_bars(total_by_year, relevant_by_year, all_years, width=190, height=44):
    """Grouped per-year bars: light column = all publications that year, dark inner
    column = the verified-relevant subset. Inline SVG (no CDN)."""
    tot = {str(y): int(v) for y, v in (total_by_year or {}).items()}
    rel = {str(y): int(v) for y, v in (relevant_by_year or {}).items()}
    if not tot and not rel:
        return ""
    years = list(all_years)
    peak = max([tot.get(y, 0) for y in years] + [rel.get(y, 0) for y in years] + [1])
    n = max(len(years), 1)
    bw = width / n
    pad = bw * 0.2
    base = height - 12
    rects, labels = [], []
    for i, y in enumerate(years):
        x = i * bw + pad
        w = bw - 2 * pad
        th = (tot.get(y, 0) / peak) * base
        rh = (rel.get(y, 0) / peak) * base
        if tot.get(y, 0):
            rects.append(f'<rect x="{x:.1f}" y="{base - th:.1f}" width="{w:.1f}" '
                         f'height="{th:.1f}" fill="{PALETTE["line"]}"/>')
        if rel.get(y, 0):
            rects.append(f'<rect x="{x:.1f}" y="{base - rh:.1f}" width="{w:.1f}" '
                         f'height="{rh:.1f}" fill="{PALETTE["accent"]}"/>')
        if y.endswith("0") or y.endswith("5"):
            labels.append(f'<text x="{x + w/2:.1f}" y="{height - 1}" font-size="7" '
                          f'text-anchor="middle" fill="{PALETTE["muted"]}">{y[2:]}</text>')
    return (f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
            f'role="img" aria-label="publications per year, total vs relevant">'
            f'{"".join(rects)}{"".join(labels)}</svg>')
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest b_kol_identification/tests/test_05_report.py -k year_bars -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add b_kol_identification/05_generate_report.py b_kol_identification/tests/test_05_report.py
git commit -m "feat(kol): report stacked total-vs-relevant per-year bars"
```

---

### Task 12: Report — score-development line chart with tier bands

**Files:**
- Modify: `b_kol_identification/05_generate_report.py`
- Test: `b_kol_identification/tests/test_05_report.py`

**Interfaces:**
- Produces: `render_score_dev_chart(trajectory: list, thresh_a: float, thresh_b: float, width=320, height=120) -> str` — inline SVG with A/B/C horizontal tier bands, a polyline of `score` over `year`, and a marker at the first year `tenure > rising_max` (crossing into KOL tenure). Returns `''` for empty/one-point trajectories. The tenure-crossing year uses a module constant `RISING_MAX_TENURE_DEFAULT = 3` (kept in sync with config in the render wiring, Task 13).
- Consumes: `score_trajectory` produced in Task 10.

- [ ] **Step 1: Write the failing test**

```python
def test_render_score_dev_chart_has_bands_and_line():
    traj = [{"year": 2016, "score": 0.1, "tier": "C", "tenure": 1},
            {"year": 2017, "score": 0.4, "tier": "B", "tenure": 2},
            {"year": 2018, "score": 0.9, "tier": "A", "tenure": 3}]
    svg = mod.render_score_dev_chart(traj, thresh_a=0.8, thresh_b=0.4)
    assert svg.startswith("<svg") and "<polyline" in svg
    assert svg.count("<rect") >= 3           # three tier bands
    assert "</svg>" in svg

def test_render_score_dev_chart_empty_for_short_series():
    assert mod.render_score_dev_chart([], 0.8, 0.4) == ""
    assert mod.render_score_dev_chart([{"year": 2018, "score": 0.5, "tier": "C", "tenure": 1}], 0.8, 0.4) == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest b_kol_identification/tests/test_05_report.py -k score_dev -v`
Expected: FAIL (`render_score_dev_chart` not defined).

- [ ] **Step 3: Implement**

```python
RISING_MAX_TENURE_DEFAULT = 3

def render_score_dev_chart(trajectory, thresh_a, thresh_b, width=320, height=120):
    """Line chart of composite score over years with A/B/C tier bands and a marker at
    the year the HCP crossed from rising-star tenure into KOL tenure. Inline SVG."""
    pts = [p for p in (trajectory or []) if isinstance(p.get("score"), (int, float))]
    if len(pts) < 2:
        return ""
    pad_l, pad_r, pad_t, pad_b = 6, 6, 6, 14
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b
    ta = max(0.0, min(1.0, float(thresh_a))) if thresh_a != float("inf") else 1.0
    tb = max(0.0, min(1.0, float(thresh_b))) if thresh_b != float("inf") else 0.6
    def sy(v):  # score 0..1 -> y
        return pad_t + (1 - max(0.0, min(1.0, v))) * plot_h
    bands = [  # (top_v, bottom_v, colour)
        (1.0, ta, PALETTE.get("tierA", "#1f8a5b")),
        (ta, tb, PALETTE.get("tierB", "#3b5b92")),
        (tb, 0.0, PALETTE.get("tierC", "#6b7684")),
    ]
    rects = "".join(
        f'<rect x="{pad_l}" y="{sy(top):.1f}" width="{plot_w}" '
        f'height="{max(0.0, sy(bot) - sy(top)):.1f}" fill="{col}" opacity="0.10"/>'
        for top, bot, col in bands)
    n = len(pts)
    xs = [pad_l + (i / (n - 1)) * plot_w for i in range(n)]
    poly = " ".join(f"{xs[i]:.1f},{sy(pts[i]['score']):.1f}" for i in range(n))
    line = f'<polyline points="{poly}" fill="none" stroke="{PALETTE["accent"]}" stroke-width="2"/>'
    dots = "".join(f'<circle cx="{xs[i]:.1f}" cy="{sy(pts[i]["score"]):.1f}" r="2.2" '
                   f'fill="{PALETTE["accent"]}"/>' for i in range(n))
    # tenure-crossing marker: first year tenure exceeds the rising limit
    marker = ""
    for i, p in enumerate(pts):
        if p.get("tenure", 0) == RISING_MAX_TENURE_DEFAULT + 1:
            marker = (f'<line x1="{xs[i]:.1f}" y1="{pad_t}" x2="{xs[i]:.1f}" '
                      f'y2="{pad_t + plot_h}" stroke="{PALETTE.get("amber", "#b7791f")}" '
                      f'stroke-width="1" stroke-dasharray="3 2"/>'
                      f'<text x="{xs[i]:.1f}" y="{height - 3}" font-size="7" text-anchor="middle" '
                      f'fill="{PALETTE.get("amber", "#b7791f")}">→ KOL tenure</text>')
            break
    return (f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
            f'role="img" aria-label="score development over years">'
            f'{rects}{marker}{line}{dots}</svg>')
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest b_kol_identification/tests/test_05_report.py -k score_dev -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add b_kol_identification/05_generate_report.py b_kol_identification/tests/test_05_report.py
git commit -m "feat(kol): report per-KOL score-development chart with tier bands"
```

---

### Task 13: Report wiring — total pubs, career labels, disjoint counts, callouts, charts in profiles

**Files:**
- Modify: `b_kol_identification/05_generate_report.py`
- Test: `b_kol_identification/tests/test_05_report.py`

**Interfaces:**
- Produces: `career_stage_label(hcp) -> str` ("Emerging (≤3y)" for rising stars, "Established" for KOLs with tenure, "—" for web-only/unknown); `established_new_to_topic(hcp) -> bool` (long total-publication span but short relevant tenure). Wires `render_year_bars` and `render_score_dev_chart` into each profile; updates the dashboard so KOL and Rising-Star counts are disjoint; adds the "established, new to this indication" callout and the fixed-yardstick / web-baseline / no-entry-exit explainer lines.
- Consumes: HCP fields from Task 10; `render_year_bars` (Task 11), `render_score_dev_chart` (Task 12).

- [ ] **Step 1: Write the failing tests**

```python
def test_career_stage_label_variants():
    assert mod.career_stage_label({"rising_star": True, "relevant_tenure": 2}) == "Emerging (≤3y)"
    assert mod.career_stage_label({"is_kol": True, "relevant_tenure": 9}) == "Established"
    assert mod.career_stage_label({"relevant_tenure": None}) == "—"

def test_established_new_to_topic_detects_veteran_pivot():
    # publishes since 2008 (long total span) but first RELEVANT year 2017 (short tenure)
    hcp = {"total_pub_by_year": {"2008": 2, "2012": 3, "2017": 1, "2018": 2},
           "relevant_tenure": 2}
    assert mod.established_new_to_topic(hcp) is True
    assert mod.established_new_to_topic({"total_pub_by_year": {"2017": 1}, "relevant_tenure": 2}) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest b_kol_identification/tests/test_05_report.py -k "career_stage or established_new" -v`
Expected: FAIL (functions not defined).

- [ ] **Step 3: Implement the pure helpers**

```python
def career_stage_label(hcp):
    if hcp.get("rising_star"):
        return "Emerging (≤3y)"
    if hcp.get("relevant_tenure") is None:
        return "—"
    return "Established"

def established_new_to_topic(hcp, min_total_span=8, max_relevant_tenure=3):
    """Long overall publication history but only recently relevant to THIS indication."""
    yrs = [int(y) for y in (hcp.get("total_pub_by_year") or {}).keys() if str(y).isdigit()]
    if not yrs:
        return False
    total_span = max(yrs) - min(yrs) + 1
    ten = hcp.get("relevant_tenure")
    return total_span >= min_total_span and ten is not None and ten <= max_relevant_tenure
```

- [ ] **Step 4: Wire into the rendered report (no new unit test — visual/integration)**

Make these edits in the render path:
- In the KOL ranking rows and Individual-profile cards, show `total_pubmed_sources` as "Total publications" and `career_stage_label(h)` as a chip.
- Replace the profile `render_sparkline(...)` call with `render_year_bars(h.get("total_pub_by_year", {}), h.get("verified_pubmed_years", {}), all_years)`, and add `render_score_dev_chart(h.get("score_trajectory", []), t_a, t_b)` beneath it (compute `t_a`/`t_b` once from the KOL-pool scores, mirroring Task 10; add a small `_kol_tier_thresholds(hcps, a_pct, b_pct)` helper in the report to avoid duplicating the math).
- Dashboard stat cards: compute `kols = sum(1 for h in hcps if h.get("is_kol"))` and `rising = sum(1 for h in hcps if h.get("rising_star"))` — these are now disjoint; label them "KOLs" and "Rising stars".
- Rising Stars section: show `relevant_tenure` and a "Breakout" pill when `h.get("breakout")`.
- Add a callout listing HCPs where `established_new_to_topic(h)` — heading "Established, new to this indication".
- Add explainer lines (use the existing `section_explainer`/`_splice_explainer` mechanism): score-dev chart uses a *fixed yardstick* (today's pool as the ruler); *web is a constant baseline* (no timestamps); the chart *cannot show pool entry/exit or demotions* — that is the two-run backtest's job (Stage 06); it applies *today's verification verdicts to historical years*.
- Excel export: add `total_publications`, `relevant_tenure`, `is_kol`, `breakout` columns (one row per KOL).

- [ ] **Step 5: Full report render smoke test**

If a `data/kol_final.json` exists from a prior run, regenerate and eyeball; otherwise rely on unit tests + import check:

Run: `.venv/bin/python -m pytest b_kol_identification/tests/test_05_report.py -q`
Expected: PASS.

Run: `.venv/bin/python -c "import importlib.util; s=importlib.util.spec_from_file_location('rpt','b_kol_identification/05_generate_report.py'); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); print('ok')"`
Expected: `ok`.

- [ ] **Step 6: Commit**

```bash
git add b_kol_identification/05_generate_report.py b_kol_identification/tests/test_05_report.py
git commit -m "feat(kol): report total pubs, career labels, disjoint buckets, dev charts, callouts"
```

---

### Task 14: Docs + full-suite verification

**Files:**
- Modify: `b_kol_identification/CLAUDE.md`
- Test: full suite.

- [ ] **Step 1: Update the service CLAUDE.md**

In the scoring overview and the `04_assemble_kols.py` / `05_generate_report.py` rows, document: the tenure partition (rising star = ≤`rising_star_max_tenure_years` relevant tenure + active; mutually exclusive from KOLs), the four absolute KOL floors, ratio-used-raw, tiers-over-KOL-pool-only, the breakout badge, the widened windows (`pubmed_window_years=10`, `pub_history_years=10`, `top_n_candidates=100`), the new Stage-01 all-pubs-per-year series, the `total_pub_by_year`/`verified_pmid_years` fields, and the report's stacked bars + score-development chart (with its fixed-yardstick / web-baseline / no-entry-exit caveats). Update "Confirmed decisions" and reference this spec/plan.

- [ ] **Step 2: Run the full suite**

Run: `.venv/bin/python -m pytest b_kol_identification/tests -q`
Expected: PASS (all tests, including the updated Stage-04/05 files).

- [ ] **Step 3: Commit**

```bash
git add b_kol_identification/CLAUDE.md
git commit -m "docs(kol): document tenure partition, KOL floors, dev charts"
```

---

## Self-Review

**Spec coverage:**
- §3 two axes / ratio-raw → Task 4; tenure → Task 5; partition (rising) → Task 6; floors → Task 7; KOL-pool tiers + breakout → Task 8. ✓
- §4 four floors (incl. co-author waiver, neutral-ratio rejection, web-current recency) → Task 7 tests. ✓
- §5 config knobs + windows + top_n bump → Task 1. Funnel-starvation is flagged as a live-run validation item (not code this iteration) — recorded in spec §5, no task needed. ✓
- §6 Stage 01 all-pubs query → Task 2; 02/03 threading + `verified_pmid_years` → Task 3; Stage 04 wiring → Task 10; Stage 05 (total pubs, stacked bars, dev chart, callouts, disjoint counts, explainers, Excel) → Tasks 11–13; Stage 06 unchanged → no task. ✓
- §7 fixed-yardstick reconstruction → Task 9. ✓
- §8 edge cases: web-only (Tasks 7, 9), thin/neutral ratio (Task 7), tenure-eligible-but-weak (Tasks 7–8), prolific newcomer breakout (Task 8), empty KOL pool (Task 8 `inf` thresholds). ✓
- §10 out-of-scope (two-run entry/exit, seniority, congresses, vector arm) — intentionally no tasks. ✓

**Placeholder scan:** One deliberate placeholder in Task 9 Step 1 (`test_pctile_in_basic`) is explicitly flagged to be replaced by the real assertions in the same step and deleted in Step 4 — not a lingering TODO. No other placeholders; all code steps carry full code.

**Type consistency:** `apply_composite`/`normalize_values`/`compute_reach`/`compute_ratio` signatures reused as they exist in `04_assemble_kols.py`. `flag_rising_stars` new signature `(hcps, min_pubs, max_tenure_years, anchor_year)` is used consistently in Tasks 6 and 10. `assign_tiers(hcps, a_pct, b_pct)` signature unchanged; behavior changed (Task 8) and call site in Task 10 matches. `build_score_trajectory` parameter names match between Task 9 definition and Task 10 call. `render_year_bars`/`render_score_dev_chart`/`career_stage_label`/`established_new_to_topic` names consistent across Tasks 11–13. `pctile_in` used only within Task 9. ✓
