# Service 1.2 — Competitive HCP Communication Monitoring

## What this service does

Identifies which HCPs are genuinely speaking about competitor drugs, determines their
sentiment per competitor, and produces a traceable HTML + Excel report.

## Why this is a rebuild (do not revert to v1 patterns)

The v1 pipeline had two fatal flaws:
1. **HCP authorship assumption** — chunks were attributed to the HCP in vector metadata,
   but that HCP was often just *mentioned*, not the actual author/speaker.
2. **CF filter as intent proof** — a content frame match only proves the HCP appears
   in a document alongside competitor keywords, not that they expressed any view.

This rebuild fixes both via a three-layer confidence model.

---

## Three-Layer Confidence Model

```
Layer 1 — LLM_VALIDATION gate (SQL)
  Only chunks where IS_DOCTOR = 1 AND IN_RELATION >= threshold.
  These are the only HCP×document pairs where the HCP is confirmed
  as the speaker/author. Hard filter — no fallback, no exceptions.

Layer 2 — Snowflake vector search + reranking
  VECTOR_COSINE_SIMILARITY search directly in Snowflake embedding tables,
  restricted to the Layer 1 corpus via JOIN on LLM_VALIDATION.
  Then rerank locally with the mmarco reranker model.

Layer 3 — Ephemeral wiki extraction
  Structured fact extraction per HCP × competitor pair each run.
  Not persisted between runs.
```

---

## Pipeline

```
01_identify_competitors.py   →  data/competitors.json
02_validated_corpus.py       →  data/validated_corpus.json
03_wiki_extract.py           →  data/wiki_facts.json
04_sentiment_synthesis.py    →  data/sentiment_results.json
05_generate_report.py        →  results/report_<timestamp>.html
                             →  results/report_<timestamp>.xlsx
```

Every stage is resume-safe: skip if output exists unless `--force` passed.

---

## Files in this folder

| File | Purpose |
|------|---------|
| `01_identify_competitors.py` | Stage 01 — build by agent |
| `02_validated_corpus.py` | Stage 02 — build by agent |
| `03_wiki_extract.py` | Stage 03 — build by agent |
| `04_sentiment_synthesis.py` | Stage 04 — build by agent |
| `05_generate_report.py` | Stage 05 — build by agent |
| `vector_creator.py` | Embeds query text via local ONNX model — **do not modify** |
| `reranker.py` | Local reranker utility — **do not modify** |
| `config.ini` | All tunable params |
| `data/` | JSON checkpoints (gitignored) |
| `results/` | HTML + Excel outputs (gitignored) |

Assets (read-only, mounted at `/assets/` in sbx):
- `/assets/gte_multilang_model_quantized.onnx` — embedding model
- `/assets/tokenizer.json` — tokenizer
- `/assets/mmarco-reranker/` — reranker model

---

## Stage 01 — `01_identify_competitors.py`

**Purpose:** Identify competitor drugs for the client's drug.

**CLI:**
```
python 01_identify_competitors.py --client-drug "Ozempic"
python 01_identify_competitors.py --client-drug "Ozempic" --cf-data files/cf_data.csv
python 01_identify_competitors.py --client-drug "Ozempic" --from-snowflake
```

**Logic:**
1. Parse CF terms from CSV or Snowflake `{schema_tmp}.CONTENT_FRAME_SPEC`
   (columns `DE_TERM_1`, `EN_TERM_1`). Row 1 is always the indication.
2. Call Bedrock LLM (model from config, temperature=0.0) with CF terms + client drug.
3. LLM returns competitors: `{brand_name, generic_name, source: cf|llm}`.
4. If < 2 from CF, supplement from model knowledge (`source: llm`).
5. Retry 3× with 2s backoff on any failure.

**Bedrock call pattern:**
```python
bedrock.converse(
    modelId=model_id,
    messages=[{"role": "user", "content": [{"text": prompt}]}],
    inferenceConfig={"temperature": 0.0, "maxTokens": max_tokens}
)
```

