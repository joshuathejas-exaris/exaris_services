# Service 1.2 — Competitive HCP Communication Monitoring

## What this service does

Identifies which HCPs genuinely speak about competitor drugs (wirkstoffe/brands),
extracts what they actually said — grounded verbatim in the source — and produces a
traceable HTML + Excel report. Each statement is attributed to a doctor only when
the source proves that doctor expressed that view.

## Why the grounding matters (do not regress)

Two failure modes this pipeline exists to kill:
1. **False attribution** — a doctor named on a page while the drug is mentioned
   elsewhere, with the doctor saying nothing about it. (Seen in the old
   `validated_corpus.json`: a chunk attributed to *Holznagel* actually quoted
   *Dr. Budić-Spasić*.)
2. **Co-occurrence ≠ opinion** — a keyword/content-frame match is not proof of a view.

The rework grounds every claim twice: (a) the verbatim quote must be literally
present in the source text (deterministic check), and (b) an independent LLM verify
pass confirms the named doctor expresses that view about the drug.

---

## Grounding model

```
Layer 1 — Revised LLM_VALIDATION gate (Track A only, SQL)
  NEAR_BY = 1 AND IS_OLD = 0 AND IS_DOCTOR = 1
  AND (brand OR generic present in COL_KEYWORDS_ORIG / COL_KEYWORDS_EN).
  IN_RELATION is intentionally NOT gated — it is non-indicative.
  Yields relevant WEBSITE_IDs + the mapped-HCP roster (S_CUSTOMER_ID).

Layer 2 — Scoped vector search + full-content assembly
  VECTOR_COSINE_SIMILARITY restricted to the Layer-1 WEBSITE_IDs (Track A) or the
  whole corpus (Track B); best `top_chunks_per_wirkstoff` chunks per wirkstoff.
  The entire document CONTENT of the matched websites becomes the wiki raw source.

Layer 3 — LLM-wiki (raw → wiki → schema), per run
  Ingest grounded claims → deterministic quote-grounding → adversarial LLM verify →
  resolve speaker to a mapped S_CUSTOMER_ID or flag "not mapped". Writes a
  wiki/<ts>/<competitor>/{raw,wiki,schema} tree + a knowledge_graph.json.
```

**Two tracks**, selected by Stage 01's `source` tag:
- **Track A — CF-derived (`source="cf"`):** LLM_VALIDATION is meaningful; mapped HCPs
  available.
- **Track B — LLM-knowledge (`source="llm"`):** no LLM_VALIDATION; vector search only;
  every doctor found is unmapped by construction.

**Mapped vs unmapped:** a *mapped* HCP resolves to an `S_CUSTOMER_ID`; an *unmapped*
doctor is genuinely quoted in a source but has no record match. Both are reported,
each flagged.

---

## Pipeline

```
01_identify_competitors.py   →  data/competitors.json
02_retrieve_sources.py       →  data/raw_sources.json
03_wiki_build.py             →  data/knowledge_graph.json  (+ wiki/<ts>/ tree)
04_synthesize.py             →  data/synthesis.json
05_generate_report.py        →  results/report_<ts>.html   guide_<ts>.html
                                 technical_<ts>.html        report_<ts>.xlsx
```

Every stage is resume-safe: skip if output exists unless `--force`.

Run order:
```
python 01_identify_competitors.py --client-drug "Ozempic" --from-snowflake
python 02_retrieve_sources.py
python 03_wiki_build.py
python 04_synthesize.py
python 05_generate_report.py
```

Tests (no AWS/Snowflake/ONNX needed — external boundaries are mocked):
```
.venv/bin/python -m pytest a_comp_hcp_communication/tests -q
```

---

## Files

