# KOL Identification — Report & Data Enhancements

**Date:** 2026-07-10
**Service:** `b_kol_identification` (Service 2.1 — KOL Identification & Mapping)
**Status:** Approved design, ready for planning

## Overview

Four independent enhancements to the KOL pipeline, plus one documentation update. All
are additive/refactoring changes that preserve the pipeline's core principle
(`kol_score = verified_web_count + verified_pubmed_count`, relevance decided per source
by the LLM). None change how scores are verified.

1. **Web-source URLs** — attach the real URL to each web source so the report and Excel
   link back to the page (currently blank; `LLM_VALIDATION` has no `URL` column).
2. **Config refactor** — remove the hand-maintained `[tables]` section; derive every
   fully-qualified table name from `database` + two schema knobs.
3. **20-year publication-history bars** — the per-year publication bars currently span
   only 2021–2023; show ~20 years of history, on a data-anchored window.
4. **Sidebar navigation** — replace the single long scroll of the HTML report with a
   sticky grouped left-nav + switchable sections, mirroring Service A.
5. **Explainer doc update** — document the 20-year-vs-5-year publication-year behaviour
   in `b_kol_identification/pipeline_explainer.html`.

After changes (1)+(3) a full `--force` re-run of stages 01→05 is required (see §7).

---

## 1. Web-source URLs (Stage 02)

**Problem.** `build_web_content_query` (`02_retrieve_sources.py`) selects only
`WEBSITE_ID, CONTENT` from `LLM_VALIDATION`, and `LLM_VALIDATION` has no `URL` column.
`assemble_web_sources` reads a `URL` field that is therefore always empty, so every web
source's `url` is `""` throughout the pipeline; the report shows blank web links and the
Excel "Source URL" column is empty for web claims. PubMed URLs are unaffected (built from
`PMID`).

**Change.** `WEBSITES_VERTICAL_ALL_SOURCE` (in `schema_final`) carries `WEBSITE_ID` + `URL`.
`build_web_content_query` gains a `LEFT JOIN` on `WEBSITE_ID`:

```sql
SELECT lv.WEBSITE_ID, lv.CONTENT, src.URL
FROM {llm_validation} lv
LEFT JOIN {websites_vertical_all_source} src ON src.WEBSITE_ID = lv.WEBSITE_ID
WHERE lv.WEBSITE_ID IN (...) AND lv.S_CUSTOMER_ID = '...'
```

