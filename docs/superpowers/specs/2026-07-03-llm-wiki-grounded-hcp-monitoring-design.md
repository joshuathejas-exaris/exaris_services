# Design — LLM-Wiki Grounded HCP Communication Monitoring (Service 1.2 rework)

**Status:** APPROVED (2026-07-03). All four foundational decisions confirmed by the
user as defaulted (see *Open Assumptions*). Proceeding to implementation plan + build.

**Date:** 2026-07-03
**Author:** Claude (brainstorming session with Joshua)

---

## 1. Motivation

The current v2 pipeline still lets false attributions through. Concrete evidence
from the existing `data/validated_corpus.json`: its first entry attributes a chunk
to *Michael Holznagel*, but the chunk actually quotes *"Dr. Vesna Budić-Spasić,
doktorica opšte medicine"* — the mapped HCP says nothing in that text. This is the
exact failure mode we must eliminate: **the HCP named on a page and the drug named
elsewhere on the page does not mean the HCP said anything about the drug.**

Two root causes remain:

1. **The LLM_VALIDATION gate is the wrong gate.** `IN_RELATION` is a weak, noisy
   signal (only `>29` even claims the HCP "speaks directly about a term"), and the
   current code gates on `IS_DOCTOR = 1 AND IN_RELATION >= threshold`. This neither
   guarantees speakership nor topical relevance.
2. **Chunks are not evidence of a statement.** A reranked chunk that mentions the
   drug is not proof that a *specific* HCP expressed a view about it.

The rework replaces the chunk-attribution model with a **Karpathy-style LLM-wiki**
(raw sources → wiki → schema) whose ingest + verify loop only emits a claim when a
named doctor genuinely says something about a wirkstoff/brand *in the source text*,
carrying the verbatim span that proves it.

---

## 2. Core concepts

- **Wirkstoff / brand:** each competitor from Stage 01 has a `brand_name` and a
  `generic_name` (the wirkstoff). "The competitor" = either name.
- **Two tracks**, driven by Stage 01's existing `source` tag:
  - **Track A — CF-derived (`source = "cf"`):** the wirkstoff came from
    `CONTENT_FRAME_SPEC`, so `LLM_VALIDATION` rows exist and are useful. Mapped HCPs
    are available.
  - **Track B — LLM-knowledge (`source = "llm"`):** the wirkstoff came from model
    knowledge only. `LLM_VALIDATION` gives nothing. Vector search + wiki only; every
    doctor found is **unmapped** by construction.
- **Mapped vs unmapped HCP:**
  - *Mapped* = the doctor extracted from a source resolves to an `S_CUSTOMER_ID` in
    `LLM_VALIDATION` / `CUSTOMER_SOURCE` (name match against `S_FIRSTNAME/S_LASTNAME`).
  - *Unmapped* = a doctor genuinely quoted in a source who does not resolve to a
    customer record. Reported with a "not mapped" flag.
- **Grounded claim** (the atomic output unit): one doctor, one wirkstoff, one
  verbatim quote, the derived statement + sentiment + confidence, a mapped flag
  (+ `S_CUSTOMER_ID` when mapped), and a citation (source doc + verbatim span).

---

## 3. Open Assumptions (CONFIRMED 2026-07-03)

| # | Decision | Confirmed choice |
|---|----------|------------------|
| Q1 | Headline output | **Grounded statements primary**; sentiment is an attribute of each claim |
| Q2 | Source of unmapped doctors | **Only from the same full-content docs** already pulled for mapped HCPs |
| Q3 | Wiki lifetime | **Per-run rebuild** (ephemeral, resume-safe), using raw/wiki/schema layout |
| Q4 | "Entire content" assembly | **`LLM_VALIDATION.CONTENT`**, with chunk-concat fallback; verify on real rows during impl |

---

## 4. Revised pipeline

```
01_identify_competitors.py   →  data/competitors.json         (unchanged shape + indication-bug fix)
02_retrieve_sources.py       →  data/raw_sources.json         (NEW: filter + vector search + full-content assembly)
03_wiki_build.py             →  data/knowledge_graph.json     (NEW: raw→wiki→schema + verify; writes wiki/ md tree)
                             →  wiki/<ts>/<competitor>/{raw,wiki,schema}/…
04_synthesize.py             →  data/synthesis.json           (aggregate grounded claims: distributions, narratives)
05_generate_report.py        →  results/report_<ts>.html + guide + technical + report_<ts>.xlsx
```

Every stage stays resume-safe (skip if output exists unless `--force`), matching the
existing convention.

---

### Stage 01 — `01_identify_competitors.py` (light touch)