**Output — `data/competitors.json`:**
```json
{
  "indication": "Obesity",
  "client_drug": "Ozempic",
  "competitors": [
    {"brand_name": "Wegovy",   "generic_name": "Semaglutide", "source": "cf"},
    {"brand_name": "Mounjaro", "generic_name": "Tirzepatide",  "source": "llm"}
  ]
}
```

---

## Stage 02 — `02_validated_corpus.py`

**Purpose:** Build a clean, high-trust corpus using the LLM_VALIDATION gate +
Snowflake vector search + local reranking.

**Snowflake connection** (private key auth via AWS Secrets Manager):
```python
import boto3, snowflake.connector
from cryptography.hazmat.primitives import serialization

session  = boto3.Session(profile_name=aws_profile, region_name="eu-central-1")
# 1. Get stack name from SSM: /exaris/main-stack-name
# 2. Get secret name from SSM: /{stack}/snowflake/secret-name
# 3. Get secret from Secrets Manager → {user, account, private_key}
private_key_bytes = ...  # load_pem_private_key → private_bytes(DER, PKCS8, NoEncryption)
conn = snowflake.connector.connect(
    user=secret["user"], account=secret["account"],
    warehouse=warehouse, database=database,
    private_key=private_key_bytes
)
```

**Step A — LLM_VALIDATION gate + Snowflake vector search (Layers 1 & 2 combined):**

For each competitor (use both brand_name and generic_name as search terms),
generate 3 query strings: `[brand_name, generic_name, f"{brand_name} {indication}"]`.

For each query string, embed it with `VectorCreator` (from `vector_creator.py`),
then run this UNION ALL across all three source tables — LLM_VALIDATION is joined
directly as the hard gate:

```sql
SELECT * FROM (
    -- Vertical websites
    SELECT e.CHUNK, lv.WEBSITE_ID, lv.S_CUSTOMER_ID,
           cs.S_FIRSTNAME, cs.S_LASTNAME, cs.S_CITY, cs.S_HCP_GROUP,
           VECTOR_COSINE_SIMILARITY(e.EMBEDDINGS, {vec_literal}) AS SIM,
           'VERTICAL' AS SOURCE_TYPE, cf.URL AS URL_VALUE
    FROM {schema_final}.LLM_VALIDATION lv
    JOIN {schema_final}.WEBSITES_VERTICAL_CONTENT_FRAME_SINGLE_TBL cf
        ON lv.WEBSITE_ID = cf.WEBSITE_ID AND lv.S_CUSTOMER_ID = cf.S_CUSTOMER_ID
    JOIN {schema_final}.WEBSITES_VERTICAL_EMBEDDINGS_512 e
        ON e.REQUEST_ID BETWEEN cf.START_REQUEST_ID_EMBEDDINGS AND cf.END_REQUEST_ID_EMBEDDINGS
    JOIN {schema_tmp}.CUSTOMER_SOURCE cs ON lv.S_CUSTOMER_ID = cs.S_CUSTOMER_ID
    WHERE lv.IS_DOCTOR = 1 AND lv.IN_RELATION >= {threshold}

    UNION ALL

    -- Public websites
    SELECT e.CHUNK, lv.WEBSITE_ID, lv.S_CUSTOMER_ID,
           cs.S_FIRSTNAME, cs.S_LASTNAME, cs.S_CITY, cs.S_HCP_GROUP,
           VECTOR_COSINE_SIMILARITY(e.EMBEDDINGS, {vec_literal}) AS SIM,
           'WEBSITES' AS SOURCE_TYPE, cf.DOMAIN_VALUE AS URL_VALUE
    FROM {schema_final}.LLM_VALIDATION lv
    JOIN {schema_final}.WEBSITES_CONTENT_FRAME_SINGLE cf
        ON lv.WEBSITE_ID = cf.WEBSITE_ID AND lv.S_CUSTOMER_ID = cf.S_CUSTOMER_ID
    JOIN {schema_final}.WEBSITES_EMBEDDINGS_512 e ON e.WEBSITE_ID = cf.WEBSITE_ID
    JOIN {schema_tmp}.CUSTOMER_SOURCE cs ON lv.S_CUSTOMER_ID = cs.S_CUSTOMER_ID
    WHERE lv.IS_DOCTOR = 1 AND lv.IN_RELATION >= {threshold}

    UNION ALL

    -- PubMed
    SELECT e.CHUNK, e.WEBSITE_ID, cf.S_CUSTOMER_ID,
           cs.S_FIRSTNAME, cs.S_LASTNAME, cs.S_CITY, cs.S_HCP_GROUP,
           VECTOR_COSINE_SIMILARITY(e.EMBEDDINGS, {vec_literal}) AS SIM,
           'PUBMED' AS SOURCE_TYPE, cf.URL AS URL_VALUE
    FROM {schema_final}.PUBMED_CONTENT_FRAME_SINGLE cf
    JOIN {schema_final}.PUBMED_EMBEDDINGS_512 e ON e.WEBSITE_ID = cf.PMID
    JOIN {schema_tmp}.CUSTOMER_SOURCE cs ON cf.S_CUSTOMER_ID = cs.S_CUSTOMER_ID
) WHERE SIM >= {min_similarity}
ORDER BY SIM DESC
LIMIT {top_k_per_query}
```

