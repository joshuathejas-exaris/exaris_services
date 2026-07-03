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

# Each stage file adds the repo root to sys.path so it can import from shared/.
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
    """One flat list of claims across competitors, each tagged competitor + generic."""
    flat = []
    for comp in graph.get("competitors", []):
        for c in comp.get("claims", []):
            d = dict(c)
            d["competitor"] = comp.get("competitor", "")
            d["generic"] = comp.get("generic", "")
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

Write "market_view": 2-4 sentences on how HCPs position "{competitor}". Distinguish \
mapped HCPs from general (unmapped) doctors where relevant. Ground strictly in the \
statements; invent nothing.

Respond with ONLY: {{"market_view": "<2-4 sentences>"}}"""


def build_overall_prompt(client_drug, indication, summaries) -> str:
    blocks = [f"{s['competitor']}: all={s['distribution_split']['all']} "
              f"mapped={s['distribution_split']['mapped']} "
              f"unmapped={s['distribution_split']['unmapped']}"
              for s in summaries]
    return f"""You are writing the executive summary of an HCP sentiment monitoring \
report for "{client_drug}" ({indication or 'unspecified'}).

Per-competitor:
{chr(10).join(blocks) or '(none)'}

Write "overall_summary": 3-5 sentences on the competitive sentiment landscape among \
HCPs. Ground strictly in the numbers above; invent nothing.

Respond with ONLY: {{"overall_summary": "<3-5 sentences>"}}"""


def load_config(path=CONFIG_PATH):
    if not os.path.exists(path):
        log.error("Config not found: %s", path)
        sys.exit(1)
    c = configparser.ConfigParser()
    c.read(path)
    return c


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
        log.info("%s exists — skipping (use --force).", OUTPUT_PATH)
        return
    config = load_config()
    graph = load_json(GRAPH_PATH, {"competitors": []})
    if not graph.get("competitors"):
        log.error("%s missing/empty — run Stage 03 first.", GRAPH_PATH)
        sys.exit(1)
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
                raw = call_bedrock_json(
                    bedrock, config["comp_hcp"]["model_id"],
                    build_market_view_prompt(client_drug, indication,
                                             comp["competitor"], dist, claims),
                    config["comp_hcp"].getfloat("temperature"),
                    config["comp_hcp"].getint("max_tokens"))
                market_view = (raw.get("market_view") or "").strip()
            except Exception as err:  # noqa: BLE001
                log.error("market_view failed for %s: %s", comp["competitor"], err)
        summaries.append({"competitor": comp["competitor"],
                          "generic": comp.get("generic", ""),
                          "distribution_split": dist, "market_view": market_view})

    overall = ""
    try:
        raw = call_bedrock_json(
            bedrock, config["comp_hcp"]["model_id"],
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
