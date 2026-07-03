# LLM-Wiki Grounded HCP Communication Monitoring — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rework the middle of Service 1.2 so HCP↔wirkstoff statements are grounded in verbatim source spans via a Karpathy-style LLM-wiki (raw→wiki→schema), eliminating false attributions and distinguishing mapped vs unmapped doctors.

**Architecture:** Stage 01 (light fix) → Stage 02 `02_retrieve_sources.py` (revised LLM_VALIDATION gate + vector search scoped to matched website IDs + full-content assembly) → Stage 03 `03_wiki_build.py` (ingest grounded claims, resolve mapped/unmapped, deterministic quote-grounding + adversarial LLM verify, write raw/wiki/schema tree) → Stage 04 `04_synthesize.py` (aggregate claims, mapped/unmapped split, narratives) → Stage 05 `05_generate_report.py` (10–15 examples/section + "N more", full Excel). Shared helpers live in `pipeline_common.py`.

**Tech Stack:** Python 3.14, boto3 (Bedrock converse), snowflake-connector-python, onnxruntime (VectorCreator/Reranker — do not modify), openpyxl, pytest. External boundaries (Snowflake cursor, Bedrock, VectorCreator, Reranker) are dependency-injected so unit tests mock them.

**Reference spec:** `docs/superpowers/specs/2026-07-03-llm-wiki-grounded-hcp-monitoring-design.md`

## Global Constraints

- All tunable values in `config.ini` — no hardcoded thresholds/models/paths.
- Each stage file keeps `sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))` so it can import `shared/`.
- Do NOT modify `shared/`, `vector_creator.py`, `reranker.py`.
- `VectorCreator().get_vector_from_list([text])` → 1-D `np.ndarray` (768-dim, L2-normalised). `vec_literal` = `f"{vec.tolist()}::VECTOR(FLOAT, 768)"`.
- `Reranker().score(query, passages)` → `list[float]`, higher = better.
- Bedrock call pattern: `bedrock.converse(modelId=…, messages=[{"role":"user","content":[{"text":prompt}]}], inferenceConfig={"temperature":…, "maxTokens":…})`; response text at `resp["output"]["message"]["content"][0]["text"]`.
- Every stage resume-safe: skip if output exists unless `--force`.
- JSON checkpoints in `data/` (gitignored); wiki tree in `wiki/<ts>/` (gitignored); reports in `results/` (gitignored).
- Test runner: `.venv/bin/python -m pytest` from the service dir. Tests never touch AWS/Snowflake/ONNX — those are mocked.
- Sentiment enums: `positive|neutral|negative|ambivalent` (+ `no_data`). Confidence: `high|medium|low`.
- German-first domain ("wirkstoff", "Adipositas"); quotes preserved verbatim in original language.

---

## File Structure

- Create `a_comp_hcp_communication/pipeline_common.py` — shared JSON/Bedrock/name helpers.
- Create `a_comp_hcp_communication/02_retrieve_sources.py` — replaces `02_validated_corpus.py`.
- Create `a_comp_hcp_communication/03_wiki_build.py` — replaces `03_wiki_extract.py`.
- Create `a_comp_hcp_communication/04_synthesize.py` — replaces `04_sentiment_synthesis.py`.
- Modify `a_comp_hcp_communication/01_identify_competitors.py` — indication-bug fix.
- Modify `a_comp_hcp_communication/05_generate_report.py` — new inputs + examples/Excel.
- Modify `a_comp_hcp_communication/config.ini`, `CLAUDE.md`, `../TASKS.md`.
- Delete `02_validated_corpus.py`, `03_wiki_extract.py`, `04_sentiment_synthesis.py` after their replacements pass.
- Create `a_comp_hcp_communication/tests/` with `conftest.py` + `test_*.py` per module.

Because tests import stage files whose names start with digits (`02_…`), tests use `importlib` helpers in `conftest.py`. `pipeline_common.py` and the logic-heavy functions are imported normally.

---

## Task 0: Test scaffold

**Files:**
- Create: `a_comp_hcp_communication/tests/__init__.py` (empty)
- Create: `a_comp_hcp_communication/tests/conftest.py`
- Create: `a_comp_hcp_communication/pytest.ini`

**Interfaces:**
- Produces: `load_stage(filename)` fixture-helper importing a numeric-prefixed stage module by path; returns the module object.

- [ ] **Step 1: Create `pytest.ini`**

```ini
[pytest]
testpaths = tests
python_files = test_*.py
addopts = -q
```

- [ ] **Step 2: Create `tests/__init__.py`** (empty file)

- [ ] **Step 3: Create `tests/conftest.py`**

```python
import importlib.util
import os
import sys

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _HERE)                       # service dir (for pipeline_common, vector_creator…)
sys.path.insert(0, os.path.join(_HERE, ".."))   # repo root (for shared/)


def load_stage(filename: str):
    """Import a numeric-prefixed stage module (e.g. '02_retrieve_sources.py') by path."""
    path = os.path.join(_HERE, filename)
    mod_name = "stage_" + os.path.splitext(filename)[0]
    spec = importlib.util.spec_from_file_location(mod_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    spec.loader.exec_module(module)
    return module
```

- [ ] **Step 4: Verify collection works**

Run: `.venv/bin/python -m pytest a_comp_hcp_communication/tests -q`
Expected: `no tests ran` (exit 5) — scaffold imports cleanly.

- [ ] **Step 5: Commit**

```bash
git add a_comp_hcp_communication/tests a_comp_hcp_communication/pytest.ini
git commit -m "test: add pytest scaffold for service 1.2 rework"
```

---

## Task 1: `pipeline_common.py` — shared helpers

**Files:**
- Create: `a_comp_hcp_communication/pipeline_common.py`
- Test: `a_comp_hcp_communication/tests/test_pipeline_common.py`

**Interfaces:**
- Produces:
  - `strip_json_fences(text: str) -> str`
  - `parse_json_object(text: str) -> dict` (strip fences then `json.loads`)
  - `normalize_name(name: str) -> str` (casefold, strip titles Dr./Prof./Med., collapse spaces, drop punctuation)
  - `name_matches(extracted: str, first: str, last: str) -> bool` (last name present AND (first name or initial present))
  - `make_bedrock_client(config) -> client`
  - `call_bedrock_json(bedrock, model_id, prompt, temperature, max_tokens, attempts=3, backoff=2) -> dict`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_pipeline_common.py
import pipeline_common as pc


def test_strip_json_fences_plain():
    assert pc.strip_json_fences('{"a":1}') == '{"a":1}'

def test_strip_json_fences_fenced():
    raw = '```json\n{"a": 1}\n```'
    assert pc.parse_json_object(raw) == {"a": 1}

def test_strip_json_fences_with_prose():
    raw = 'Here you go: {"a": 2} thanks'
    assert pc.parse_json_object(raw) == {"a": 2}

def test_normalize_name_strips_titles():
    assert pc.normalize_name("Dr. med. Vesna Budić-Spasić") == "vesna budic-spasic" \
        or pc.normalize_name("Dr. med. Vesna Budić-Spasić") == "vesna budić-spasić"

def test_name_matches_last_and_first():
    assert pc.name_matches("Michael Holznagel", "Michael", "Holznagel") is True

def test_name_matches_last_and_initial():
    assert pc.name_matches("M. Holznagel", "Michael", "Holznagel") is True

def test_name_matches_wrong_person():
    assert pc.name_matches("Vesna Budić-Spasić", "Michael", "Holznagel") is False

def test_name_matches_last_only_is_not_enough():
    # last name alone must not match (too weak)
    assert pc.name_matches("Holznagel", "Michael", "Holznagel") is False
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest a_comp_hcp_communication/tests/test_pipeline_common.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pipeline_common'`.

- [ ] **Step 3: Implement `pipeline_common.py`**

```python
#!/usr/bin/env python3
"""Shared helpers for Service 1.2 stages: JSON parsing, name matching, Bedrock."""

import json
import re
import time
import unicodedata
from typing import Optional

import boto3

_TITLE_RE = re.compile(r"\b(dr|prof|med|dipl|mag|phd|md|priv|doz)\.?\b", re.IGNORECASE)
_PUNCT_RE = re.compile(r"[^\w\s-]", re.UNICODE)
_WS_RE = re.compile(r"\s+")


def strip_json_fences(text: str) -> str:
    """Remove ```json ... ``` fences and surrounding prose around a JSON object."""
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1] if "\n" in text else text
        text = text.rsplit("```", 1)[0]
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1 and end > start:
        text = text[start:end + 1]
    return text.strip()


def parse_json_object(text: str) -> dict:
    """Strip fences then parse. Raises json.JSONDecodeError on failure."""
    return json.loads(strip_json_fences(text))


def normalize_name(name: str) -> str:
    """Lowercase, drop academic titles and punctuation, collapse whitespace."""
    s = (name or "").strip()
    s = _TITLE_RE.sub(" ", s)
    s = _PUNCT_RE.sub(" ", s)
    s = _WS_RE.sub(" ", s).strip().casefold()
    return s


def _tokens(name: str) -> list:
    return [t for t in normalize_name(name).split(" ") if t]


def name_matches(extracted: str, first: str, last: str) -> bool:
    """True when an extracted speaker name plausibly denotes the (first, last) HCP.

    Requires the last name to appear AND the first name (or its initial) to appear,
    so a bare surname collision does not create a false mapping.
    """
    ex = _tokens(extracted)
    first_t = _tokens(first)
    last_t = _tokens(last)
    if not ex or not last_t:
        return False
    last_ok = all(lt in ex for lt in last_t)
    if not last_ok:
        return False
    if not first_t:
        return False
    f = first_t[0]
    first_ok = any(t == f or (len(t) == 1 and f.startswith(t)) or t.startswith(f[0]) and len(t) == 1 for t in ex)
    # accept full first name OR a single-letter initial matching the first name's initial
    first_ok = any(t == f for t in ex) or any(len(t) == 1 and t == f[0] for t in ex)
    return last_ok and first_ok


def make_bedrock_client(config):
    """Create a Bedrock runtime client from the [comp_hcp] config block."""
    cfg = config["comp_hcp"]
    region = cfg.get("bedrock_region", "eu-central-1")
    session = boto3.Session(profile_name=cfg["bedrock_profile"], region_name=region)
    return session.client("bedrock-runtime")