`vec_literal` format: `{vec.tolist()}::VECTOR(FLOAT, 768)`

Collect results from all 3 queries per competitor, deduplicate by
`(website_id, s_customer_id, chunk)` keeping the highest SIM, then
group by HCP and rank by max SIM descending.

**Step B — Local reranking:**
Pass top chunks through `reranker.py` (model at `/assets/mmarco-reranker/`).
Keep `top_k_after_rerank` per HCP × competitor. Take `top_hcps_for_llm` HCPs.

**Output — `data/validated_corpus.json`:**
```json
[
  {
    "hcp_id": "CUST_12345",
    "hcp_name": "Dr. M. Karthaus",
    "competitor": "Wegovy",
    "chunks": [
      {
        "text": "...",
        "rerank_score": 0.92,
        "similarity": 0.84,
        "source_type": "VERTICAL",
        "source_url": "https://..."
      }
    ]
  }
]
```

**Edge case:** 0 results for a competitor → log WARNING, write empty list, continue.

---

## Stage 03 — `03_wiki_extract.py`

**Purpose:** Ephemeral structured fact extraction — what did each HCP actually say?

**Logic:**
- For each `(hcp_id × competitor)` pair with non-empty chunks: one Bedrock LLM call.
- Extract structured facts: verbatim quote, context type (publication/conference/website), source URL.
- Use `extraction_model_id` from config (lighter model).
- Parallelise with `concurrent.futures.ThreadPoolExecutor`, max 5 concurrent calls.
- Skip pairs with 0 chunks — do not call LLM on empty input.

**Output — `data/wiki_facts.json`:**
```json
[
  {
    "hcp_id": "CUST_12345",
    "hcp_name": "Dr. M. Karthaus",
    "competitor": "Wegovy",
    "facts": [
      {
        "quote": "Wegovy hat in unserer Praxis...",
        "context": "conference_presentation",
        "source_url": "https://..."
      }
    ]
  }
]
```

**Edge case:** No extractable facts → `"facts": []`. Do not error.

---

## Stage 04 — `04_sentiment_synthesis.py`

**Purpose:** Per-pair sentiment verdict (nachvollziehbar) + aggregation.

**Pass 1 — Per-pair verdicts (parallelised, max 5 concurrent):**
For each `(hcp_id × competitor)` with non-empty facts: one LLM call.
Use `model_id` (Qwen3-235B), temperature=0.0.

Verdict schema:
```json
{
  "label": "positive",
  "confidence": "high",
  "key_quote": "...",
  "reasoning": "..."
}
```
Labels: `positive | neutral | negative | ambivalent`
Confidence: `high | medium | low`
Empty facts → `{"label": "no_data"}`, skip from aggregation.

