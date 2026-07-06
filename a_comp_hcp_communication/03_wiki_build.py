#!/usr/bin/env python3
"""Stage 03 — Build the per-run LLM-wiki (raw → wiki → schema).

For each competitor block from Stage 02:
  * write each source's full text as an immutable raw/ markdown file;
  * INGEST: one LLM call per source extracts grounded claims — a named doctor
    genuinely saying something about the wirkstoff, with a verbatim quote;
  * GROUND: deterministically drop any claim whose quote is not literally present
    in the source (kills fabricated / misattributed quotes);
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

# Each stage file adds the repo root to sys.path so it can import from shared/.
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


# --------------------------------------------------------------------------- #
# Grounding logic (unit-tested)
# --------------------------------------------------------------------------- #
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


# --------------------------------------------------------------------------- #
# Prompts
# --------------------------------------------------------------------------- #
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

EXCLUDE (never extract), even when a named doctor says them:
- Conflict-of-interest / financial-disclosure statements: research funding or grants, \
stock or share ownership, advisory-board membership, consulting fees, speaker \
honoraria, and case/study payments or other financial ties to a manufacturer \
(e.g. "Ich erhalte Forschungsgelder von ...", "Ich halte Aktien ...", "war Mitglied \
im Advisory Board", "Speaker Honoraria", "Case payments").
Extract only statements expressing a view or clinical claim ABOUT the drug itself — \
efficacy, safety/tolerability, dosing, mechanism, positioning, patient experience, or \
comparison with other drugs.

Assign "sentiment" by the doctor's stance TOWARD the drug:
- "positive" — favourable: efficacy, benefit, endorsement, good tolerability.
- "negative" — unfavourable OR reports a material drawback: significant side-effect \
burden, safety risk, cost concern, efficacy limitation, weight regain, need for \
lifelong therapy, muscle-mass loss, or an explicitly critical view.
- "ambivalent" — names a benefit AND a drawback together.
- "neutral" — purely descriptive/factual with no benefit or drawback implied \
(e.g. approval status, dosing schedule, mechanism, brand/generic identity).
Judge only from the quote; never invent a stance the text does not support. Extract \
critical statements with the SAME fidelity as positive ones.

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


# --------------------------------------------------------------------------- #
# Ingest + verify
# --------------------------------------------------------------------------- #
def ingest_source(bedrock, config, competitor: str, generic: str, source: dict) -> List[dict]:
    """Ingest one source → grounded, speaker-resolved claims (pre-verify)."""
    cfg = config["comp_hcp"]
    wcfg = config["wiki"]
    prompt = build_ingest_prompt(competitor, generic, source)
    try:
        raw = call_bedrock_json(bedrock, wcfg["ingest_model_id"], prompt,
                                cfg.getfloat("temperature"),
                                cfg.getint("extraction_max_tokens"))
    except Exception as err:  # noqa: BLE001
        log.error("Ingest failed for %s / %s: %s", competitor,
                  source.get("website_id"), err)
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
    cfg = config["comp_hcp"]
    wcfg = config["wiki"]
    try:
        raw = call_bedrock_json(bedrock, wcfg["verify_model_id"],
                                build_verify_prompt(claim, source),
                                cfg.getfloat("temperature"), 256)
        return bool(raw.get("verified"))
    except Exception as err:  # noqa: BLE001
        log.warning("Verify failed (drop) for %s: %s", claim.get("speaker_name"), err)
        return False


# --------------------------------------------------------------------------- #
# Knowledge graph + wiki tree
# --------------------------------------------------------------------------- #
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


def _slug(s: str) -> str:
    keep = "".join(ch if (ch.isalnum() or ch in " -_") else "_" for ch in (s or ""))
    return "_".join(keep.split()) or "untitled"


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
            page.append(f"- _{c['sentiment']}_ ({c['confidence']}): "
                        f"“{c['verbatim_quote']}” — "
                        f"[[raw/{_slug(str(c['citation']['website_id']))}]] "
                        f"{c['citation'].get('url') or ''}")
        _write_text(os.path.join(comp_dir, "wiki", f"{_slug(name)}.md"), "\n".join(page))
        index_lines.append(f"- [[{_slug(name)}]] — {len(cs)} statement(s), {flag}")
    _write_text(os.path.join(comp_dir, "wiki", "index.md"), "\n".join(index_lines))
    _write_text(os.path.join(comp_dir, "wiki", "log.md"),
                f"## run\n- ingested {len(block.get('sources', []))} source(s); "
                f"{len(claims)} grounded+verified claim(s).\n")
    # schema/
    _write_text(os.path.join(comp_dir, "schema", "knowledge_graph.json"),
                json.dumps(build_competitor_graph(block, claims), indent=2,
                           ensure_ascii=False))


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def load_config(path=CONFIG_PATH):
    if not os.path.exists(path):
        log.error("Config not found: %s", path)
        sys.exit(1)
    c = configparser.ConfigParser()
    c.read(path)
    return c


def load_blocks(path=INPUT_PATH) -> List[dict]:
    if not os.path.exists(path):
        log.error("%s not found — run Stage 02 first.", path)
        sys.exit(1)
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def main() -> None:
    ap = argparse.ArgumentParser(description="Stage 03 — build the per-run LLM-wiki.")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    if os.path.exists(OUTPUT_PATH) and not args.force:
        log.info("%s exists — skipping (use --force).", OUTPUT_PATH)
        return
    config = load_config()
    blocks = load_blocks()
    # Attach mapped_hcps onto each source for ingest-time speaker resolution.
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
                futs = {pool.submit(ingest_source, bedrock, config, comp, generic, s): s
                        for s in sources}
                for f in as_completed(futs):
                    s = futs[f]
                    for c in f.result():
                        ingested.append((c, s))
        # VERIFY (parallel over claims)
        verified: List[dict] = []
        if ingested:
            with ThreadPoolExecutor(max_workers=max(1, min(vw, len(ingested)))) as pool:
                futs = {pool.submit(verify_claim, bedrock, config, c, s): (c, s)
                        for c, s in ingested}
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