def call_bedrock_json(bedrock, model_id: str, prompt: str, temperature: float,
                      max_tokens: int, attempts: int = 3, backoff: int = 2) -> dict:
    """Call Bedrock converse and parse a JSON object, retrying on any failure."""
    last_err: Optional[Exception] = None
    for attempt in range(1, attempts + 1):
        try:
            resp = bedrock.converse(
                modelId=model_id,
                messages=[{"role": "user", "content": [{"text": prompt}]}],
                inferenceConfig={"temperature": temperature, "maxTokens": max_tokens},
            )
            raw = resp["output"]["message"]["content"][0]["text"]
            return parse_json_object(raw)
        except Exception as err:  # noqa: BLE001 — retry any transient failure
            last_err = err
            if attempt < attempts:
                time.sleep(backoff)
    raise last_err
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest a_comp_hcp_communication/tests/test_pipeline_common.py -v`
Expected: PASS (8 tests). Fix `normalize_name` diacritic expectation if needed — keep whichever assertion branch matches; the test allows both diacritic-preserving and stripping. If neither passes, the intended behaviour is to PRESERVE diacritics (only strip titles/punctuation), so ensure `test_normalize_name_strips_titles` passes on the `budić-spasić` branch.

- [ ] **Step 5: Commit**

```bash
git add a_comp_hcp_communication/pipeline_common.py a_comp_hcp_communication/tests/test_pipeline_common.py
git commit -m "feat: shared pipeline helpers (json, name-match, bedrock)"
```

---

## Task 2: Stage 01 indication-bug fix

**Files:**
- Modify: `a_comp_hcp_communication/01_identify_competitors.py`
- Test: `a_comp_hcp_communication/tests/test_stage01_indication.py`

**Interfaces:**
- Produces: `sanitize_indication(indication: str, competitors: list, client_drug: str) -> str` — returns `""` if the indication equals any competitor brand/generic or the client drug (a brand leaked in as indication), else the indication.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_stage01_indication.py
from conftest import load_stage
mod = load_stage("01_identify_competitors.py")


def test_indication_rejects_brand_leak():
    comps = [{"brand_name": "Saxenda", "generic_name": "Liraglutid"}]
    # LLM wrongly returned a brand as the indication
    assert mod.sanitize_indication("Wegovy", comps, "Ozempic") == ""

def test_indication_rejects_client_drug():
    assert mod.sanitize_indication("Ozempic", [], "Ozempic") == ""

def test_indication_keeps_real_indication():
    comps = [{"brand_name": "Saxenda", "generic_name": "Liraglutid"}]
    assert mod.sanitize_indication("Adipositas", comps, "Ozempic") == "Adipositas"
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest a_comp_hcp_communication/tests/test_stage01_indication.py -v`
Expected: FAIL — `AttributeError: module has no attribute 'sanitize_indication'`.

- [ ] **Step 3: Implement `sanitize_indication` and wire it into `main()`**

Add near `normalise_competitors`:

```python
def sanitize_indication(indication: str, competitors: list, client_drug: str) -> str:
    """Reject an 'indication' that is really a drug name (brand/generic/client).

    Stage 01 has been observed to emit a competitor brand (e.g. 'Wegovy') as the
    indication. An indication is a condition, never a marketed drug, so if it
    collides with any known drug name we drop it and let downstream infer.
    """
    ind = (indication or "").strip()
    if not ind:
        return ""
    banned = {(client_drug or "").strip().lower()}
    for c in competitors or []:
        banned.add((c.get("brand_name") or "").strip().lower())
        banned.add((c.get("generic_name") or "").strip().lower())
    banned.discard("")
    return "" if ind.lower() in banned else ind
```

In `main()`, replace the `final_indication` line:

```python
    final_indication = indication or (result.get("indication") or "").strip()
    final_indication = sanitize_indication(final_indication, competitors, args.client_drug)
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest a_comp_hcp_communication/tests/test_stage01_indication.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add a_comp_hcp_communication/01_identify_competitors.py a_comp_hcp_communication/tests/test_stage01_indication.py
git commit -m "fix(stage01): reject drug names leaking in as indication"
```

---

## Task 3: config.ini rework

**Files:**
- Modify: `a_comp_hcp_communication/config.ini`

**Interfaces:**
- Produces config keys consumed by Stages 02–05 (names referenced verbatim in later tasks).

- [ ] **Step 1: Replace `config.ini` with the reworked layout**

```ini
[comp_hcp]
bedrock_profile = AWS_User_Set-403014052718
bedrock_region = eu-central-1
model_id = qwen.qwen3-235b-a22b-2507-v1:0
extraction_model_id = eu.amazon.nova-pro-v1:0
temperature = 0.0
max_tokens = 2048
extraction_max_tokens = 4096

[llm_validation]
; Revised Layer-1 gate. IN_RELATION intentionally NOT gated (non-indicative).
near_by = 1
is_old = 0
is_doctor = 1

[retrieval]
top_chunks_per_wirkstoff = 100
min_similarity = 0.65
max_sources_per_competitor = 40

[wiki]
ingest_model_id = eu.amazon.nova-pro-v1:0
verify_model_id = qwen.qwen3-235b-a22b-2507-v1:0
ingest_max_workers = 5
verify_max_workers = 5
unmapped_source_mode = same_docs
wiki_lifetime = per_run
content_source = llm_validation
max_source_chars = 24000

[synthesis]
synth_max_workers = 5

[report]
examples_per_section = 15

[snowflake]
aws_profile = AdministratorAccess-311524101909
warehouse = COMPUTE_WH
database = CUST_NOVO
schema_final = ADIPOS_AMBU_FINAL
schema_tmp = ADIPOS_AMBU_V1
```

- [ ] **Step 2: Sanity-check parse**

Run: `.venv/bin/python -c "import configparser;c=configparser.ConfigParser();c.read('a_comp_hcp_communication/config.ini');print(c['llm_validation'].getint('near_by'), c['retrieval'].getint('top_chunks_per_wirkstoff'), c['wiki']['content_source'])"`
Expected: `1 100 llm_validation`

- [ ] **Step 3: Commit**

```bash
git add a_comp_hcp_communication/config.ini
git commit -m "config: revised gate, retrieval, wiki, report params"
```

---

## Task 4: Stage 02 pure logic — keyword match, query strings, content assembly

**Files:**
- Create: `a_comp_hcp_communication/02_retrieve_sources.py` (logic functions only in this task)
- Test: `a_comp_hcp_communication/tests/test_stage02_logic.py`

**Interfaces:**
- Produces:
  - `competitor_terms(competitor: dict) -> list[str]` (non-empty brand/generic, deduped)
  - `build_query_strings(competitor: dict, indication: Optional[str]) -> list[str]`
  - `matches_keywords(keywords_orig: str, keywords_en: str, terms: list[str]) -> bool` — token-boundary match (case-insensitive) of any term in either keyword column; guards the `(SELECT)` false-positive.
  - `assemble_full_text(content: Optional[str], chunk_texts: list[str], max_chars: int) -> str`
  - `dedupe_sources(rows: list[dict]) -> list[dict]` (by `website_id`, keep first)

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_stage02_logic.py
from conftest import load_stage
mod = load_stage("02_retrieve_sources.py")


def test_competitor_terms_dedupes_and_drops_empty():
    c = {"brand_name": "Saxenda", "generic_name": "Liraglutid"}
    assert mod.competitor_terms(c) == ["Saxenda", "Liraglutid"]
    assert mod.competitor_terms({"brand_name": "", "generic_name": "Liraglutid"}) == ["Liraglutid"]

def test_build_query_strings():
    c = {"brand_name": "Saxenda", "generic_name": "Liraglutid"}
    qs = mod.build_query_strings(c, "Adipositas")
    assert "Saxenda" in qs and "Liraglutid" in qs and "Saxenda Adipositas" in qs

def test_matches_keywords_hit():
    assert mod.matches_keywords("Gewichtsverlust, Saxenda, Abnehmen", "weight loss", ["Saxenda", "Liraglutid"])

def test_matches_keywords_generic_hit_english_col():
    assert mod.matches_keywords("", "SELECT, Liraglutide trial", ["Saxenda", "Liraglutide"])

def test_matches_keywords_no_substring_false_positive():
    # 'SELECT' must not match term 'ELE'; token-boundary only
    assert mod.matches_keywords("(SELECT), SELECT", "", ["ELE"]) is False

def test_matches_keywords_miss():
    assert mod.matches_keywords("Gewichtsverlust, Abnehmen", "weight loss", ["Mounjaro"]) is False

def test_assemble_full_text_prefers_content():
    assert mod.assemble_full_text("FULL DOC", ["chunk a", "chunk b"], 100) == "FULL DOC"

def test_assemble_full_text_falls_back_to_chunks():
    assert mod.assemble_full_text("", ["chunk a", "chunk b"], 100) == "chunk a\n\nchunk b"

def test_assemble_full_text_truncates():
    assert mod.assemble_full_text("x" * 50, [], 10) == "x" * 10

def test_dedupe_sources_keeps_first_by_website():
    rows = [{"website_id": "w1", "n": 1}, {"website_id": "w1", "n": 2}, {"website_id": "w2", "n": 3}]
    out = mod.dedupe_sources(rows)
    assert [r["website_id"] for r in out] == ["w1", "w2"]
    assert out[0]["n"] == 1
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest a_comp_hcp_communication/tests/test_stage02_logic.py -v`
Expected: FAIL — module/functions not defined.

- [ ] **Step 3: Create `02_retrieve_sources.py` with header + logic functions**

```python
#!/usr/bin/env python3
"""Stage 02 — Retrieve full-content source documents for the LLM-wiki.

Track A (CF-derived competitors): revised LLM_VALIDATION gate
(NEAR_BY=1 AND IS_OLD=0 AND IS_DOCTOR=1 AND brand/generic in COL_KEYWORDS_*),
then Snowflake VECTOR_COSINE_SIMILARITY restricted to the matched WEBSITE_IDs,
then assemble each matched document's entire content as a raw source. The gate
also yields the mapped-HCP roster for those documents.

Track B (LLM-knowledge competitors): vector search across the corpus, no
LLM_VALIDATION, no mapped HCPs.

Output: data/raw_sources.json (resume-safe; skipped unless --force).
"""