- **Fix the indication bug.** Current `competitors.json` has `"indication":
  "Wegovy"` (a drug). The indication must never be a brand. Prefer the CF row-1
  indication; if the LLM returns a brand as the indication, reject and fall back.
- Keep the `source: cf|llm` tag — it now formally selects Track A vs Track B.
- Otherwise unchanged.

---

### Stage 02 — `02_retrieve_sources.py` (replaces `02_validated_corpus.py`)

Purpose: produce, per competitor, the set of **full-content source documents** to
feed the wiki, plus the mapped-HCP roster for those documents.

**Track A (cf) — Layer 1 filter (revised gate):**

```sql
SELECT WEBSITE_ID, S_CUSTOMER_ID, S_FIRSTNAME, S_LASTNAME, S_CITY,
       COL_KEYWORDS_ORIG, COL_KEYWORDS_EN, CONTENT
FROM {schema_final}.LLM_VALIDATION
WHERE NEAR_BY = 1
  AND IS_OLD = 0
  AND IS_DOCTOR = 1
  AND (
       COL_KEYWORDS_ORIG ILIKE '%{brand}%' OR COL_KEYWORDS_EN ILIKE '%{brand}%'
    OR COL_KEYWORDS_ORIG ILIKE '%{generic}%' OR COL_KEYWORDS_EN ILIKE '%{generic}%'
  )
```

- `IN_RELATION` is **dropped** from the gate (per user: not indicative).
- Keyword match is case-insensitive substring on either keyword column against
  brand or generic. (Refinement: token-boundary match to avoid `SELECT` matching
  inside `(SELECT)` etc. — decide during impl.)
- This yields the relevant `WEBSITE_ID`s and their **mapped HCPs** (`S_CUSTOMER_ID`
  + name + city).

**Track A — Layer 2 vector search (scoped to Layer-1 website IDs):**

- Embed the wirkstoff query strings (`brand`, `generic`, `brand + indication`) with
  `VectorCreator`.
- `VECTOR_COSINE_SIMILARITY` over the embedding tables, **restricted to the Layer-1
  `WEBSITE_ID` set**, keep the best `top_chunks_per_wirkstoff` (=100) chunks per
  wirkstoff.
- The chunks identify the *relevant documents*; we then assemble each document's
  **entire content** (Q4: `LLM_VALIDATION.CONTENT`, fallback chunk-concat) as the raw
  source. Chunks are retained only as provenance / retrieval evidence.

**Track B (llm):**

- No LLM_VALIDATION. Vector search across the whole corpus for the wirkstoff →
  best 100 chunks → assemble full content of those websites. No mapped HCPs.

**Output — `data/raw_sources.json`:**

```json
[
  {
    "competitor": "Saxenda",
    "track": "A",
    "mapped_hcps": [
      {"s_customer_id": "WDEM06255914", "name": "Michael Holznagel", "city": "…"}
    ],
    "sources": [
      {
        "website_id": "…",
        "source_type": "VERTICAL|WEBSITES|PUBMED",
        "url": "https://…",
        "full_text": "…entire document…",
        "matched_chunks": [{"text": "…", "similarity": 0.84}]
      }
    ]
  }
]
```

---

### Stage 03 — `03_wiki_build.py` (replaces `03_wiki_extract.py`) — the heart

Implements the Karpathy raw → wiki → schema pattern per competitor, in a run-scoped
directory `wiki/<timestamp>/<competitor>/`.

**3a. `raw/` (immutable sources).** One markdown file per source document, with YAML
frontmatter: `website_id`, `url`, `source_type`, `mapped_hcps`. Body = the full text.

**3b. Ingest → grounded claims.** For each raw source, one LLM call extracts, for
**every doctor who genuinely expresses something about the wirkstoff/brand in that
document**:

- `speaker_name` (exactly as written in the source),
- `verbatim_quote` (a span copied from the text — proves both speakership and topic),
- `statement` (one-line paraphrase of what they say about the wirkstoff),
- `sentiment` ∈ {positive, neutral, negative, ambivalent} + `confidence` ∈ {high, medium, low},
- `citation` = `{website_id, url, quote}`.

Grounding rules baked into the prompt:
- Emit a claim **only** if the same doctor is the one making a statement *about the
  wirkstoff* — reject "doctor named at top, drug named elsewhere."
- The quote must be verbatim from the provided text; no paraphrase/translation/invention.
- If the doctor is only mentioned (no view about the wirkstoff), emit nothing.