**Pass 2 — Aggregation:**
- One LLM call per competitor: sentiment distribution + market view narrative.
- One final LLM call: overall summary across all competitors.

**Output — `data/sentiment_results.json`:**
```json
{
  "indication": "Obesity",
  "client_drug": "Ozempic",
  "hcp_verdicts": [
    {
      "hcp_id": "CUST_12345",
      "hcp_name": "Dr. M. Karthaus",
      "competitor": "Wegovy",
      "sentiment": {
        "label": "positive",
        "confidence": "high",
        "key_quote": "...",
        "reasoning": "..."
      }
    }
  ],
  "competitor_summaries": [
    {
      "competitor": "Wegovy",
      "sentiment_distribution": {"positive": 8, "neutral": 4, "negative": 2, "ambivalent": 1},
      "market_view": "..."
    }
  ],
  "overall_summary": "..."
}
```

---

## Stage 05 — `05_generate_report.py`

**Purpose:** Render results into three HTML reports + one Excel file.

All HTML must be fully offline — no CDN links, no external fonts, no remote images.
All CSS/JS inline. Light theme (white background).

---

### Report A — Competitor Intelligence Report (`report_<timestamp>.html`)

The main deliverable. For pharma commercial/medical teams.

Sections:
1. Executive summary — overall market sentiment, top signals
2. Per-competitor — sentiment distribution chart, notable HCP positions with key quotes and source links
3. Per-HCP drill-down — all competitor verdicts traceable to source
4. Short methodology note (2–3 sentences max)

---

### Report B — Plain-Language Guide (`guide_<timestamp>.html`)

**Audience: non-programmers** (commercial managers, MSLs, brand teams).
No SQL, no code, no technical jargon.