import argparse
import configparser
import json
import logging
import os
import re
import sys
from typing import Dict, List, Optional

import boto3

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from shared.parameter_manager import ParameterManager  # noqa: E402
from shared.secret_reader import SecretReader  # noqa: E402

from vector_creator import VectorCreator  # noqa: E402

_HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(_HERE, "config.ini")
COMPETITORS_PATH = os.path.join(_HERE, "data", "competitors.json")
OUTPUT_PATH = os.path.join(_HERE, "data", "raw_sources.json")
EMBEDDING_DIM = 768

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("stage02")


# ------------------------- pure logic (unit-tested) ------------------------- #
def competitor_terms(competitor: dict) -> List[str]:
    """Non-empty [brand, generic] search terms, order-preserving + deduped."""
    out, seen = [], set()
    for t in ((competitor.get("brand_name") or "").strip(),
              (competitor.get("generic_name") or "").strip()):
        if t and t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
    return out


def build_query_strings(competitor: dict, indication: Optional[str]) -> List[str]:
    """brand, generic, and brand(+else generic) + indication — deduped."""
    terms = competitor_terms(competitor)
    ind = (indication or "").strip()
    queries = list(terms)
    if terms and ind:
        queries.append(f"{terms[0]} {ind}")
    out, seen = [], set()
    for q in queries:
        if q and q.lower() not in seen:
            seen.add(q.lower())
            out.append(q)
    return out


def matches_keywords(keywords_orig: str, keywords_en: str, terms: List[str]) -> bool:
    """True if any term appears as a whole token in either keyword column.

    Token-boundary (not substring) so 'ELE' does not match inside '(SELECT)'.
    """
    hay = f"{keywords_orig or ''} , {keywords_en or ''}".casefold()
    tokens = set(re.findall(r"[\w-]+", hay, flags=re.UNICODE))
    for term in terms:
        term_tokens = re.findall(r"[\w-]+", (term or "").casefold(), flags=re.UNICODE)
        if term_tokens and all(tt in tokens for tt in term_tokens):
            return True
    return False


def assemble_full_text(content: Optional[str], chunk_texts: List[str], max_chars: int) -> str:
    """Prefer LLM_VALIDATION.CONTENT; fall back to concatenated chunks. Truncate."""
    text = (content or "").strip()
    if not text:
        text = "\n\n".join(t.strip() for t in chunk_texts if t and t.strip())
    if max_chars and len(text) > max_chars:
        text = text[:max_chars]
    return text


def dedupe_sources(rows: List[dict]) -> List[dict]:
    """Deduplicate source rows by website_id, keeping the first seen."""
    seen, out = set(), []
    for r in rows:
        wid = str(r.get("website_id"))
        if wid not in seen:
            seen.add(wid)
            out.append(r)
    return out
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest a_comp_hcp_communication/tests/test_stage02_logic.py -v`
Expected: PASS (10 tests).

- [ ] **Step 5: Commit**

```bash
git add a_comp_hcp_communication/02_retrieve_sources.py a_comp_hcp_communication/tests/test_stage02_logic.py
git commit -m "feat(stage02): pure retrieval logic (keyword match, queries, content assembly)"
```

---

## Task 5: Stage 02 SQL + Snowflake wiring + main

**Files:**
- Modify: `a_comp_hcp_communication/02_retrieve_sources.py`
- Test: `a_comp_hcp_communication/tests/test_stage02_search.py`

**Interfaces:**
- Consumes: Task 4 functions; `VectorCreator`.
- Produces:
  - `connect_snowflake(aws_profile, warehouse, database)` (canonical pattern)
  - `layer1_rows(cur, config, terms) -> list[dict]` — runs the gate SQL for one competitor's terms; returns rows with keys `WEBSITE_ID, S_CUSTOMER_ID, S_FIRSTNAME, S_LASTNAME, S_CITY, COL_KEYWORDS_ORIG, COL_KEYWORDS_EN, CONTENT, SOURCE_TYPE, URL_VALUE`.
  - `vector_scoped_website_ids(cur, config, vec, website_ids) -> set[str]` — top chunk website IDs restricted to the Layer-1 set.
  - `process_competitor_track_a(cur, config, vectorizer, competitor, indication) -> dict`
  - `process_competitor_track_b(cur, config, vectorizer, competitor, indication) -> dict`
  - `main()`
  - Output schema: `{competitor, generic, track, mapped_hcps:[{s_customer_id,name,city}], sources:[{website_id,source_type,url,full_text,matched_chunks:[{text,similarity}]}]}`

- [ ] **Step 1: Write the failing test (mapped-HCP roster building, mock cursor)**

```python
# tests/test_stage02_search.py
from conftest import load_stage
mod = load_stage("02_retrieve_sources.py")


def test_build_mapped_hcps_dedupes_by_customer():
    rows = [
        {"S_CUSTOMER_ID": "c1", "S_FIRSTNAME": "Michael", "S_LASTNAME": "Holznagel", "S_CITY": "Berlin"},
        {"S_CUSTOMER_ID": "c1", "S_FIRSTNAME": "Michael", "S_LASTNAME": "Holznagel", "S_CITY": "Berlin"},
        {"S_CUSTOMER_ID": "c2", "S_FIRSTNAME": "Vesna", "S_LASTNAME": "Budić", "S_CITY": "Wien"},
    ]
    hcps = mod.build_mapped_hcps(rows)
    assert {h["s_customer_id"] for h in hcps} == {"c1", "c2"}
    assert any(h["name"] == "Michael Holznagel" for h in hcps)

def test_group_sources_attaches_chunks_and_content():
    rows = [
        {"WEBSITE_ID": "w1", "SOURCE_TYPE": "VERTICAL", "URL_VALUE": "http://a",
         "CONTENT": "FULL A", "S_CUSTOMER_ID": "c1"},
    ]
    keep = {"w1"}
    chunks = {"w1": [{"text": "chunk", "similarity": 0.9}]}
    srcs = mod.group_sources(rows, keep, chunks, max_chars=100)
    assert len(srcs) == 1
    assert srcs[0]["website_id"] == "w1"
    assert srcs[0]["full_text"] == "FULL A"
    assert srcs[0]["matched_chunks"][0]["similarity"] == 0.9
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest a_comp_hcp_communication/tests/test_stage02_search.py -v`
Expected: FAIL — `build_mapped_hcps` / `group_sources` not defined.

- [ ] **Step 3: Append SQL, wiring, and `main()` to `02_retrieve_sources.py`**

```python
# --- SQL --------------------------------------------------------------------
# Layer-1 gate across the three source families. {vec_literal} is NOT used here;
# this query only selects gated rows + their content + mapped HCP identity.
LAYER1_SQL = """
SELECT lv.WEBSITE_ID, lv.S_CUSTOMER_ID, cs.S_FIRSTNAME, cs.S_LASTNAME, cs.S_CITY,
       lv.COL_KEYWORDS_ORIG, lv.COL_KEYWORDS_EN, lv.CONTENT,
       'VERTICAL' AS SOURCE_TYPE, cf.URL AS URL_VALUE
FROM {schema_final}.LLM_VALIDATION lv
JOIN {schema_final}.WEBSITES_VERTICAL_CONTENT_FRAME_SINGLE_TBL cf
    ON lv.WEBSITE_ID = cf.WEBSITE_ID AND lv.S_CUSTOMER_ID = cf.S_CUSTOMER_ID
JOIN {schema_tmp}.CUSTOMER_SOURCE cs ON lv.S_CUSTOMER_ID = cs.S_CUSTOMER_ID
WHERE lv.NEAR_BY = {near_by} AND lv.IS_OLD = {is_old} AND lv.IS_DOCTOR = {is_doctor}
  AND ({kw_predicate})

UNION ALL

SELECT lv.WEBSITE_ID, lv.S_CUSTOMER_ID, cs.S_FIRSTNAME, cs.S_LASTNAME, cs.S_CITY,
       lv.COL_KEYWORDS_ORIG, lv.COL_KEYWORDS_EN, lv.CONTENT,
       'WEBSITES' AS SOURCE_TYPE, cf.DOMAIN_VALUE AS URL_VALUE
FROM {schema_final}.LLM_VALIDATION lv
JOIN {schema_final}.WEBSITES_CONTENT_FRAME_SINGLE cf
    ON lv.WEBSITE_ID = cf.WEBSITE_ID AND lv.S_CUSTOMER_ID = cf.S_CUSTOMER_ID
JOIN {schema_tmp}.CUSTOMER_SOURCE cs ON lv.S_CUSTOMER_ID = cs.S_CUSTOMER_ID
WHERE lv.NEAR_BY = {near_by} AND lv.IS_OLD = {is_old} AND lv.IS_DOCTOR = {is_doctor}
  AND ({kw_predicate})
"""

# Vector search scoped to a set of website IDs (Track A) or global (Track B).
VECTOR_SQL_SCOPED = """
SELECT * FROM (
    SELECT e.CHUNK, e.WEBSITE_ID,
           VECTOR_COSINE_SIMILARITY(e.EMBEDDINGS, {vec_literal}) AS SIM
    FROM {schema_final}.WEBSITES_VERTICAL_EMBEDDINGS_512 e
    WHERE e.WEBSITE_ID IN ({id_list})
    UNION ALL
    SELECT e.CHUNK, e.WEBSITE_ID,
           VECTOR_COSINE_SIMILARITY(e.EMBEDDINGS, {vec_literal}) AS SIM
    FROM {schema_final}.WEBSITES_EMBEDDINGS_512 e
    WHERE e.WEBSITE_ID IN ({id_list})
) WHERE SIM >= {min_similarity}
ORDER BY SIM DESC
LIMIT {top_chunks}
"""