**3c. Map speakers → HCPs.** Resolve each `speaker_name` against the Stage-02
`mapped_hcps` roster (normalised name match on `S_FIRSTNAME`/`S_LASTNAME`). Match →
`mapped = true` + `s_customer_id`. No match → `mapped = false` (unmapped/general
doctor). Track B is always unmapped.

**3d. Verify / lint pass (adversarial — this is the "bulletproof" guarantee).** A
second LLM call per claim (or small batch) re-checks the claim against its cited
source span: is the quote actually present, and does it genuinely attribute a view
about the wirkstoff to that speaker? Claims that fail are dropped; borderline ones are
downgraded in confidence. Parallelised.

**3e. `wiki/` (LLM-owned synthesis).** Entity pages per wirkstoff and per HCP with
`[[wikilinks]]`, `index.md`, and an append-only `log.md`, each claim citing its raw
source — following the pattern from the referenced articles.

**3f. `schema/knowledge_graph.json` (machine-readable output consumed downstream):**

```json
{
  "competitor": "Saxenda",
  "track": "A",
  "nodes": {
    "hcps": [{"id": "WDEM06255914", "name": "…", "mapped": true}],
    "wirkstoffe": [{"name": "Saxenda", "generic": "Liraglutid"}]
  },
  "claims": [
    {
      "speaker_name": "Michael Holznagel",
      "s_customer_id": "WDEM06255914",
      "mapped": true,
      "wirkstoff": "Saxenda",
      "statement": "…",
      "verbatim_quote": "…",
      "sentiment": "positive",
      "confidence": "high",
      "citation": {"website_id": "…", "url": "https://…"},
      "verified": true
    }
  ]
}
```

Stage 03's merged output `data/knowledge_graph.json` is the union across competitors.

---

### Stage 04 — `04_synthesize.py` (aggregation, lighter)

Reads `data/knowledge_graph.json` (already-grounded, already-sentimented claims). No
per-pair re-judgement needed. Produces:

- per-wirkstoff sentiment distribution **split by mapped vs unmapped**,
- per-wirkstoff market-view narrative (LLM, grounded strictly in the claims),
- overall summary,
- optional per-HCP roll-up when one HCP has several claims about one wirkstoff.

Output `data/synthesis.json` (shape mirrors current `sentiment_results.json` plus a
`mapped`/`unmapped` breakdown).

---

### Stage 05 — `05_generate_report.py` (extended)

- **Report A (Competitor Intelligence):** exec summary; per-wirkstoff section showing
  **10–15 example grounded statements** (mapped/unmapped badge, `S_CUSTOMER_ID` when
  mapped, sentiment + confidence, verbatim quote, source link) with a *"+ N more —
  see Excel"* note; per-HCP drill-down (10–15 examples, same note). A mapped-vs-unmapped
  legend.
- **Report B (Plain-language guide):** updated to explain mapped vs unmapped doctors
  and how grounding/verification works, in lay terms.
- **Report C (Technical):** updated tables + the new filter gate + wiki architecture.
- **Excel (FULL results):** one row per grounded claim — speaker, mapped flag,
  `S_CUSTOMER_ID`, wirkstoff, statement, verbatim quote, sentiment, confidence, source
  URL, verified flag. Plus a per-wirkstoff summary sheet with mapped/unmapped split.

---

## 5. Config additions (`config.ini`)

```
[llm_validation]           ; the revised Layer-1 gate
near_by = 1
is_old = 0
is_doctor = 1
; in_relation gate removed

[retrieval]
top_chunks_per_wirkstoff = 100
min_similarity = 0.65

[wiki]
ingest_model_id = …        ; extraction of grounded claims
verify_model_id = …        ; adversarial verification pass
wiki_max_workers = 5
verify_max_workers = 5
unmapped_source_mode = same_docs   ; same_docs | broad_search | both  (Q2)
wiki_lifetime = per_run            ; per_run | persistent            (Q3)
content_source = llm_validation    ; llm_validation | chunk_concat   (Q4)
```

---

## 6. Testing approach

- **Unit:** keyword-match logic (brand/generic vs COL_KEYWORDS incl. the `(SELECT)`
  edge case), name-normalisation/mapping, claim schema validation, "N more" counting.
- **Grounding regression:** the Holznagel/Budić-Spasić case must produce **zero**
  claims attributed to Holznagel — a fixture-based test proving the false attribution
  is now rejected.
- **Verify pass:** a fixture where a quote is subtly altered must be caught and dropped.
- **End-to-end smoke** on a small competitor with `--force` through all five stages.

---

## 7. Out of scope

- No changes to `shared/`, `vector_creator.py`, or `reranker.py`.
- No persistent cross-run wiki unless Q3 is flipped to *persistent*.
- No new source tables beyond those already used.
```