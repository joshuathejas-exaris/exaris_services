# KOL Report & Excel Adjustments — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add rising-star score charts + score drill-down, declutter KOL profile cards, align the two profile charts, and add two audit sheets to the Excel — all traceable back to source data.

**Architecture:** Pure additions/edits to `05_generate_report.py` (HTML renderers + `write_excel`) plus a one-predicate widening in `04_assemble_kols.py`. New Excel sheets join `sources.json` (all sources) with `wiki.json` (verified claims); the per-year sheet reads `score_trajectory` already in `kol_final.json`.

**Tech Stack:** Python 3 (stdlib + `openpyxl`), inline-SVG charts, pytest (mock Snowflake/Bedrock).

## Global Constraints

- Self-contained HTML: no CDN/network/fonts; charts are inline SVG. (Do not introduce external assets.)
- Excel must always be produced: missing `sources.json`/`wiki.json` degrades a sheet to a note row, never crashes.
- Source `full_text`/abstracts are never written into the Excel — only `source_id`, `kind`, `url`, `pmid` and verified-claim fields.
- Rising Stars and KOLs are disjoint buckets; do not merge them.
- Run tests with the repo venv: `cd /d/Dev/exaris_services && .venv/bin/python -m pytest b_kol_identification/tests -q`.
- Stage 04 cannot be executed in this environment (no Snowflake/AWS). Deliver + unit-test the C7 code; the user re-runs Stage 04 on the DB sandbox.

---

### Task 1: Stage-04 trajectory coverage (C7)

Widen the score-trajectory build from "top-N KOLs" to "every KOL and every rising star", via a small testable helper.

**Files:**
- Modify: `b_kol_identification/04_assemble_kols.py` (add `trajectory_targets`; change loop at line ~436; remove now-unused `rep_n`)
- Test: `b_kol_identification/tests/test_04_assemble.py`