VECTOR_SQL_GLOBAL = """
SELECT * FROM (
    SELECT e.CHUNK, e.WEBSITE_ID, 'VERTICAL' AS SOURCE_TYPE,
           VECTOR_COSINE_SIMILARITY(e.EMBEDDINGS, {vec_literal}) AS SIM
    FROM {schema_final}.WEBSITES_VERTICAL_EMBEDDINGS_512 e
    UNION ALL
    SELECT e.CHUNK, e.WEBSITE_ID, 'WEBSITES' AS SOURCE_TYPE,
           VECTOR_COSINE_SIMILARITY(e.EMBEDDINGS, {vec_literal}) AS SIM
    FROM {schema_final}.WEBSITES_EMBEDDINGS_512 e
    UNION ALL
    SELECT e.CHUNK, e.WEBSITE_ID, 'PUBMED' AS SOURCE_TYPE,
           VECTOR_COSINE_SIMILARITY(e.EMBEDDINGS, {vec_literal}) AS SIM
    FROM {schema_final}.PUBMED_EMBEDDINGS_512 e
) WHERE SIM >= {min_similarity}
ORDER BY SIM DESC
LIMIT {top_chunks}
"""


def _kw_predicate(terms: List[str]) -> str:
    """Coarse ILIKE OR-predicate (SQL prefilter); Python matches_keywords refines it."""
    clauses = []
    for t in terms:
        safe = t.replace("'", "''")
        clauses.append(f"lv.COL_KEYWORDS_ORIG ILIKE '%{safe}%'")
        clauses.append(f"lv.COL_KEYWORDS_EN ILIKE '%{safe}%'")
    return " OR ".join(clauses) or "1=0"


def connect_snowflake(aws_profile: str, warehouse: str, database: str):
    import snowflake.connector
    from cryptography.hazmat.primitives import serialization
    session = boto3.Session(profile_name=aws_profile, region_name="eu-central-1")
    pm = ParameterManager(session)
    secret = SecretReader().get_secret(pm.get_snowflake_secret_name(), session)
    pk = serialization.load_pem_private_key(
        secret["private_key"].replace("\\n", "\n").encode("utf-8"), password=None)
    pk_bytes = pk.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption())
    return snowflake.connector.connect(
        user=secret["user"], account=secret["account"], warehouse=warehouse,
        database=database, private_key=pk_bytes)


def build_mapped_hcps(rows: List[dict]) -> List[dict]:
    """Dedupe Layer-1 rows into a mapped-HCP roster keyed by S_CUSTOMER_ID."""
    out: "Dict[str, dict]" = {}
    for r in rows:
        cid = str(r.get("S_CUSTOMER_ID") or "").strip()
        if not cid or cid in out:
            continue
        name = " ".join(p for p in ((r.get("S_FIRSTNAME") or "").strip(),
                                    (r.get("S_LASTNAME") or "").strip()) if p)
        out[cid] = {"s_customer_id": cid, "name": name or cid,
                    "city": (r.get("S_CITY") or "").strip()}
    return list(out.values())


def group_sources(rows: List[dict], keep_ids: set, chunks_by_id: Dict[str, list],
                  max_chars: int) -> List[dict]:
    """Assemble one source doc per kept website_id with full text + matched chunks."""
    by_id: "Dict[str, dict]" = {}
    for r in rows:
        wid = str(r.get("WEBSITE_ID"))
        if wid not in keep_ids or wid in by_id:
            continue
        by_id[wid] = {
            "website_id": wid,
            "source_type": r.get("SOURCE_TYPE"),
            "url": r.get("URL_VALUE"),
            "full_text": assemble_full_text(
                r.get("CONTENT"), [c["text"] for c in chunks_by_id.get(wid, [])], max_chars),
            "matched_chunks": chunks_by_id.get(wid, []),
        }
    return list(by_id.values())
```

Then add `process_competitor_track_a`, `process_competitor_track_b`, and `main()`:

```python
def _dictcur(conn):
    import snowflake.connector
    return conn.cursor(snowflake.connector.DictCursor)


def _run_vector(cur, sql: str, **fmt) -> List[dict]:
    cur.execute(sql.format(**fmt))
    return cur.fetchall()


def process_competitor_track_a(cur, config, vectorizer, competitor, indication) -> dict:
    sf, lv, rt = config["snowflake"], config["llm_validation"], config["retrieval"]
    terms = competitor_terms(competitor)
    label = terms[0] if terms else ""
    # Layer 1
    cur.execute(LAYER1_SQL.format(
        schema_final=sf["schema_final"], schema_tmp=sf["schema_tmp"],
        near_by=lv.getint("near_by"), is_old=lv.getint("is_old"),
        is_doctor=lv.getint("is_doctor"), kw_predicate=_kw_predicate(terms)))
    rows = cur.fetchall()
    # Refine coarse ILIKE with precise token match
    rows = [r for r in rows if matches_keywords(
        r.get("COL_KEYWORDS_ORIG"), r.get("COL_KEYWORDS_EN"), terms)]
    if not rows:
        log.warning("Track A '%s': 0 gated rows.", label)
        return {"competitor": label, "generic": competitor.get("generic_name", ""),
                "track": "A", "mapped_hcps": [], "sources": []}
    website_ids = sorted({str(r["WEBSITE_ID"]) for r in rows})
    id_list = ",".join("'" + w.replace("'", "''") + "'" for w in website_ids)
    # Layer 2 vector search scoped to those website IDs
    chunks_by_id: Dict[str, list] = {}
    for q in build_query_strings(competitor, indication):
        vec = vectorizer.get_vector_from_list([q])
        vlit = f"{vec.tolist()}::VECTOR(FLOAT, {EMBEDDING_DIM})"
        for row in _run_vector(cur, VECTOR_SQL_SCOPED, vec_literal=vlit, id_list=id_list,
                               min_similarity=rt.getfloat("min_similarity"),
                               top_chunks=rt.getint("top_chunks_per_wirkstoff")):
            wid = str(row["WEBSITE_ID"])
            chunks_by_id.setdefault(wid, []).append(
                {"text": row.get("CHUNK") or "", "similarity": round(float(row.get("SIM") or 0), 6)})
    keep = set(chunks_by_id.keys()) or set(website_ids)
    keep = set(list(keep)[:rt.getint("max_sources_per_competitor")])
    sources = group_sources(rows, keep, chunks_by_id, config["wiki"].getint("max_source_chars"))
    return {"competitor": label, "generic": competitor.get("generic_name", ""),
            "track": "A", "mapped_hcps": build_mapped_hcps(rows), "sources": sources}


def process_competitor_track_b(cur, config, vectorizer, competitor, indication) -> dict:
    sf, rt = config["snowflake"], config["retrieval"]
    terms = competitor_terms(competitor)
    label = terms[0] if terms else ""
    chunks_by_id: Dict[str, list] = {}
    meta_by_id: Dict[str, dict] = {}
    for q in build_query_strings(competitor, indication):
        vec = vectorizer.get_vector_from_list([q])
        vlit = f"{vec.tolist()}::VECTOR(FLOAT, {EMBEDDING_DIM})"
        for row in _run_vector(cur, VECTOR_SQL_GLOBAL, vec_literal=vlit,
                               schema_final=sf["schema_final"],
                               min_similarity=rt.getfloat("min_similarity"),
                               top_chunks=rt.getint("top_chunks_per_wirkstoff")):
            wid = str(row["WEBSITE_ID"])
            chunks_by_id.setdefault(wid, []).append(
                {"text": row.get("CHUNK") or "", "similarity": round(float(row.get("SIM") or 0), 6)})
            meta_by_id.setdefault(wid, {"WEBSITE_ID": wid, "SOURCE_TYPE": row.get("SOURCE_TYPE"),
                                        "URL_VALUE": None, "CONTENT": None})
    keep = set(list(chunks_by_id.keys())[:rt.getint("max_sources_per_competitor")])
    sources = group_sources(list(meta_by_id.values()), keep, chunks_by_id,
                            config["wiki"].getint("max_source_chars"))
    return {"competitor": label, "generic": competitor.get("generic_name", ""),
            "track": "B", "mapped_hcps": [], "sources": sources}


def load_config(path=CONFIG_PATH):
    if not os.path.exists(path):
        log.error("Config not found: %s", path); sys.exit(1)
    c = configparser.ConfigParser(); c.read(path); return c


def load_competitors(path=COMPETITORS_PATH):
    if not os.path.exists(path):
        log.error("%s not found — run Stage 01 first.", path); sys.exit(1)
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def main() -> None:
    ap = argparse.ArgumentParser(description="Stage 02 — retrieve full-content sources.")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    if os.path.exists(OUTPUT_PATH) and not args.force:
        log.info("%s exists — skipping (use --force).", OUTPUT_PATH); return
    config = load_config()
    data = load_competitors()
    indication = (data.get("indication") or "").strip() or None
    competitors = data.get("competitors", [])
    if not competitors:
        _write([]); return
    log.info("Loading embedding model …")
    vectorizer = VectorCreator()
    sf = config["snowflake"]
    conn = connect_snowflake(sf["aws_profile"], sf["warehouse"], sf["database"])
    out: List[dict] = []
    try:
        cur = _dictcur(conn)
        for c in competitors:
            track = "A" if (c.get("source") == "cf") else "B"
            fn = process_competitor_track_a if track == "A" else process_competitor_track_b
            entry = fn(cur, config, vectorizer, c, indication)
            log.info("Competitor '%s' [%s]: %d source(s), %d mapped HCP(s).",
                     entry["competitor"], track, len(entry["sources"]), len(entry["mapped_hcps"]))
            out.append(entry)
    finally:
        conn.close()
    _write(out)


def _write(out) -> None:
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2, ensure_ascii=False)
    log.info("Wrote %d competitor block(s) to %s", len(out), OUTPUT_PATH)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest a_comp_hcp_communication/tests/test_stage02_search.py -v`
Expected: PASS (2 tests). (Snowflake/embedding paths are exercised live only in the sbx sandbox.)

- [ ] **Step 5: Commit**

```bash
git add a_comp_hcp_communication/02_retrieve_sources.py a_comp_hcp_communication/tests/test_stage02_search.py
git commit -m "feat(stage02): revised gate SQL, scoped vector search, source assembly"
```

---

## Task 6: Stage 03 grounding logic — claim normalize, speaker resolve, quote-grounding

**Files:**
- Create: `a_comp_hcp_communication/03_wiki_build.py` (logic functions this task)
- Test: `a_comp_hcp_communication/tests/test_stage03_grounding.py`

**Interfaces:**
- Consumes: `pipeline_common.name_matches`, `normalize_name`.
- Produces:
  - `SENTIMENTS = ("positive","neutral","negative","ambivalent")`, `CONFIDENCES=("high","medium","low")`
  - `normalize_claim(raw: dict, wirkstoff: str, source: dict) -> Optional[dict]` — returns a claim dict or `None` if unusable (missing quote/speaker).
  - `quote_grounded(quote: str, full_text: str) -> bool` — deterministic: normalised quote is a substring of normalised source text.
  - `resolve_speaker(speaker_name: str, mapped_hcps: list) -> tuple[bool, str]` — `(mapped, s_customer_id)`.
  - `filter_grounded_claims(claims: list, source: dict) -> list` — drops claims whose quote is not present in the source (the bulletproof gate).

- [ ] **Step 1: Write the failing tests (incl. the Holznagel regression)**

```python
# tests/test_stage03_grounding.py
from conftest import load_stage
mod = load_stage("03_wiki_build.py")