- `LEFT JOIN` so a missing URL row never drops the source (URL just stays `""`).
- Join on `WEBSITE_ID` alone; `URL` is unique per `WEBSITE_ID`.
- `assemble_web_sources` already reads `_g(r, "URL")` — no change needed there; URLs then
  flow automatically 02 → 03 (onto each claim's `url`) → 04 (`top_quotes[].url`) → 05
  (the "source" link in profiles + the Excel "Source URL" column).

**Interface impact.** `build_web_content_query` gains a `websites_vertical_all_source`
parameter. No JSON schema change (the `url` field already exists on web sources).

---

## 2. Config refactor — derive table FQNs from schema knobs

**Problem.** `config.ini` has a `[tables]` section listing eight fully-qualified names by
hand. This is error-prone (it already disagrees with the unused `[snowflake] schema_v1`
key) and forces editing eight lines per targeting when only the schema names change.

**Change.** Mirror Service A's `config.ini`.

New `[snowflake]` section (no `[tables]` section):

```ini
[snowflake]
aws_profile  = AdministratorAccess-311524101909
warehouse    = COMPUTE_WH
database     = CUST_TC
schema_final = ADIPOS_AMBU_FINAL
schema_tmp   = ADIPOS_AMBU_TMP
```

- `schema_v1` is renamed to `schema_tmp` with value `ADIPOS_AMBU_TMP` (the value the
  working `[tables]` paths actually used). `[terms]`, `[funnel]`, `[bedrock]`,
  `[scoring]`, `[report]` sections are unchanged.

New helper in `pipeline_common.py`:

```python
def resolve_tables(sf):
    """Build fully-qualified table names from the [snowflake] config section.
    Only database + schema_final + schema_tmp change per targeting; CORE.PUBMED.*
    tables are constants."""
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

Each stage's `main()` swaps `tb = cfg["tables"]` for `tb = resolve_tables(cfg["snowflake"])`.
The returned dict keeps the **same keys** the stages already use (`tb["llm_validation"]`,
etc.), so query-builder call sites are unchanged apart from the new
`websites_vertical_all_source` key used by change (1). Stages touched: 01, 02, 04
(03 and 05 do not read `[tables]`).

**Schema-of-record fix.** The table locations documented in `b_kol_identification/CLAUDE.md`
(§ Snowflake tables) currently show stale `CUST_NOVO`/`ADIPOS_AMBU_V1` values; update that
table to the resolved `CUST_TC` / `schema_final` / `schema_tmp` reality, including the new
`WEBSITES_VERTICAL_ALL_SOURCE` row.

---

## 3. 20-year publication-history bars (Stages 01 + 05)

**Problem.** The per-year bars in Rising Stars and Individual KOL Profiles span only
2021–2023 for two reasons: (a) Stage 01's PubMed candidate query filters
`YEAR_VAL >= current_year - 5`, anchored on `datetime.now().year` (2026), so it collects
only 2021+; (b) Stage 05 builds the chart axis from the union of years actually present in
`pub_by_year`. Both need to change, and the anchor must come from the data, not the clock.

### 3a. Data-anchored year windows (Stage 01)

- New query `Q0`: `SELECT MAX(YEAR_VAL) AS ANCHOR FROM {pubmed_cf_flag}` → `anchor_year`.
  (For this targeting ≈ 2023.) If the table is empty, fall back to `datetime.now().year`.
- The **existing candidate/scoring window** is re-anchored: cutoff becomes
  `anchor_year - pubmed_window_years` (5) instead of `now().year - 5`. Consequence: the
  5-year window shifts from 2021–2025 to ≈2018–2023, so the candidate set, the top-75
  shortlist, and therefore the final KOL set **will change** on re-run. This is accepted
  and intended (it is the correct window for a 2023 targeting).

### 3b. Separate 20-year history query (Stage 01, display only)

- New `[funnel]` param `pub_history_years = 20`.
- A **separate** aggregation counts obesity-relevant (CF-flagged, `cf_any`) PubMed articles
  per `(S_CUSTOMER_ID, YEAR_VAL)` over `YEAR_VAL >= anchor_year - pub_history_years`. This
  repopulates each HCP's `pub_by_year` (year → count).
- `pub_by_year` is **display-only**: scoring uses the 5-year `pubmed_articles` list;
  rising-star flags use `verified_pubmed_years` (Stage 04). Widening `pub_by_year` to 20
  years changes neither. This must be preserved — verify no scoring/rising-star path reads
  `pub_by_year`.
- The bars therefore show a 20-year **content-frame-flag** publication history (keyword-level,
  unverified). Only the most-recent-5-years subset is what actually reaches the LLM and the
  score; the older bars are historical context.

### 3c. Fixed axis + thin bars (Stage 05)

- `anchor_year` and `pub_history_years` propagate top-level through
  `shortlist.json → sources.json → wiki.json → kol_final.json` (each stage copies them, as
  it already does for `indication`/`client_drug`/`pca_terms`).
- Stage 05 builds a **fixed axis** of exactly `pub_history_years` slots:
  `all_years = list(range(anchor_year - pub_history_years + 1, anchor_year + 1))`
  (falls back to the union-of-present-years if `anchor_year` is absent, for older JSON).
  Zero-count years render as empty slots so spacing is even.
- `render_sparkline` is widened (e.g. `width≈190`) so 20 thin bars read cleanly; the
  existing `bw = width/n - 1` auto-thins the bars. Both call sites (Rising Stars,
  Individual KOL Profiles) use the same axis. The `pubs/yr (year_range)` label shows the
  full `anchor-19 … anchor` span.

---

## 4. Sidebar navigation (Stage 05)

**Problem.** The HTML report is one long scroll; reaching the Top-25 Individual KOL
Profiles means scrolling past every other section.

**Change.** Port Service A's sidebar pattern (`a_comp_hcp_communication/05_generate_report.py`:
`_render_sidebar`, `TAB_SCRIPT`, and the `.layout`/`.sidebar`/`.nav-item`/`.panel` CSS):

- A sticky grouped left-nav beside a content pane; JS `showTab()` shows one `.panel` at a
  time and highlights the active nav item.
- Degrades to a full-scroll page when JS is disabled (no panel hidden by default CSS).
- Mobile `@media(max-width:720px)` collapses the sidebar to full width above the content.
- The report header (title, client drug, generated timestamp) stays pinned above the layout.
- Adapt the ported CSS to the KOL report's existing `PALETTE` (accent `#2f4a7c`, etc.) so it
  matches the current look; keep everything self-contained (no CDN/fonts/network), per the
  service convention.

**Section grouping (nav items → panels):**

- **OVERVIEW** — Executive Dashboard (stat cards) · KOL Ranking (Top 25 table)
- **ANALYSIS** — Rising Stars · Thematic Distribution · Regional Distribution ·
  Collaboration Network
- **PROFILES** — Individual KOL Profiles (Top 25)

Each existing `render_*` function becomes the body of a panel; `build_report_html` is
restructured to assemble `groups` and call the ported `_render_sidebar`. No change to the
render functions' internals beyond the axis change in §3c. The Excel export is unchanged.

---

## 5. Explainer doc update

Update `b_kol_identification/pipeline_explainer.html` (the copy in the service root, **not**
`results/`) to document the publication-year behaviour:

- Stage 01 section: state that the anchor year is `MAX(YEAR_VAL)` from
  `PUBMED_CONTENT_FRAME_SINGLE_TBL` (not the current calendar year), and that the 5-year
  candidate/scoring window is measured back from that anchor.
- Add a short note (Stage 01 and/or the "Under the hood" section) explaining the two
  windows: the **20-year** CF-flag count that feeds the publication-history bars is
  **display only** and never sees the LLM; only the **most-recent-5-years** articles go
  through LLM-wiki verification and contribute to the score/tier/rising-star flags. Make
  explicit that bar height = CF-flag count (keyword-level, unverified activity), not score.

Keep the existing visual style of the explainer; this is a copy/markup edit, not a redesign.

---

## 6. Testing

- **`resolve_tables`** — unit test: given a `[snowflake]`-like dict, asserts each key maps
  to the expected FQN and that `pubmed_article`/`pubmed_author` are the `CORE.PUBMED.*`
  constants.
- **Stage 01** — tests for: `Q0` anchor query builder; the 5-year cutoff computed from
  `anchor_year`; the new 20-year history aggregation producing correct per-year counts;
  candidate scoring still uses only the 5-year `pubmed_articles`. Update any existing test
  that assumed `now().year` anchoring or read `[tables]`.
- **Stage 02** — test that `build_web_content_query` emits the `LEFT JOIN` and selects
  `URL`, and that `assemble_web_sources` surfaces a joined URL.
- **Stage 05** — test that the axis is a fixed `pub_history_years`-length range derived from
  `anchor_year` (incl. zero years), and that `_render_sidebar`/`showTab` output contains the
  expected nav groups and one active panel. Existing report tests updated for the new axis.
- All external boundaries (Snowflake/Bedrock) remain mocked, per the service convention.
  Run: `.venv/bin/python -m pytest b_kol_identification/tests -q`.

## 7. Rollout / re-run

Because §3a shifts the shortlist, a full re-run is required and picks up every change in one
pass:

```
python 01_fetch_and_shortlist.py --force
python 02_retrieve_sources.py    --force
python 03_wiki_build.py          --force   # Bedrock cost (verification) re-incurred
python 04_assemble_kols.py       --force
python 05_generate_report.py     --force
```

The config refactor (§2), sidebar (§4), and explainer (§5) do not themselves require a
re-run, but the re-run above exercises them end to end.

## 8. Out of scope

- No change to the verification logic (ingest → ground → verify) or the score formula.
- No change to rising-star semantics (still computed on `verified_pubmed_years`).
- No change to PubMed URL construction, the collaboration network, or the Excel schema
  (beyond web URLs now being populated).