| File | Purpose |
|------|---------|
| `01_identify_competitors.py` | Map CF terms + client drug → competitors (brand/generic, `source: cf\|llm`). Rejects a drug name leaking in as the indication. |
| `02_retrieve_sources.py` | Revised gate (Track A) / global vector search (Track B) → full-content source docs + mapped-HCP roster. |
| `03_wiki_build.py` | Ingest → quote-grounding → verify → map; writes wiki tree + `knowledge_graph.json`. |
| `04_synthesize.py` | Aggregate claims: mapped/unmapped sentiment split, market view, overall summary. |
| `05_generate_report.py` | 3 HTML reports (10–15 examples/section + "N more") + full Excel. |
| `pipeline_common.py` | Shared helpers: JSON parse, name-match, Bedrock JSON call. |
| `vector_creator.py` | Local ONNX embedding — **do not modify**. |
| `reranker.py` | Local reranker — **do not modify** (unused after the rework; kept for parity). |
| `config.ini` | All tunable params. |
| `tests/` | Pytest unit tests (mock Snowflake/Bedrock/ONNX). |
| `data/` | JSON checkpoints (gitignored). |
| `wiki/<ts>/` | Per-run raw/wiki/schema tree (gitignored). |
| `results/` | HTML + Excel outputs (gitignored). |

Assets (read-only, mounted at `/assets/` in sbx): `gte_multilang_model_quantized.onnx`,
`tokenizer.json`, `mmarco-reranker/`.

---

## Snowflake tables

| Table | Schema | Purpose | Key columns |
|-------|--------|---------|-------------|
| `LLM_VALIDATION` | schema_final | Layer-1 gate + full CONTENT + keywords | `NEAR_BY, IS_OLD, IS_DOCTOR, COL_KEYWORDS_ORIG, COL_KEYWORDS_EN, CONTENT, WEBSITE_ID, S_CUSTOMER_ID` |
| `WEBSITES_VERTICAL_CONTENT_FRAME_SINGLE_TBL` | schema_final | Vertical CF mapping | `WEBSITE_ID, S_CUSTOMER_ID, URL` |
| `WEBSITES_VERTICAL_EMBEDDINGS_512` | schema_final | Vertical chunks + embeddings | `CHUNK, EMBEDDINGS, WEBSITE_ID` |
| `WEBSITES_CONTENT_FRAME_SINGLE` | schema_final | Public website CF mapping | `WEBSITE_ID, DOMAIN_VALUE` |
| `WEBSITES_EMBEDDINGS_512` | schema_final | Public chunks + embeddings | `CHUNK, EMBEDDINGS, WEBSITE_ID` |
| `PUBMED_EMBEDDINGS_512` | schema_final | PubMed chunks (Track B) | `CHUNK, EMBEDDINGS, WEBSITE_ID` |
| `CUSTOMER_SOURCE` | schema_tmp | HCP master record | `S_FIRSTNAME, S_LASTNAME, S_CITY` |
| `CONTENT_FRAME_SPEC` | schema_tmp | CF terms for Stage 01 | `DE_TERM_1, EN_TERM_1` |

> **Verify on first live run (Q4):** `02_retrieve_sources.assemble_full_text` prefers
> `LLM_VALIDATION.CONTENT`; if CONTENT turns out to be a fragment rather than the whole
> document, switch `[wiki] content_source = chunk_concat` and add a chunk-reconstruction
> query (concatenate `*_EMBEDDINGS_512.CHUNK` per WEBSITE_ID).

---

## Snowflake connection

Same pattern as `01_identify_competitors.py` / `02_retrieve_sources.py`
(`connect_snowflake`): boto3 + AWS Secrets Manager private-key auth via `shared/`
(`ParameterManager`, `SecretReader`). Each stage adds the repo root to `sys.path`:
```python
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
```

---

## Design & plan

- Spec: `docs/superpowers/specs/2026-07-03-llm-wiki-grounded-hcp-monitoring-design.md`
- Plan: `docs/superpowers/plans/2026-07-03-llm-wiki-grounded-hcp-monitoring.md`

Confirmed decisions: grounded statements are the headline (sentiment is a per-claim
attribute); unmapped doctors come from the same source docs; the wiki is rebuilt per
run; full text comes from `LLM_VALIDATION.CONTENT` (fallback chunk-concat).