SOURCE = {"website_id": "w1", "url": "http://a",
          "full_text": 'Dr. Vesna Budić-Spasić sagt: "Saxenda wirkt gut bei Adipositas."',
          "mapped_hcps": [{"s_customer_id": "c1", "name": "Michael Holznagel", "city": "Berlin"}]}


def test_quote_grounded_true():
    assert mod.quote_grounded("Saxenda wirkt gut bei Adipositas", SOURCE["full_text"]) is True

def test_quote_grounded_false_for_invented_quote():
    assert mod.quote_grounded("Saxenda ist gefährlich", SOURCE["full_text"]) is False

def test_normalize_claim_valid():
    raw = {"speaker_name": "Vesna Budić-Spasić", "verbatim_quote": "Saxenda wirkt gut bei Adipositas",
           "statement": "positive on efficacy", "sentiment": "positive", "confidence": "high"}
    c = mod.normalize_claim(raw, "Saxenda", SOURCE)
    assert c["sentiment"] == "positive" and c["wirkstoff"] == "Saxenda"
    assert c["citation"]["website_id"] == "w1"

def test_normalize_claim_bad_enum_coerced():
    raw = {"speaker_name": "X Y", "verbatim_quote": "q", "statement": "s",
           "sentiment": "great", "confidence": "certain"}
    c = mod.normalize_claim(raw, "Saxenda", SOURCE)
    assert c["sentiment"] == "neutral" and c["confidence"] == "low"

def test_normalize_claim_drops_empty_quote():
    raw = {"speaker_name": "X Y", "verbatim_quote": "  ", "sentiment": "positive"}
    assert mod.normalize_claim(raw, "Saxenda", SOURCE) is None

def test_resolve_speaker_maps_matching_hcp():
    mapped, cid = mod.resolve_speaker("Michael Holznagel", SOURCE["mapped_hcps"])
    assert mapped is True and cid == "c1"

def test_resolve_speaker_unmapped_for_other_doctor():
    mapped, cid = mod.resolve_speaker("Vesna Budić-Spasić", SOURCE["mapped_hcps"])
    assert mapped is False and cid == ""

def test_filter_grounded_drops_holznagel_false_attribution():
    # The mapped HCP (Holznagel) never speaks; only Budić-Spasić does. An ingest
    # that (wrongly) attributed a Saxenda view to Holznagel with a fabricated quote
    # must be dropped because that quote is not in the source text.
    claims = [
        {"speaker_name": "Michael Holznagel", "verbatim_quote": "Ich empfehle Saxenda",
         "wirkstoff": "Saxenda", "sentiment": "positive", "confidence": "high",
         "statement": "endorses", "citation": {"website_id": "w1", "url": "http://a"}},
        {"speaker_name": "Vesna Budić-Spasić", "verbatim_quote": "Saxenda wirkt gut bei Adipositas",
         "wirkstoff": "Saxenda", "sentiment": "positive", "confidence": "high",
         "statement": "efficacy", "citation": {"website_id": "w1", "url": "http://a"}},
    ]
    kept = mod.filter_grounded_claims(claims, SOURCE)
    assert len(kept) == 1
    assert kept[0]["speaker_name"] == "Vesna Budić-Spasić"
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest a_comp_hcp_communication/tests/test_stage03_grounding.py -v`
Expected: FAIL — module/functions not defined.

- [ ] **Step 3: Create `03_wiki_build.py` header + grounding logic**

```python
#!/usr/bin/env python3
"""Stage 03 — Build the per-run LLM-wiki (raw → wiki → schema).

For each competitor block from Stage 02:
  * write each source's full text as an immutable raw/ markdown file;
  * INGEST: one LLM call per source extracts grounded claims — a named doctor
    genuinely saying something about the wirkstoff, with a verbatim quote;
  * GROUND: deterministically drop any claim whose quote is not literally present
    in the source (kills fabricated/misattributed quotes);
  * VERIFY: one adversarial LLM call re-checks each surviving claim vs its source;
  * MAP: resolve each speaker to a mapped S_CUSTOMER_ID or flag unmapped;
  * write wiki/ pages (+ index.md, log.md) and schema/knowledge_graph.json.

Output: data/knowledge_graph.json (+ wiki/<ts>/ tree). Resume-safe unless --force.
"""

import argparse
import configparser
import json
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from pipeline_common import (call_bedrock_json, make_bedrock_client,  # noqa: E402
                             name_matches, normalize_name)

_HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(_HERE, "config.ini")
INPUT_PATH = os.path.join(_HERE, "data", "raw_sources.json")
OUTPUT_PATH = os.path.join(_HERE, "data", "knowledge_graph.json")
WIKI_ROOT = os.path.join(_HERE, "wiki")

SENTIMENTS = ("positive", "neutral", "negative", "ambivalent")
CONFIDENCES = ("high", "medium", "low")

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("stage03")


def quote_grounded(quote: str, full_text: str) -> bool:
    """Deterministic grounding: is the (whitespace-normalised) quote in the source?"""
    def norm(s: str) -> str:
        return " ".join((s or "").split()).casefold()
    q, t = norm(quote), norm(full_text)
    return bool(q) and q in t


def normalize_claim(raw: dict, wirkstoff: str, source: dict) -> Optional[dict]:
    """Coerce one raw LLM claim into the fixed schema; None if unusable."""
    if not isinstance(raw, dict):
        return None
    speaker = (raw.get("speaker_name") or "").strip()
    quote = (raw.get("verbatim_quote") or "").strip()
    if not speaker or not quote:
        return None
    sentiment = (raw.get("sentiment") or "").strip().lower()
    if sentiment not in SENTIMENTS:
        sentiment = "neutral"
    confidence = (raw.get("confidence") or "").strip().lower()
    if confidence not in CONFIDENCES:
        confidence = "low"
    return {
        "speaker_name": speaker,
        "verbatim_quote": quote,
        "statement": (raw.get("statement") or "").strip(),
        "wirkstoff": wirkstoff,
        "sentiment": sentiment,
        "confidence": confidence,
        "citation": {"website_id": source.get("website_id", ""),
                     "url": source.get("url", "")},
    }


def resolve_speaker(speaker_name: str, mapped_hcps: List[dict]) -> Tuple[bool, str]:
    """Return (mapped, s_customer_id) by matching a speaker to the mapped roster."""
    for h in mapped_hcps or []:
        name = h.get("name") or ""
        parts = name.split()
        if not parts:
            continue
        first, last = parts[0], parts[-1]
        if name_matches(speaker_name, first, last):
            return True, h.get("s_customer_id", "")
    return False, ""


def filter_grounded_claims(claims: List[dict], source: dict) -> List[dict]:
    """Keep only claims whose verbatim_quote is literally present in the source."""
    text = source.get("full_text", "")
    return [c for c in claims if quote_grounded(c.get("verbatim_quote", ""), text)]
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest a_comp_hcp_communication/tests/test_stage03_grounding.py -v`
Expected: PASS (8 tests) — including the Holznagel false-attribution regression.

- [ ] **Step 5: Commit**

```bash
git add a_comp_hcp_communication/03_wiki_build.py a_comp_hcp_communication/tests/test_stage03_grounding.py
git commit -m "feat(stage03): grounding core — claim normalize, speaker map, quote-grounding gate"
```

---

## Task 7: Stage 03 ingest/verify + wiki tree + knowledge graph + main

**Files:**
- Modify: `a_comp_hcp_communication/03_wiki_build.py`
- Test: `a_comp_hcp_communication/tests/test_stage03_wiki.py`

**Interfaces:**
- Consumes: Task 6 functions; `call_bedrock_json`, `make_bedrock_client`.
- Produces:
  - `build_ingest_prompt(wirkstoff, generic, source) -> str`
  - `build_verify_prompt(claim, source) -> str`
  - `ingest_source(bedrock, config, competitor, generic, source) -> list[dict]` (grounded, mapped, pre-verify)
  - `verify_claim(bedrock, config, claim, source) -> bool`
  - `write_wiki_tree(run_dir, competitor_block, claims) -> None` (writes raw/, wiki/, index.md, log.md, schema/)
  - `build_competitor_graph(competitor_block, claims) -> dict`
  - `main()`
  - Output schema: `{"competitors": [ {competitor, generic, track, nodes:{hcps,wirkstoffe}, claims:[…, "mapped", "s_customer_id", "verified"]} ]}`

- [ ] **Step 1: Write the failing tests (prompt smoke + wiki tree writing, Bedrock not called)**

```python
# tests/test_stage03_wiki.py
import json, os
from conftest import load_stage
mod = load_stage("03_wiki_build.py")

BLOCK = {"competitor": "Saxenda", "generic": "Liraglutid", "track": "A",
         "mapped_hcps": [{"s_customer_id": "c1", "name": "Michael Holznagel", "city": "Berlin"}],
         "sources": [{"website_id": "w1", "url": "http://a", "source_type": "VERTICAL",
                      "full_text": "Dr. Holznagel: Saxenda wirkt gut."}]}


def test_ingest_prompt_has_grounding_rules():
    p = mod.build_ingest_prompt("Saxenda", "Liraglutid", BLOCK["sources"][0])
    assert "Saxenda" in p and "verbatim" in p.lower()
    assert "only" in p.lower()  # must instruct to emit only genuine statements