**Interfaces:**
- Produces: `trajectory_targets(hcps: list) -> list` — HCPs with `is_kol` OR `rising_star`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_04_assemble.py`:

```python
def test_trajectory_targets_includes_all_kols_and_rising_stars():
    hcps = [{"name": "K", "is_kol": True, "rising_star": False},
            {"name": "R", "is_kol": False, "rising_star": True},
            {"name": "N", "is_kol": False, "rising_star": False}]
    assert [h["name"] for h in mod.trajectory_targets(hcps)] == ["K", "R"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /d/Dev/exaris_services && .venv/bin/python -m pytest b_kol_identification/tests/test_04_assemble.py::test_trajectory_targets_includes_all_kols_and_rising_stars -q`
Expected: FAIL with `AttributeError: module ... has no attribute 'trajectory_targets'`

- [ ] **Step 3: Add the helper**

Add near the other module-level helpers in `04_assemble_kols.py` (e.g. just above `def main`):

```python
def trajectory_targets(hcps: list) -> list:
    """HCPs that get a score-development trajectory: every KOL and every rising star
    (not only the reported top-N), so the report's rising-star score charts and the
    Excel per-year sheet have full coverage."""
    return [h for h in hcps if h.get("is_kol") or h.get("rising_star")]
```

- [ ] **Step 4: Rewire the trajectory loop**

In `main()`, replace:

```python
    # Trajectories only for the top-N KOLs shown in the report's KOL Profiles (hcps is
    # already sorted by kol_score desc), so a KOL ranked just below a higher-scoring
    # rising star still gets a chart. Rising stars use the per-year bars, not trajectories.
    for h in [x for x in hcps if x.get("is_kol")][:rep_n]:
```

with:

```python
    # Trajectories for every KOL and every rising star (see trajectory_targets): the
    # report's rising-star Score-development charts and the Excel per-year sheet both
    # need coverage beyond the reported top-N.
    for h in trajectory_targets(hcps):
```

- [ ] **Step 5: Remove the now-unused `rep_n`**

Run: `grep -n "rep_n" b_kol_identification/04_assemble_kols.py`
If the only remaining hit is the assignment `rep_n = int(cfg["report"]["top_n_report"])`, delete that line. (If any other use exists, leave it.)

- [ ] **Step 6: Run tests**

Run: `cd /d/Dev/exaris_services && .venv/bin/python -m pytest b_kol_identification/tests/test_04_assemble.py -q`
Expected: PASS (new test + all existing stage-04 tests)

- [ ] **Step 7: Commit**

```bash
git add b_kol_identification/04_assemble_kols.py b_kol_identification/tests/test_04_assemble.py
git commit -m "feat(kol): build score trajectories for all KOLs + rising stars"
```

---

### Task 2: Rising Stars — Score-development section + score drill-down (C1, C2)

`render_rising_stars` gains `weights`, `t_a`, `t_b`, `rising_max`; add a per-row score
breakdown to the table and a new "Score development" section of charts below the bars.

**Files:**
- Modify: `b_kol_identification/05_generate_report.py` (`render_rising_stars` ~line 402; call site in `build_report_html` ~line 843)
- Test: `b_kol_identification/tests/test_05_report.py`

**Interfaces:**
- Consumes: `render_score_dev_chart(trajectory, thresh_a, thresh_b, rising_max=...)`, `render_score_breakdown(hcp, weights)`, `DEFAULT_WEIGHTS`, `RISING_MAX_TENURE_DEFAULT` (all already defined).
- Produces: `render_rising_stars(hcps, all_years, weights=None, t_a=float("inf"), t_b=float("inf"), rising_max=RISING_MAX_TENURE_DEFAULT) -> str`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_05_report.py`:

```python
def _rising_hcp_with_traj():
    return {"name": "Rita Stern", "specialty": "Innere Medizin", "city": "Kiel",
            "rising_star": True, "relevant_tenure": 2, "kol_score": 0.71,
            "verified_pubmed_years": {"2022": 2, "2023": 3},
            "total_pub_by_year": {"2022": 3, "2023": 4},
            "theme_labels": [{"term_key": "CF_OBESITY", "term_en": "Obesity", "count": 3}],
            "norm_relevance": 0.8, "norm_reach": 0.5, "norm_ratio": 0.6,
            "factor_contributions": {"relevance": 0.48, "reach": 0.12, "ratio": 0.09},
            "reach": {"distinct_coauthors": 5, "distinct_affiliations": 4},
            "ratio": {"ratio": 0.6, "denominator": 10},
            "verified_web_count": 2, "verified_pubmed_count": 5,
            "top_quotes": [{"quote": "q", "url": "http://x"}],
            "score_trajectory": [{"year": 2021, "score": 0.3, "tier": "C", "tenure": 0},
                                 {"year": 2022, "score": 0.5, "tier": "B", "tenure": 1},
                                 {"year": 2023, "score": 0.71, "tier": "B", "tenure": 2}]}


def test_rising_stars_has_score_development_section_with_chart():
    html = mod.render_rising_stars([_rising_hcp_with_traj()], ["2021", "2022", "2023"],
                                   weights={"relevance": 0.6, "reach": 0.25, "ratio": 0.15},
                                   t_a=0.8, t_b=0.4)
    assert "Score development" in html
    assert "<polyline" in html            # the dev line chart rendered


def test_rising_stars_row_has_score_breakdown():
    html = mod.render_rising_stars([_rising_hcp_with_traj()], ["2021", "2022", "2023"],
                                   weights={"relevance": 0.6, "reach": 0.25, "ratio": 0.15})
    assert "score-breakdown" in html      # the <details> drill-down is present in the table
    assert "how it was scored" in html
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /d/Dev/exaris_services && .venv/bin/python -m pytest b_kol_identification/tests/test_05_report.py -k "rising_stars_has_score_development or rising_stars_row_has_score_breakdown" -q`
Expected: FAIL (`TypeError: render_rising_stars() got an unexpected keyword argument 'weights'`)

- [ ] **Step 3: Update `render_rising_stars`**

Replace the signature and body of `render_rising_stars` (currently lines ~402-439) with:

```python
def render_rising_stars(hcps, all_years, weights=None, t_a=float("inf"), t_b=float("inf"),
                        rising_max=RISING_MAX_TENURE_DEFAULT):
    weights = weights or DEFAULT_WEIGHTS
    stars = [h for h in hcps if h.get("rising_star")]
    if not stars:
        return ""
    # Table (on top) — one row per rising star with score, tenure and momentum.
    trows = ""
    for i, h in enumerate(stars, 1):
        # The Rising badge (Stage 04) is computed from verified_pubmed_years, so the
        # displayed recent/prior/ratio must come from the same (verified) field -- not
        # the unverified/candidate pub_by_year -- or the numbers won't justify the badge.
        recent, prior = _recent_prior(h.get("verified_pubmed_years", {}))
        ratio = f"{recent / max(prior, 1):.1f}×" if prior > 0 else "New voice"
        breakout = ' <span class="pill breakout">Breakout</span>' if h.get("breakout") else ""
        tenure = h.get("relevant_tenure")
        tenure_txt = f"{tenure}y on-topic" if isinstance(tenure, int) and tenure > 0 else "—"
        themes = ", ".join(_esc(t["term_en"]) for t in h.get("theme_labels", [])[:3])
        trows += (f'<tr><td>{i}</td>'
                  f'<td><b>{_esc(h.get("name",""))}</b> <span class="pill rise">Rising</span>{breakout}<br>'
                  f'<span class="muted">{_esc(h.get("specialty",""))}</span></td>'
                  f'<td>{_esc(h.get("city",""))}</td><td>{tenure_txt}</td>'
                  f'<td><b>{h.get("kol_score",0):.2f}</b>{render_score_breakdown(h, weights)}</td>'
                  f'<td><b>{recent}</b> recent vs <b>{prior}</b> prior &middot; {ratio}</td>'
                  f'<td>{themes}</td></tr>')
    table = (f'<table><thead><tr><th>#</th><th>Name / Specialty</th><th>City</th><th>Tenure</th>'
             f'<th>Composite score</th><th>Recent vs prior (verified pubs)</th><th>Themes</th>'
             f'</tr></thead><tbody>{trows}</tbody></table>')
    # Publication bars — total vs indication-relevant per year, same style as KOL profiles.
    bar_cards = ""
    for h in stars:
        bars = render_year_bars(h.get("total_pub_by_year", {}), h.get("verified_pubmed_years", {}), all_years)
        if not bars:
            continue
        bar_cards += (f'<div class="rising-card"><b>{_esc(h.get("name",""))}</b>'
                      f'<div style="margin:.4rem 0">{bars}'
                      f'<span class="muted spark-label">pubs/yr — total vs relevant</span></div></div>')
    bars_block = (f'<h3>Publication trajectory — total vs indication-relevant</h3>'
                  f'<div class="rising-grid">{bar_cards}</div>') if bar_cards else ""
    # Score development (separate section) — composite score over years with tier bands,
    # the same chart used on KOL profile cards.
    dev_cards = ""
    for h in stars:
        chart = render_score_dev_chart(h.get("score_trajectory", []), t_a, t_b, rising_max=rising_max)
        if not chart:
            continue
        dev_cards += (f'<div class="rising-card"><b>{_esc(h.get("name",""))}</b>'
                      f'<div style="margin:.4rem 0">{chart}'
                      f'<span class="muted spark-label">score development</span></div></div>')
    dev_block = (f'<h3>Score development — composite score over time</h3>'
                 f'<div class="rising-grid">{dev_cards}</div>') if dev_cards else ""
    return f'<h2>Rising Stars</h2>{table}{bars_block}{dev_block}'
```

- [ ] **Step 4: Thread params at the call site**

In `build_report_html`, in the `rising_section = _splice_explainer(` block (~line 843), replace `render_rising_stars(top, all_years)` with:

```python
        render_rising_stars(top, all_years, weights=weights, t_a=t_a, t_b=t_b, rising_max=rising_max)
```

- [ ] **Step 5: Run tests**

Run: `cd /d/Dev/exaris_services && .venv/bin/python -m pytest b_kol_identification/tests/test_05_report.py -q`
Expected: PASS (new tests + all existing, incl. `test_rising_stars_*`)

- [ ] **Step 6: Commit**

```bash
git add b_kol_identification/05_generate_report.py b_kol_identification/tests/test_05_report.py
git commit -m "feat(kol): rising stars get score-development charts + score drill-down"
```

---

### Task 3: KOL Profiles — remove score-breakdown dropdown + tenure sticker (C3, C4)

**Files:**
- Modify: `b_kol_identification/05_generate_report.py` (`render_profiles` ~lines 546-585)
- Test: `b_kol_identification/tests/test_05_report.py`

**Interfaces:**
- `render_profiles` signature unchanged (keeps `weights` param for call-site stability; it is simply no longer emitted in the card).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_05_report.py`:

```python
def test_profiles_omit_score_breakdown_dropdown():
    html = mod.render_profiles(DATA["hcps"], ["2023", "2024"], top_n=10)
    assert "score-breakdown" not in html
    assert "how it was scored" not in html


def test_profiles_omit_tenure_sticker():
    h = dict(DATA["hcps"][0]); h["relevant_tenure"] = 7
    html = mod.render_profiles([h], ["2023", "2024"], top_n=10)
    assert 'pill stage' not in html
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /d/Dev/exaris_services && .venv/bin/python -m pytest b_kol_identification/tests/test_05_report.py -k "profiles_omit" -q`
Expected: FAIL (both `score-breakdown`/`how it was scored` and `pill stage` currently present)

- [ ] **Step 3: Remove the tenure sticker**

In `render_profiles`, delete these two lines (~554-555):

```python
        tc = tenure_chip(h)
        stage = f' <span class="pill stage">{_esc(tc)}</span>' if tc else ""
```

and change the header cell (~line 580) from:

```python
            f'<div>{badge}{stage}</div></div>'
```

to:

```python
            f'<div>{badge}</div></div>'
```

- [ ] **Step 4: Remove the score-breakdown dropdown from the card**

Change the card's final line (~583) from:

```python
            f'<div>{themes}</div>{quotes}{render_score_breakdown(h, weights)}</div>'
```

to:

```python
            f'<div>{themes}</div>{quotes}</div>'
```

- [ ] **Step 5: Run tests**

Run: `cd /d/Dev/exaris_services && .venv/bin/python -m pytest b_kol_identification/tests/test_05_report.py -q`
Expected: PASS. (Note: `test_profiles_render_quotes_and_verified_source_breakdown` asserts quotes + the `meta` composite line, not the dropdown — confirm it still passes; if it asserted the dropdown text it must be updated, but it checks the composite `meta` line.)

- [ ] **Step 6: Commit**

```bash
git add b_kol_identification/05_generate_report.py b_kol_identification/tests/test_05_report.py
git commit -m "feat(kol): declutter KOL profile cards (drop score dropdown + tenure sticker)"
```

---

### Task 4: Align profile charts + add bar-chart axes (C8)

Make `render_year_bars` and `render_score_dev_chart` share a width so their x-axes span
the same length, and draw x/y axis lines on the bar chart.

**Files:**
- Modify: `b_kol_identification/05_generate_report.py` (add `PROFILE_CHART_W`; `render_year_bars` ~lines 326-356; confirm `render_score_dev_chart` width)
- Test: `b_kol_identification/tests/test_05_report.py`

**Interfaces:**
- Produces: module constant `PROFILE_CHART_W = 320`; `render_year_bars(..., width=PROFILE_CHART_W, height=54)` now emits two `<line>` axis elements.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_05_report.py`:

```python
def test_render_year_bars_has_axes_and_shared_width():
    svg = mod.render_year_bars({"2017": 4, "2018": 6}, {"2017": 1, "2018": 3},
                               ["2016", "2017", "2018"])
    assert svg.count("<line") >= 2                       # x-axis + y-axis
    assert f'width="{mod.PROFILE_CHART_W}"' in svg       # same width as the dev chart
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /d/Dev/exaris_services && .venv/bin/python -m pytest b_kol_identification/tests/test_05_report.py::test_render_year_bars_has_axes_and_shared_width -q`
Expected: FAIL (`AttributeError: ... 'PROFILE_CHART_W'` / no `<line>` in output)

- [ ] **Step 3: Add the shared width constant**

Add near the other module constants (below `RISING_MAX_TENURE_DEFAULT`, ~line 36):

```python
# Shared width for the two per-profile charts so their x-axes span the same length and
# the publication bars sit directly above the matching points on the score line below.
PROFILE_CHART_W = 320
```

- [ ] **Step 4: Rewrite `render_year_bars` with axes + shared geometry**

Replace `render_year_bars` (lines ~326-356) with:

```python
def render_year_bars(total_by_year, relevant_by_year, all_years, width=PROFILE_CHART_W, height=54):
    """Grouped per-year bars: light column = all publications that year, dark inner
    column = the verified-relevant subset. Drawn on an x/y axis frame, and sharing the
    score-development chart's width + horizontal insets so the columns line up above the
    score line. Inline SVG (no CDN)."""
    tot = {str(y): int(v) for y, v in (total_by_year or {}).items()}
    rel = {str(y): int(v) for y, v in (relevant_by_year or {}).items()}
    if not tot and not rel:
        return ""
    years = list(all_years)
    peak = max([tot.get(y, 0) for y in years] + [rel.get(y, 0) for y in years] + [1])
    # Same horizontal insets as render_score_dev_chart (pad_l/pad_r = 6) so both charts'
    # plotted regions start and end at the same x.
    pad_l, pad_r, pad_t, pad_b = 6, 6, 6, 12
    plot_w = width - pad_l - pad_r
    base = height - pad_b
    plot_h = base - pad_t
    n = max(len(years), 1)
    bw = plot_w / n
    pad = bw * 0.2
    rects, labels = [], []
    for i, y in enumerate(years):
        x = pad_l + i * bw + pad
        w = bw - 2 * pad
        th = (tot.get(y, 0) / peak) * plot_h
        rh = (rel.get(y, 0) / peak) * plot_h
        if tot.get(y, 0):
            rects.append(f'<rect x="{x:.1f}" y="{base - th:.1f}" width="{w:.1f}" '
                         f'height="{th:.1f}" fill="{PALETTE["line"]}"/>')
        if rel.get(y, 0):
            rects.append(f'<rect x="{x:.1f}" y="{base - rh:.1f}" width="{w:.1f}" '
                         f'height="{rh:.1f}" fill="{PALETTE["accent"]}"/>')
        if y.endswith("0") or y.endswith("5"):
            labels.append(f'<text x="{x + w/2:.1f}" y="{height - 1}" font-size="7" '
                          f'text-anchor="middle" fill="{PALETTE["muted"]}">{y[2:]}</text>')
    axes = (f'<line x1="{pad_l}" y1="{pad_t}" x2="{pad_l}" y2="{base:.1f}" '
            f'stroke="{PALETTE["muted"]}" stroke-width="1"/>'
            f'<line x1="{pad_l}" y1="{base:.1f}" x2="{width - pad_r}" y2="{base:.1f}" '
            f'stroke="{PALETTE["muted"]}" stroke-width="1"/>')
    return (f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
            f'role="img" aria-label="publications per year, total vs relevant">'
            f'{axes}{"".join(rects)}{"".join(labels)}</svg>')
```

- [ ] **Step 5: Confirm the dev chart shares the width**

Confirm `render_score_dev_chart` default is `width=320` (it is). To lock them together, change its signature default from `width=320` to `width=PROFILE_CHART_W`:

```python
def render_score_dev_chart(trajectory, thresh_a, thresh_b, width=PROFILE_CHART_W, height=120, rising_max=RISING_MAX_TENURE_DEFAULT):
```

- [ ] **Step 6: Run tests**

Run: `cd /d/Dev/exaris_services && .venv/bin/python -m pytest b_kol_identification/tests/test_05_report.py -k "year_bars or score_dev" -q`
Expected: PASS (new axes test + existing `test_render_year_bars_stacks_total_and_relevant` — still ≥4 `<rect>` — and `test_render_score_dev_chart_has_bands_and_line`)

- [ ] **Step 7: Commit**

```bash
git add b_kol_identification/05_generate_report.py b_kol_identification/tests/test_05_report.py
git commit -m "feat(kol): align profile bar/line charts to same width + add bar axes"
```

---

### Task 5: Excel sheet "LLM Wiki Verdicts" (C5)

Add source-level audit sheet joining `sources.json` + `wiki.json`; thread the file paths
through `write_excel` and `main`.

**Files:**
- Modify: `b_kol_identification/05_generate_report.py` (add helpers + sheet; `write_excel` signature; `main` wiring)
- Test: `b_kol_identification/tests/test_05_report.py`

**Interfaces:**
- Produces:
  - `WIKI_VERDICT_HEADERS: list[str]`
  - `build_wiki_verdict_rows(hcps, sources_data, wiki_data) -> list[list]`
  - `write_excel(data, path, sources_path=None, wiki_path=None) -> None`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_05_report.py`:

```python
def test_wiki_verdict_rows_mark_counted_and_rejected():
    hcps = [{"s_customer_id": "10", "name": "Anna Berg"}]
    sources = {"hcps": [{"s_customer_id": "10",
                         "web_sources": [{"source_id": "w1", "kind": "web", "url": "http://a"},
                                         {"source_id": "w2", "kind": "web", "url": "http://b"}],
                         "pubmed_sources": [{"source_id": "111", "kind": "pubmed",
                                             "pmid": "111", "url": "http://pm/111",
                                             "full_text": "SECRET BODY"}]}]}
    wiki = {"hcps": [{"s_customer_id": "10",
                      "claims": [{"source_id": "w1", "statement": "s1", "themes": ["Obesity"],
                                  "sentiment": "positive", "verified": True},
                                 {"source_id": "111", "statement": "s2", "themes": ["NASH"],
                                  "sentiment": "neutral", "verified": True}]}]}
    rows = mod.build_wiki_verdict_rows(hcps, sources, wiki)
    hdr = mod.WIKI_VERDICT_HEADERS
    verdicts = {r[hdr.index("URL")]: r[hdr.index("Verdict")] for r in rows}
    assert verdicts["http://a"] == "counted"      # w1 produced a claim
    assert verdicts["http://b"] == "rejected"      # w2 handed over, no claim
    assert verdicts["http://pm/111"] == "counted"
    assert all("SECRET BODY" not in str(c) for r in rows for c in r)   # full_text never leaks
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /d/Dev/exaris_services && .venv/bin/python -m pytest b_kol_identification/tests/test_05_report.py::test_wiki_verdict_rows_mark_counted_and_rejected -q`
Expected: FAIL (`AttributeError: ... 'build_wiki_verdict_rows'`)

- [ ] **Step 3: Add module-level helpers + headers**

Add above `def write_excel` in `05_generate_report.py`:

```python
WIKI_VERDICT_HEADERS = ["Rank", "Name", "Kind", "URL", "PMID", "Verdict",
                        "Verified claims", "Statements", "Themes", "Sentiments"]
_CELL_MAX = 1500  # keep joined statement cells readable, well under Excel's 32767 limit


def _load_json_safe(path):
    if not path:
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def build_wiki_verdict_rows(hcps, sources_data, wiki_data):
    """One row per source handed to the LLM (from sources.json), joined to the verified
    claims it produced (from wiki.json), keyed by source_id. Verdict is 'counted' when the
    source yielded >=1 verified claim, else 'rejected'. Source full_text is never emitted."""
    src_by_id = {h.get("s_customer_id"): h for h in (sources_data or {}).get("hcps", [])}
    claims_by_hcp = {}
    for wh in (wiki_data or {}).get("hcps", []):
        by_src = {}
        for c in wh.get("claims", []):
            by_src.setdefault(str(c.get("source_id")), []).append(c)
        claims_by_hcp[wh.get("s_customer_id")] = by_src
    rows = []
    for rank, h in enumerate(hcps, 1):
        cid = h.get("s_customer_id")
        sh = src_by_id.get(cid)
        if not sh:
            continue
        by_src = claims_by_hcp.get(cid, {})
        for s in (sh.get("web_sources", []) + sh.get("pubmed_sources", [])):
            claims = by_src.get(str(s.get("source_id")), [])
            statements = " | ".join(c.get("statement", "") for c in claims)[:_CELL_MAX]
            themes = ", ".join(sorted({t for c in claims for t in (c.get("themes") or [])}))
            sentiments = ", ".join(sorted({c.get("sentiment", "") for c in claims if c.get("sentiment")}))
            rows.append([rank, h.get("name", ""), s.get("kind", ""), s.get("url", ""),
                         s.get("pmid", ""), "counted" if claims else "rejected",
                         len(claims), statements, themes, sentiments])
    return rows


def _autosize(ws, headers, max_w=60):
    from openpyxl.utils import get_column_letter
    for ci in range(1, len(headers) + 1):
        col = get_column_letter(ci)
        best = max((len(str(c.value)) for c in ws[col] if c.value is not None), default=0)
        ws.column_dimensions[col].width = min(max_w, best) + 2
```

- [ ] **Step 4: Extend `write_excel` signature + append the sheet**

Change `def write_excel(data: dict, path: str) -> None:` to:

```python
def write_excel(data: dict, path: str, sources_path: str = None, wiki_path: str = None) -> None:
```

Immediately before `wb.save(path)` at the end of `write_excel`, insert:

```python
    ws.freeze_panes = "A2"
    _autosize(ws, headers)

    # Sheet 2 — LLM Wiki Verdicts: one row per source handed to the LLM, with the
    # 'counted'/'rejected' verdict and the verified claim(s) it produced.
    ws2 = wb.create_sheet("LLM Wiki Verdicts")
    ws2.append(WIKI_VERDICT_HEADERS)
    wiki_rows = build_wiki_verdict_rows(
        data["hcps"], _load_json_safe(sources_path), _load_json_safe(wiki_path))
    if wiki_rows:
        for r in wiki_rows:
            ws2.append(r)
    else:
        note = ["", "sources.json / wiki.json not found — run stages 02–03"]
        ws2.append(note + [""] * (len(WIKI_VERDICT_HEADERS) - len(note)))
    ws2.freeze_panes = "A2"
    _autosize(ws2, WIKI_VERDICT_HEADERS)
```

(`headers` is the existing local list built at the top of `write_excel`.)

- [ ] **Step 5: Wire `main` to pass the paths**

In `main()`, change:

```python
    write_excel(data, xlsx_path)
```

to:

```python
    write_excel(data, xlsx_path,
                sources_path=os.path.join(_DIR, "data", "sources.json"),
                wiki_path=os.path.join(_DIR, "data", "wiki.json"))
```

- [ ] **Step 6: Run tests**

Run: `cd /d/Dev/exaris_services && .venv/bin/python -m pytest b_kol_identification/tests/test_05_report.py -q`
Expected: PASS (new test + `test_write_excel_creates_one_row_per_kol` still green — `wb.active` remains the "KOLs" sheet).

- [ ] **Step 7: Commit**

```bash
git add b_kol_identification/05_generate_report.py b_kol_identification/tests/test_05_report.py
git commit -m "feat(kol): Excel LLM Wiki Verdicts sheet (per-source counted/rejected audit)"
```

---

### Task 6: Excel sheet "Score by Year" (C6)

Add per-year composite reconstruction from `score_trajectory`.

**Files:**
- Modify: `b_kol_identification/05_generate_report.py` (add helper + sheet)
- Test: `b_kol_identification/tests/test_05_report.py`

**Interfaces:**
- Produces:
  - `SCORE_YEAR_HEADERS: list[str]`
  - `build_score_year_rows(hcps) -> list[list]`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_05_report.py`:

```python
def test_score_year_rows_one_per_trajectory_year():
    hcps = [{"name": "Kai", "score_trajectory": [
                {"year": 2022, "score": 0.5, "relevance": 10, "reach": 3, "ratio": 0.6, "tenure": 1, "tier": "B"},
                {"year": 2023, "score": 0.7, "relevance": 12, "reach": 5, "ratio": 0.65, "tenure": 2, "tier": "A"}]},
            {"name": "NoTraj", "score_trajectory": []}]
    rows = mod.build_score_year_rows(hcps)
    hdr = mod.SCORE_YEAR_HEADERS
    assert len(rows) == 2                                  # NoTraj contributes nothing
    assert [r[hdr.index("Year")] for r in rows] == [2022, 2023]
    assert rows[1][hdr.index("Tier")] == "A"
    assert rows[0][hdr.index("Relevance")] == 10
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /d/Dev/exaris_services && .venv/bin/python -m pytest b_kol_identification/tests/test_05_report.py::test_score_year_rows_one_per_trajectory_year -q`
Expected: FAIL (`AttributeError: ... 'build_score_year_rows'`)

- [ ] **Step 3: Add helper + headers**

Add near the other Excel helpers (below `build_wiki_verdict_rows`):

```python
SCORE_YEAR_HEADERS = ["Rank", "Name", "Year", "Composite score", "Relevance",
                      "Reach", "Ratio", "Tenure", "Tier"]


def build_score_year_rows(hcps):
    """One row per (HCP, trajectory year) from score_trajectory. HCPs with no trajectory
    contribute no rows. Mirrors the report's score-development chart data."""
    rows = []
    for rank, h in enumerate(hcps, 1):
        for p in (h.get("score_trajectory") or []):
            rows.append([rank, h.get("name", ""), p.get("year"),
                         round(float(p.get("score", 0)), 4), p.get("relevance"),
                         p.get("reach"), round(float(p.get("ratio", 0)), 4),
                         p.get("tenure"), p.get("tier")])
    return rows
```

- [ ] **Step 4: Append the sheet in `write_excel`**

Immediately before `wb.save(path)` (after the "LLM Wiki Verdicts" block), insert:

```python
    # Sheet 3 — Score by Year: composite reconstruction per year (score-dev chart data).
    ws3 = wb.create_sheet("Score by Year")
    ws3.append(SCORE_YEAR_HEADERS)
    for r in build_score_year_rows(data["hcps"]):
        ws3.append(r)
    ws3.freeze_panes = "A2"
    _autosize(ws3, SCORE_YEAR_HEADERS)
```

- [ ] **Step 5: Run tests**

Run: `cd /d/Dev/exaris_services && .venv/bin/python -m pytest b_kol_identification/tests/test_05_report.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add b_kol_identification/05_generate_report.py b_kol_identification/tests/test_05_report.py
git commit -m "feat(kol): Excel Score by Year sheet (per-year composite reconstruction)"
```

---

### Task 7: Full-suite + real-data verification

**Files:**
- No source changes (verification only). May update this plan's checkboxes.

- [ ] **Step 1: Run the full test suite**

Run: `cd /d/Dev/exaris_services && .venv/bin/python -m pytest b_kol_identification/tests -q`
Expected: PASS — 127 prior tests + the new ones (≈135), 0 failures.

- [ ] **Step 2: Generate the report against real data (no DB needed)**

Run: `cd /d/Dev/exaris_services/b_kol_identification && ../.venv/bin/python 05_generate_report.py --force`
Expected: logs "Wrote …/kol_report_<ts>.html" and "… .xlsx".

- [ ] **Step 3: Verify the HTML changes**

Run these greps against the newest `results/kol_report_*.html`:

```bash
cd /d/Dev/exaris_services/b_kol_identification
NEW=$(ls -t results/kol_report_*.html | head -1); echo "$NEW"
grep -c "Score development" "$NEW"        # >=1 (rising-stars section)
grep -c 'pill stage' "$NEW"               # profile sticker gone from profiles (may still appear in KOL ranking table — that's fine)
```

Then open `$NEW` in a browser and confirm: Rising Stars tab shows a "Score development" section of line charts + a score drill-down per row; KOL Profile cards have no "how it was scored" dropdown and no "Ny on-topic" sticker; in a profile card the publication bar chart and the score line chart are the same width with the bar chart sitting on visible x/y axes.

- [ ] **Step 4: Verify the Excel sheets**

Run:

```bash
cd /d/Dev/exaris_services/b_kol_identification
NEW=$(ls -t results/kol_report_*.xlsx | head -1)
../.venv/bin/python -c "
import openpyxl, sys
wb = openpyxl.load_workbook(sys.argv[1])
print('sheets:', wb.sheetnames)
w = wb['LLM Wiki Verdicts']; print('wiki header:', [c.value for c in w[1]]); print('wiki rows:', w.max_row-1)
s = wb['Score by Year']; print('score header:', [c.value for c in s[1]]); print('score rows:', s.max_row-1)
" "$NEW"
```

Expected: three sheets (`KOLs`, `LLM Wiki Verdicts`, `Score by Year`); wiki sheet has many rows with `counted`/`rejected` verdicts; score sheet has one row per HCP-year.

- [ ] **Step 5: Note the sandbox re-run for full coverage**

Confirm the wrap-up message tells the user: to get rising-star charts + the per-year sheet covering *all* 60 KOLs+rising-stars (not just the 25 in the current file), re-run on the Snowflake sandbox:

```bash
python 04_assemble_kols.py --force
python 05_generate_report.py --force
```

- [ ] **Step 6: Commit any plan checkbox updates (optional)**

```bash
git add docs/superpowers/plans/2026-07-16-kol-report-excel-adjustments.md
git commit -m "docs(kol): mark report/excel adjustments plan complete"
```

---

## Self-Review

**Spec coverage:** C1→Task 2; C2→Task 2; C3→Task 3; C4→Task 3; C5→Task 5; C6→Task 6;
C7→Task 1; C8→Task 4. Data-flow wiring (write_excel signature, main paths, graceful
missing-file) → Task 5. Verification → Task 7. All spec sections mapped.

**Placeholder scan:** No TBD/TODO; every code step shows full code; commands have expected
output. Clear.

**Type consistency:** `build_wiki_verdict_rows(hcps, sources_data, wiki_data)`,
`build_score_year_rows(hcps)`, `_load_json_safe(path)`, `_autosize(ws, headers, max_w)`,
`trajectory_targets(hcps)`, `render_rising_stars(hcps, all_years, weights, t_a, t_b,
rising_max)`, `PROFILE_CHART_W`, `WIKI_VERDICT_HEADERS`, `SCORE_YEAR_HEADERS` — names used
consistently across tasks and tests.