Sections:
1. **What is this report?** — one paragraph in plain language
2. **How was the data collected?** — plain explanation of the three confidence layers,
   no technical terms (e.g. "we ran three checks to make sure only doctors who
   genuinely wrote or spoke about a drug were included")
3. **How to read the sentiment labels** — explain positive/neutral/negative/ambivalent
   with a concrete pharma example for each
4. **What does confidence mean?** — explain high/medium/low with examples
5. **What this report does NOT tell you** — limitations in plain language
6. **Glossary** — define: HCP, competitor drug, sentiment, key quote, source document.
   One sentence each, no unexplained acronyms.

---

### Report C — Technical Documentation (`technical_<timestamp>.html`)

**Audience: programmers and data engineers.**

Sections:
1. **Pipeline overview** — ASCII flowchart of the 5 stages with input/output files
2. **Stage-by-stage breakdown** — for each stage: what it does, input schema,
   output schema, key config.ini params
3. **Snowflake tables used** — table name, schema, purpose, key columns:

   | Table | Schema | Purpose | Key columns |
   |-------|--------|---------|-------------|
   | `LLM_VALIDATION` | schema_final | Hard gate — confirmed HCP speaker | `IS_DOCTOR`, `IN_RELATION`, `WEBSITE_ID`, `S_CUSTOMER_ID` |
   | `WEBSITES_VERTICAL_CONTENT_FRAME_SINGLE_TBL` | schema_final | Maps website IDs to embedding row ranges | `START_REQUEST_ID_EMBEDDINGS`, `END_REQUEST_ID_EMBEDDINGS` |
   | `WEBSITES_VERTICAL_EMBEDDINGS_512` | schema_final | Vertical site chunks + 768-dim embeddings | `CHUNK`, `EMBEDDINGS`, `REQUEST_ID` |
   | `WEBSITES_CONTENT_FRAME_SINGLE` | schema_final | Public website CF mapping | `WEBSITE_ID`, `DOMAIN_VALUE` |
   | `WEBSITES_EMBEDDINGS_512` | schema_final | Public website chunks + embeddings | `CHUNK`, `EMBEDDINGS`, `WEBSITE_ID` |
   | `PUBMED_CONTENT_FRAME_SINGLE` | schema_final | PubMed HCP×article links | `PMID`, `S_CUSTOMER_ID`, `URL` |
   | `PUBMED_EMBEDDINGS_512` | schema_final | PubMed chunks + embeddings | `CHUNK`, `EMBEDDINGS`, `WEBSITE_ID` |
   | `CUSTOMER_SOURCE` | schema_tmp | HCP master record | `S_FIRSTNAME`, `S_LASTNAME`, `S_CITY`, `S_HCP_GROUP` |
   | `CONTENT_FRAME_SPEC` | schema_tmp | CF terms for Stage 01 | `DE_TERM_1`, `EN_TERM_1` |

4. **Three-layer confidence model** — technical explanation of why each layer exists
   and what failure mode it prevents
5. **LLM calls summary** — stage, model, prompt intent, temperature, max_tokens,
   parallelisation strategy
6. **Reproducing a run** — exact CLI commands for each stage in sequence
7. **Config reference** — every config.ini key, type, default, effect

---

**All outputs:**
- `results/report_<timestamp>.html` — Report A (main competitor intelligence)
- `results/guide_<timestamp>.html` — Report B (plain-language for non-programmers)
- `results/technical_<timestamp>.html` — Report C (technical docs for engineers)
- `results/report_<timestamp>.xlsx` — flat Excel, one row per HCP × competitor verdict

---

## Agent Prompts

Open each tmux window (`Ctrl+b w`), run `claude`, paste the prompt below.

### stage-01-competitors
```
You are building Stage 01 of service 1.2 (Competitive HCP Communication Monitoring).
Working directory: /workspace/a_comp_hcp_communication
Read CLAUDE.md — follow the Stage 01 spec exactly.

Build 01_identify_competitors.py from scratch.
When done and manually tested, mark Stage 01 DONE in /workspace/TASKS.md.
Do not touch any other stage files.
```

### stage-02-corpus
```
You are building Stage 02 of service 1.2 (Competitive HCP Communication Monitoring).
Working directory: /workspace/a_comp_hcp_communication
Read CLAUDE.md — follow the Stage 02 spec exactly.

The SQL template is in CLAUDE.md. Use vector_creator.py (already in this folder)
to embed query strings. Connect to Snowflake via boto3 + AWS Secrets Manager
(private key auth) — the connection pattern is in CLAUDE.md.
Build 02_validated_corpus.py from scratch.
When done, mark Stage 02 DONE in /workspace/TASKS.md.
Do not touch any other stage files.
```

### stage-03-wiki
```
You are building Stage 03 of service 1.2 (Competitive HCP Communication Monitoring).
Working directory: /workspace/a_comp_hcp_communication
Read CLAUDE.md — follow the Stage 03 spec exactly.

Build 03_wiki_extract.py from scratch.
When done, mark Stage 03 DONE in /workspace/TASKS.md.
Do not touch any other stage files.
```

### stage-04-sentiment
```
You are building Stage 04 of service 1.2 (Competitive HCP Communication Monitoring).
Working directory: /workspace/a_comp_hcp_communication
Read CLAUDE.md — follow the Stage 04 spec exactly.

Build 04_sentiment_synthesis.py from scratch.
When done, mark Stage 04 DONE in /workspace/TASKS.md.
Do not touch any other stage files.
```

### stage-05-reviewer
```
You are the reviewer for service 1.2 (Competitive HCP Communication Monitoring).
Working directory: /workspace/a_comp_hcp_communication
Read CLAUDE.md for the full spec.

Wait until TASKS.md shows all Stages 01-04 DONE.
Review all four stage files for:
- Data contract consistency (each stage output matches next stage input schema)
- Edge case handling (0 SQL results, empty facts, LLM failures, malformed JSON)
- All params in config.ini — no hardcoded values
- Three-layer confidence model preserved end-to-end (LLM_VALIDATION IS_DOCTOR=1
  AND IN_RELATION>=threshold is never bypassed)

Write findings to REVIEW.md with file:line references. Do not edit stage files.
```