def test_build_competitor_graph_nodes_and_claims():
    claims = [{"speaker_name": "Michael Holznagel", "s_customer_id": "c1", "mapped": True,
               "wirkstoff": "Saxenda", "verbatim_quote": "Saxenda wirkt gut", "statement": "x",
               "sentiment": "positive", "confidence": "high",
               "citation": {"website_id": "w1", "url": "http://a"}, "verified": True}]
    g = mod.build_competitor_graph(BLOCK, claims)
    assert g["competitor"] == "Saxenda"
    assert any(h["mapped"] for h in g["nodes"]["hcps"])
    assert g["claims"][0]["verified"] is True

def test_write_wiki_tree_creates_files(tmp_path):
    claims = [{"speaker_name": "Michael Holznagel", "s_customer_id": "c1", "mapped": True,
               "wirkstoff": "Saxenda", "verbatim_quote": "Saxenda wirkt gut", "statement": "x",
               "sentiment": "positive", "confidence": "high",
               "citation": {"website_id": "w1", "url": "http://a"}, "verified": True}]
    run_dir = str(tmp_path / "run1")
    mod.write_wiki_tree(run_dir, BLOCK, claims)
    comp_dir = os.path.join(run_dir, "Saxenda")
    assert os.path.exists(os.path.join(comp_dir, "raw", "w1.md"))
    assert os.path.exists(os.path.join(comp_dir, "wiki", "index.md"))
    assert os.path.exists(os.path.join(comp_dir, "wiki", "log.md"))
    assert os.path.exists(os.path.join(comp_dir, "schema", "knowledge_graph.json"))
    with open(os.path.join(comp_dir, "schema", "knowledge_graph.json"), encoding="utf-8") as fh:
        assert json.load(fh)["competitor"] == "Saxenda"
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest a_comp_hcp_communication/tests/test_stage03_wiki.py -v`
Expected: FAIL — functions not defined.

- [ ] **Step 3: Append prompts, ingest/verify, wiki-tree writer, graph builder, and `main()`**

```python
def _slug(s: str) -> str:
    keep = "".join(ch if (ch.isalnum() or ch in " -_") else "_" for ch in (s or ""))
    return "_".join(keep.split()) or "untitled"


def build_ingest_prompt(wirkstoff: str, generic: str, source: dict) -> str:
    names = wirkstoff if not generic else f"{wirkstoff} (Wirkstoff: {generic})"
    return f"""You are a pharmaceutical medical-affairs analyst. Extract, from the \
document below, ONLY concrete statements that a NAMED doctor makes ABOUT the drug \
"{names}".

A statement qualifies ONLY IF the same named doctor is the one expressing a view \
about the drug in the text. If a doctor is merely named on the page while the drug \
is mentioned elsewhere, and that doctor does not actually say anything about the \
drug, DO NOT extract it. Do not infer, translate, or invent. Every "verbatim_quote" \
must be copied character-for-character from the document, in its original language.

Document (source_url: {source.get('url') or '(none)'}):
\"\"\"
{source.get('full_text', '')}
\"\"\"

Respond with ONLY a JSON object in exactly this shape:
{{
  "claims": [
    {{"speaker_name": "<doctor named in the text>",
      "verbatim_quote": "<exact span copied from the document>",
      "statement": "<one short line: what they say about {wirkstoff}>",
      "sentiment": "positive|neutral|negative|ambivalent",
      "confidence": "high|medium|low"}}
  ]
}}
If there are no qualifying statements, return {{"claims": []}}."""


def build_verify_prompt(claim: dict, source: dict) -> str:
    return f"""Verify a single extracted claim against its source document. \
Answer strictly.

Claim:
  speaker: {claim.get('speaker_name')}
  drug: {claim.get('wirkstoff')}
  quote: {claim.get('verbatim_quote')}

Source document:
\"\"\"
{source.get('full_text', '')}
\"\"\"

Answer TRUE only if BOTH hold: (1) the quote appears in the document, and (2) the \
document shows THIS speaker expressing this view about the drug (not merely \
co-mentioned). Otherwise answer FALSE.

Respond with ONLY: {{"verified": true}} or {{"verified": false}}."""


def ingest_source(bedrock, config, competitor: str, generic: str, source: dict) -> List[dict]:
    """Ingest one source → grounded, speaker-resolved claims (pre-verify)."""
    cfg = config["comp_hcp"]; wcfg = config["wiki"]
    prompt = build_ingest_prompt(competitor, generic, source)
    try:
        raw = call_bedrock_json(bedrock, wcfg["ingest_model_id"], prompt,
                                cfg.getfloat("temperature"), cfg.getint("extraction_max_tokens"))
    except Exception as err:  # noqa: BLE001
        log.error("Ingest failed for %s / %s: %s", competitor, source.get("website_id"), err)
        return []
    claims = []
    for rc in raw.get("claims") or []:
        c = normalize_claim(rc, competitor, source)
        if c is None:
            continue
        mapped, cid = resolve_speaker(c["speaker_name"], source.get("mapped_hcps", []))
        c["mapped"] = mapped
        c["s_customer_id"] = cid
        claims.append(c)
    # deterministic grounding gate BEFORE spending a verify call
    return filter_grounded_claims(claims, source)


def verify_claim(bedrock, config, claim: dict, source: dict) -> bool:
    cfg = config["comp_hcp"]; wcfg = config["wiki"]
    try:
        raw = call_bedrock_json(bedrock, wcfg["verify_model_id"], build_verify_prompt(claim, source),
                                cfg.getfloat("temperature"), 256)
        return bool(raw.get("verified"))
    except Exception as err:  # noqa: BLE001
        log.warning("Verify failed (drop) for %s: %s", claim.get("speaker_name"), err)
        return False


def build_competitor_graph(block: dict, claims: List[dict]) -> dict:
    hcps: "Dict[str, dict]" = {}
    for c in claims:
        key = c.get("s_customer_id") or f"unmapped::{normalize_name(c['speaker_name'])}"
        hcps.setdefault(key, {"id": c.get("s_customer_id") or "",
                              "name": c["speaker_name"], "mapped": c.get("mapped", False)})
    return {
        "competitor": block.get("competitor", ""),
        "generic": block.get("generic", ""),
        "track": block.get("track", ""),
        "nodes": {"hcps": list(hcps.values()),
                  "wirkstoffe": [{"name": block.get("competitor", ""),
                                  "generic": block.get("generic", "")}]},
        "claims": claims,
    }


def _write_text(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def write_wiki_tree(run_dir: str, block: dict, claims: List[dict]) -> None:
    """Write raw/, wiki/ (entity pages + index.md + log.md), schema/ for one competitor."""
    comp = block.get("competitor", "untitled")
    comp_dir = os.path.join(run_dir, _slug(comp))
    # raw/
    for s in block.get("sources", []):
        fm = (f"---\nwebsite_id: {s.get('website_id')}\nurl: {s.get('url')}\n"
              f"source_type: {s.get('source_type')}\n---\n\n")
        _write_text(os.path.join(comp_dir, "raw", f"{_slug(str(s.get('website_id')))}.md"),
                    fm + (s.get("full_text") or ""))
    # wiki/ entity pages per HCP
    index_lines = [f"# {comp} — Wiki Index\n"]
    by_hcp: "Dict[str, list]" = {}
    for c in claims:
        by_hcp.setdefault(c["speaker_name"], []).append(c)
    for name, cs in by_hcp.items():
        flag = "mapped" if cs[0].get("mapped") else "not mapped"
        page = [f"# {name}  ({flag})\n", f"Statements about **{comp}**:\n"]
        for c in cs:
            page.append(f"- _{c['sentiment']}_ ({c['confidence']}): “{c['verbatim_quote']}” "
                        f"— [[raw/{_slug(str(c['citation']['website_id']))}]] {c['citation'].get('url') or ''}")
        _write_text(os.path.join(comp_dir, "wiki", f"{_slug(name)}.md"), "\n".join(page))
        index_lines.append(f"- [[{_slug(name)}]] — {len(cs)} statement(s), {flag}")
    _write_text(os.path.join(comp_dir, "wiki", "index.md"), "\n".join(index_lines))
    _write_text(os.path.join(comp_dir, "wiki", "log.md"),
                f"## run\n- ingested {len(block.get('sources', []))} source(s); "
                f"{len(claims)} grounded+verified claim(s).\n")
    # schema/
    _write_text(os.path.join(comp_dir, "schema", "knowledge_graph.json"),
                json.dumps(build_competitor_graph(block, claims), indent=2, ensure_ascii=False))


def load_config(path=CONFIG_PATH):
    if not os.path.exists(path):
        log.error("Config not found: %s", path); sys.exit(1)
    c = configparser.ConfigParser(); c.read(path); return c


def load_blocks(path=INPUT_PATH) -> List[dict]:
    if not os.path.exists(path):
        log.error("%s not found — run Stage 02 first.", path); sys.exit(1)
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def main() -> None:
    ap = argparse.ArgumentParser(description="Stage 03 — build the per-run LLM-wiki.")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    if os.path.exists(OUTPUT_PATH) and not args.force:
        log.info("%s exists — skipping (use --force).", OUTPUT_PATH); return
    config = load_config()
    blocks = load_blocks()
    # attach mapped_hcps onto each source for ingest-time resolution
    for b in blocks:
        for s in b.get("sources", []):
            s["mapped_hcps"] = b.get("mapped_hcps", [])
    run_dir = os.path.join(WIKI_ROOT, time.strftime("%Y%m%d_%H%M%S"))
    bedrock = make_bedrock_client(config)
    iw = config["wiki"].getint("ingest_max_workers")
    vw = config["wiki"].getint("verify_max_workers")
    graph = {"competitors": []}
    for b in blocks:
        comp, generic = b.get("competitor", ""), b.get("generic", "")
        sources = b.get("sources", [])
        # INGEST (parallel over sources)
        ingested: List[Tuple[dict, dict]] = []  # (claim, source)
        if sources:
            with ThreadPoolExecutor(max_workers=max(1, min(iw, len(sources)))) as pool:
                futs = {pool.submit(ingest_source, bedrock, config, comp, generic, s): s for s in sources}
                for f in as_completed(futs):
                    s = futs[f]
                    for c in f.result():
                        ingested.append((c, s))
        # VERIFY (parallel over claims)
        verified: List[dict] = []
        if ingested:
            with ThreadPoolExecutor(max_workers=max(1, min(vw, len(ingested)))) as pool:
                futs = {pool.submit(verify_claim, bedrock, config, c, s): (c, s) for c, s in ingested}
                for f in as_completed(futs):
                    c, _ = futs[f]
                    if f.result():
                        c["verified"] = True
                        verified.append(c)
        log.info("Competitor '%s': %d ingested → %d verified claim(s).",
                 comp, len(ingested), len(verified))
        write_wiki_tree(run_dir, b, verified)
        graph["competitors"].append(build_competitor_graph(b, verified))
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as fh:
        json.dump(graph, fh, indent=2, ensure_ascii=False)
    log.info("Wrote %s and wiki tree at %s", OUTPUT_PATH, run_dir)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest a_comp_hcp_communication/tests/test_stage03_wiki.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add a_comp_hcp_communication/03_wiki_build.py a_comp_hcp_communication/tests/test_stage03_wiki.py
git commit -m "feat(stage03): ingest+verify loop, wiki tree, knowledge graph output"
```

---

## Task 8: Stage 04 — synthesis over the knowledge graph

**Files:**
- Create: `a_comp_hcp_communication/04_synthesize.py`
- Test: `a_comp_hcp_communication/tests/test_stage04.py`

**Interfaces:**
- Consumes: `data/knowledge_graph.json`, `data/competitors.json`; `call_bedrock_json`, `make_bedrock_client`.
- Produces:
  - `distribution_split(claims: list) -> dict` → `{"mapped": {sent:count}, "unmapped": {sent:count}, "all": {sent:count}}`
  - `flatten_claims(graph: dict) -> list` (adds `competitor` onto each claim)
  - `build_market_view_prompt(...)`, `build_overall_prompt(...)`
  - `main()`
  - Output `data/synthesis.json`: `{indication, client_drug, claims:[…flattened…], competitor_summaries:[{competitor, distribution_split, market_view}], overall_summary}`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_stage04.py
from conftest import load_stage
mod = load_stage("04_synthesize.py")

GRAPH = {"competitors": [
    {"competitor": "Saxenda", "generic": "Liraglutid", "track": "A",
     "nodes": {"hcps": [], "wirkstoffe": []},
     "claims": [
         {"speaker_name": "A", "mapped": True, "s_customer_id": "c1", "wirkstoff": "Saxenda",
          "sentiment": "positive", "confidence": "high", "verbatim_quote": "q1",
          "statement": "s", "citation": {"website_id": "w1", "url": "u1"}, "verified": True},
         {"speaker_name": "B", "mapped": False, "s_customer_id": "", "wirkstoff": "Saxenda",
          "sentiment": "negative", "confidence": "low", "verbatim_quote": "q2",
          "statement": "s", "citation": {"website_id": "w2", "url": "u2"}, "verified": True},
     ]}]}


def test_distribution_split_counts_mapped_and_unmapped():
    claims = GRAPH["competitors"][0]["claims"]
    d = mod.distribution_split(claims)
    assert d["mapped"]["positive"] == 1 and d["unmapped"]["negative"] == 1
    assert d["all"]["positive"] == 1 and d["all"]["negative"] == 1

def test_flatten_claims_adds_competitor():
    flat = mod.flatten_claims(GRAPH)
    assert len(flat) == 2 and all(c["competitor"] == "Saxenda" for c in flat)
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest a_comp_hcp_communication/tests/test_stage04.py -v`
Expected: FAIL — module/functions not defined.

- [ ] **Step 3: Implement `04_synthesize.py`**

```python
#!/usr/bin/env python3
"""Stage 04 — Aggregate grounded claims into competitor + overall summaries.

Reads the knowledge graph (already grounded + verified, sentiment per claim), so no
per-claim re-judgement. Produces mapped/unmapped sentiment splits per competitor,
an LLM market-view narrative per competitor, and one overall summary.

Output: data/synthesis.json (resume-safe unless --force).
"""

import argparse
import configparser
import json
import logging
import os
import sys
from typing import Dict, List

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from pipeline_common import call_bedrock_json, make_bedrock_client  # noqa: E402

_HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(_HERE, "config.ini")
GRAPH_PATH = os.path.join(_HERE, "data", "knowledge_graph.json")
COMPETITORS_PATH = os.path.join(_HERE, "data", "competitors.json")
OUTPUT_PATH = os.path.join(_HERE, "data", "synthesis.json")

SENTIMENTS = ("positive", "neutral", "negative", "ambivalent")

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("stage04")


def _empty_dist() -> Dict[str, int]:
    return {s: 0 for s in SENTIMENTS}


def distribution_split(claims: List[dict]) -> dict:
    """Sentiment counts overall and split by mapped vs unmapped."""
    out = {"mapped": _empty_dist(), "unmapped": _empty_dist(), "all": _empty_dist()}
    for c in claims:
        s = c.get("sentiment")
        if s not in SENTIMENTS:
            continue
        out["all"][s] += 1
        out["mapped" if c.get("mapped") else "unmapped"][s] += 1
    return out


def flatten_claims(graph: dict) -> List[dict]:
    flat = []
    for comp in graph.get("competitors", []):
        for c in comp.get("claims", []):
            d = dict(c)
            d["competitor"] = comp.get("competitor", "")
            flat.append(d)
    return flat


def build_market_view_prompt(client_drug, indication, competitor, dist, claims) -> str:
    lines = [f"- [{'mapped' if c.get('mapped') else 'unmapped'}] {c.get('speaker_name')}: "
             f"{c.get('sentiment')} — {c.get('statement') or ''}" for c in claims]
    return f"""You are a pharmaceutical competitive-intelligence analyst.

Client drug: "{client_drug}"
Indication: "{indication or 'unspecified'}"
Competitor: "{competitor}"

Sentiment (all): {dist['all']}
Mapped HCPs: {dist['mapped']} | Unmapped doctors: {dist['unmapped']}

Grounded statements:
{chr(10).join(lines) or '(none)'}

Write "market_view": 2–4 sentences on how HCPs position "{competitor}". Distinguish \
mapped HCPs from general (unmapped) doctors where relevant. Ground strictly in the \
statements; invent nothing.

Respond with ONLY: {{"market_view": "<2-4 sentences>"}}"""


def build_overall_prompt(client_drug, indication, summaries) -> str:
    blocks = [f"{s['competitor']}: all={s['distribution_split']['all']} "
              f"mapped={s['distribution_split']['mapped']} unmapped={s['distribution_split']['unmapped']}"
              for s in summaries]
    return f"""You are writing the executive summary of an HCP sentiment monitoring \
report for "{client_drug}" ({indication or 'unspecified'}).

Per-competitor:
{chr(10).join(blocks) or '(none)'}

Write "overall_summary": 3–5 sentences on the competitive sentiment landscape among \
HCPs. Ground strictly in the numbers above; invent nothing.

Respond with ONLY: {{"overall_summary": "<3-5 sentences>"}}"""


def load_config(path=CONFIG_PATH):
    if not os.path.exists(path):
        log.error("Config not found: %s", path); sys.exit(1)
    c = configparser.ConfigParser(); c.read(path); return c


def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def main() -> None:
    ap = argparse.ArgumentParser(description="Stage 04 — synthesise grounded claims.")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    if os.path.exists(OUTPUT_PATH) and not args.force:
        log.info("%s exists — skipping (use --force).", OUTPUT_PATH); return
    config = load_config()
    graph = load_json(GRAPH_PATH, {"competitors": []})
    if not graph.get("competitors"):
        log.error("%s missing/empty — run Stage 03 first.", GRAPH_PATH); sys.exit(1)
    ctx = load_json(COMPETITORS_PATH, {})
    client_drug = (ctx.get("client_drug") or "").strip()
    indication = (ctx.get("indication") or "").strip()
    bedrock = make_bedrock_client(config)

    summaries = []
    for comp in graph["competitors"]:
        claims = comp.get("claims", [])
        dist = distribution_split(claims)
        market_view = ""
        if claims:
            try:
                raw = call_bedrock_json(bedrock, config["comp_hcp"]["model_id"],
                                        build_market_view_prompt(client_drug, indication,
                                                                 comp["competitor"], dist, claims),
                                        config["comp_hcp"].getfloat("temperature"),
                                        config["comp_hcp"].getint("max_tokens"))
                market_view = (raw.get("market_view") or "").strip()
            except Exception as err:  # noqa: BLE001
                log.error("market_view failed for %s: %s", comp["competitor"], err)
        summaries.append({"competitor": comp["competitor"],
                          "distribution_split": dist, "market_view": market_view})

    overall = ""
    try:
        raw = call_bedrock_json(bedrock, config["comp_hcp"]["model_id"],
                                build_overall_prompt(client_drug, indication, summaries),
                                config["comp_hcp"].getfloat("temperature"),
                                config["comp_hcp"].getint("max_tokens"))
        overall = (raw.get("overall_summary") or "").strip()
    except Exception as err:  # noqa: BLE001
        log.error("overall_summary failed: %s", err)

    out = {"indication": indication, "client_drug": client_drug,
           "claims": flatten_claims(graph), "competitor_summaries": summaries,
           "overall_summary": overall}
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2, ensure_ascii=False)
    log.info("Wrote %s (%d claim(s), %d competitor(s)).",
             OUTPUT_PATH, len(out["claims"]), len(summaries))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest a_comp_hcp_communication/tests/test_stage04.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add a_comp_hcp_communication/04_synthesize.py a_comp_hcp_communication/tests/test_stage04.py
git commit -m "feat(stage04): synthesis with mapped/unmapped sentiment split"
```

---

## Task 9: Stage 05 — report (examples-per-section + full Excel)

**Files:**
- Modify: `a_comp_hcp_communication/05_generate_report.py`
- Test: `a_comp_hcp_communication/tests/test_stage05.py`

**Interfaces:**
- Consumes: `data/synthesis.json` (new schema: `claims[]` with `mapped`, `s_customer_id`, `verbatim_quote`, `citation`; `competitor_summaries[].distribution_split`).
- Produces:
  - `slice_examples(items: list, n: int) -> tuple[list, int]` → `(shown, remaining_count)`
  - `mapped_badge(mapped: bool) -> str`
  - `write_excel(synthesis, path)` — one row per grounded claim (FULL results) + competitor summary sheet.
  - updated `build_report_a/b/c` reading claims.
  - `main()` reading `synthesis.json`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_stage05.py
import os
from conftest import load_stage
mod = load_stage("05_generate_report.py")


def test_slice_examples_reports_remaining():
    shown, remaining = mod.slice_examples(list(range(20)), 15)
    assert len(shown) == 15 and remaining == 5

def test_slice_examples_no_remaining():
    shown, remaining = mod.slice_examples([1, 2, 3], 15)
    assert shown == [1, 2, 3] and remaining == 0

def test_mapped_badge_text():
    assert "mapped" in mod.mapped_badge(True).lower()
    assert "not" in mod.mapped_badge(False).lower()

def test_write_excel_one_row_per_claim(tmp_path):
    synth = {"indication": "Adipositas", "client_drug": "Ozempic",
             "claims": [
                 {"speaker_name": "A", "mapped": True, "s_customer_id": "c1", "competitor": "Saxenda",
                  "statement": "x", "verbatim_quote": "q", "sentiment": "positive", "confidence": "high",
                  "citation": {"website_id": "w1", "url": "http://a"}, "verified": True}],
             "competitor_summaries": [{"competitor": "Saxenda",
                 "distribution_split": {"all": {"positive": 1, "neutral": 0, "negative": 0, "ambivalent": 0},
                                        "mapped": {"positive": 1, "neutral": 0, "negative": 0, "ambivalent": 0},
                                        "unmapped": {"positive": 0, "neutral": 0, "negative": 0, "ambivalent": 0}},
                 "market_view": "mv"}],
             "overall_summary": "os"}
    path = str(tmp_path / "out.xlsx")
    mod.write_excel(synth, path)
    from openpyxl import load_workbook
    wb = load_workbook(path)
    ws = wb["Grounded Claims"]
    assert ws.max_row == 2  # header + 1 claim
    headers = [c.value for c in ws[1]]
    assert "Mapped" in headers and "Verbatim Quote" in headers
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest a_comp_hcp_communication/tests/test_stage05.py -v`
Expected: FAIL — functions not defined / old schema.

- [ ] **Step 3: Rewrite `05_generate_report.py`**

Keep the existing `esc`, `link`, `label_badge`, `confidence_badge`, `BASE_CSS`, `html_document`, `footer_html`, `svg_distribution_chart` helpers. Change inputs to `synthesis.json` and add:

```python
SYNTHESIS_PATH = os.path.join(_HERE, "data", "synthesis.json")

def slice_examples(items, n):
    """Return (first n items, count of the remainder)."""
    if n is None or n < 0:
        return list(items), 0
    return list(items[:n]), max(0, len(items) - n)

def mapped_badge(mapped: bool) -> str:
    if mapped:
        return '<span class="badge" style="background:#1565c0">mapped HCP</span>'
    return '<span class="badge" style="background:#9333ea">not mapped</span>'
```

Report A: for each competitor, chart `distribution_split["all"]`, show market_view, then the competitor's claims via `slice_examples(claims, examples_per_section)` — each example rendering speaker name + `mapped_badge` + `S_CUSTOMER_ID` (if mapped) + `label_badge(sentiment)` + `confidence_badge` + verbatim quote + source link — followed by `"+ N more — see Excel export"` when `remaining > 0`. Per-HCP drill-down groups claims by `(s_customer_id or speaker_name)` and likewise slices to N with a "+N more" note. Report B adds a "Mapped vs general doctors" plain-language subsection. Report C updates the gate description (`NEAR_BY=1 AND IS_OLD=0 AND IS_DOCTOR=1`, no `IN_RELATION`), the new stage files/schemas, and the wiki architecture. `write_excel` writes sheet **"Grounded Claims"** (columns: HCP/Speaker, Mapped, S_CUSTOMER_ID, Competitor, Sentiment, Confidence, Statement, Verbatim Quote, Source URL, Verified) — one row per claim — plus a **"Competitor Summary"** sheet with the mapped/unmapped/all split. `main()` loads `synthesis.json` (required), keeps the three HTML + one XLSX outputs and the `report_*.html` skip/`--force` behaviour.

Full `write_excel`:

```python
def write_excel(synthesis, path):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    wb = Workbook(); ws = wb.active; ws.title = "Grounded Claims"
    headers = ["HCP/Speaker", "Mapped", "S_CUSTOMER_ID", "Competitor", "Sentiment",
               "Confidence", "Statement", "Verbatim Quote", "Source URL", "Verified"]
    ws.append(headers)
    hf = Font(bold=True, color="FFFFFF"); fill = PatternFill("solid", fgColor="37474F")
    for i, _ in enumerate(headers, 1):
        ws.cell(row=1, column=i).font = hf; ws.cell(row=1, column=i).fill = fill
    for c in synthesis.get("claims", []):
        cit = c.get("citation", {}) or {}
        ws.append([c.get("speaker_name", ""), "yes" if c.get("mapped") else "no",
                   c.get("s_customer_id", ""), c.get("competitor", ""), c.get("sentiment", ""),
                   c.get("confidence", ""), c.get("statement", ""), c.get("verbatim_quote", ""),
                   cit.get("url", ""), "yes" if c.get("verified") else "no"])
    for i, w in enumerate([22, 8, 16, 16, 12, 12, 40, 60, 40, 9], 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    wrap = Alignment(vertical="top", wrap_text=True)
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = wrap
    ws.freeze_panes = "A2"
    ws2 = wb.create_sheet("Competitor Summary")
    ws2.append(["Competitor", "Scope", "Positive", "Neutral", "Negative", "Ambivalent", "Market View"])
    for i in range(1, 8):
        ws2.cell(row=1, column=i).font = hf; ws2.cell(row=1, column=i).fill = fill
    for s in synthesis.get("competitor_summaries", []):
        ds = s.get("distribution_split", {})
        for scope in ("all", "mapped", "unmapped"):
            d = ds.get(scope, {})
            ws2.append([s.get("competitor", ""), scope, d.get("positive", 0), d.get("neutral", 0),
                        d.get("negative", 0), d.get("ambivalent", 0),
                        s.get("market_view", "") if scope == "all" else ""])
    for i, w in enumerate([18, 10, 10, 10, 10, 12, 70], 1):
        ws2.column_dimensions[get_column_letter(i)].width = w
    wb.save(path)
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest a_comp_hcp_communication/tests/test_stage05.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add a_comp_hcp_communication/05_generate_report.py a_comp_hcp_communication/tests/test_stage05.py
git commit -m "feat(stage05): grounded-claim report, examples-per-section, full Excel"
```

---

## Task 10: Retire old stages, update docs, full test sweep

**Files:**
- Delete: `a_comp_hcp_communication/02_validated_corpus.py`, `03_wiki_extract.py`, `04_sentiment_synthesis.py`
- Modify: `a_comp_hcp_communication/CLAUDE.md`, `../TASKS.md`

- [ ] **Step 1: Delete superseded stage files**

```bash
git rm a_comp_hcp_communication/02_validated_corpus.py \
       a_comp_hcp_communication/03_wiki_extract.py \
       a_comp_hcp_communication/04_sentiment_synthesis.py
```

- [ ] **Step 2: Update `CLAUDE.md`**

Replace the "Three-Layer Confidence Model", "Pipeline", and per-stage sections to describe: the revised Layer-1 gate (`NEAR_BY=1 AND IS_OLD=0 AND IS_DOCTOR=1` + brand/generic in `COL_KEYWORDS_*`, `IN_RELATION` dropped); Track A vs Track B; the LLM-wiki (raw→wiki→schema) with deterministic quote-grounding + adversarial verify; mapped vs unmapped HCPs; the new file names (`02_retrieve_sources.py`, `03_wiki_build.py`, `04_synthesize.py`) and outputs (`data/raw_sources.json`, `data/knowledge_graph.json`, `data/synthesis.json`, `wiki/<ts>/`). Update the Snowflake tables table to note `LLM_VALIDATION.CONTENT` and `COL_KEYWORDS_ORIG/EN` usage.

- [ ] **Step 3: Update `../TASKS.md`** — mark the rework tasks and note the new pipeline shape.

- [ ] **Step 4: Full test sweep**

Run: `.venv/bin/python -m pytest a_comp_hcp_communication/tests -v`
Expected: PASS (all tests across tasks 1–9).

- [ ] **Step 5: Byte-compile all stages (import-time sanity)**

Run: `.venv/bin/python -m py_compile a_comp_hcp_communication/01_identify_competitors.py a_comp_hcp_communication/02_retrieve_sources.py a_comp_hcp_communication/03_wiki_build.py a_comp_hcp_communication/04_synthesize.py a_comp_hcp_communication/05_generate_report.py a_comp_hcp_communication/pipeline_common.py`
Expected: no output, exit 0.

- [ ] **Step 6: Commit**

```bash
git add -A a_comp_hcp_communication ../TASKS.md
git commit -m "chore: retire v2 stages, update docs for LLM-wiki rework"
```

---

## Self-Review notes

- **Spec coverage:** Q1 (grounded-primary) → Stages 03/04/05 claim-centric. Q2 (same_docs) → `unmapped_source_mode=same_docs`, ingest reads only Stage-02 docs; broader search not built (config flag reserved). Q3 (per_run) → `wiki/<ts>/` run dir. Q4 (CONTENT + fallback) → `assemble_full_text`. Indication bug → Task 2. Two tracks → Task 5. 10–15 examples + "N more" + full Excel → Task 9. Bulletproofing → deterministic `filter_grounded_claims` (Task 6) + adversarial `verify_claim` (Task 7) + Holznagel regression test.
- **Live-run caveat:** Snowflake/Bedrock/ONNX/`/assets` are absent in the dev box, so Stages 02–04's network paths are exercised only in the sbx sandbox; unit tests cover all pure logic + I/O shaping via mocks and tmp dirs.
- **Type consistency:** claim dict keys (`speaker_name, verbatim_quote, statement, wirkstoff, sentiment, confidence, mapped, s_customer_id, citation{website_id,url}, verified`) are identical across Tasks 6–9. `distribution_split` shape (`mapped/unmapped/all`) consistent between Tasks 8 and 9.
