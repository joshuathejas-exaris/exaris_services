#!/usr/bin/env python3
"""Stage 01 — Identify competitor drugs for the client's drug.

Parses content-frame (CF) terms from a CSV or Snowflake, then asks a Bedrock
LLM to map those terms to concrete competitor drugs (brand + generic names),
supplementing from model knowledge when the CF yields fewer than two.

Output: data/competitors.json (resume-safe; skipped if present unless --force).
"""

import argparse
import configparser
import csv
import json
import logging
import os
import sys
import time
from typing import List, Optional, Tuple

import boto3

# Each stage file adds the repo root to sys.path so it can import from shared/.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from shared.parameter_manager import ParameterManager  # noqa: E402
from shared.secret_reader import SecretReader  # noqa: E402

_HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(_HERE, "config.ini")
OUTPUT_PATH = os.path.join(_HERE, "data", "competitors.json")

MIN_COMPETITORS_FROM_CF = 2
RETRY_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 2

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("stage01")


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
def load_config(path: str = CONFIG_PATH) -> configparser.ConfigParser:
    """Load config.ini, erroring clearly if it is missing."""
    if not os.path.exists(path):
        log.error("Config file not found: %s", path)
        sys.exit(1)
    config = configparser.ConfigParser()
    config.read(path)
    return config


# --------------------------------------------------------------------------- #
# CF term parsing
# --------------------------------------------------------------------------- #
def _extract_terms(rows: List[dict]) -> Tuple[Optional[str], List[str]]:
    """Turn CF rows into (indication, competitor_candidate_terms).

    Row 1 is always the indication. Every subsequent row contributes its
    German and English terms as candidate competitor keywords.
    """
    indication: Optional[str] = None
    terms: List[str] = []
    seen = set()
    for i, row in enumerate(rows):
        # Column lookup is case-insensitive to survive header casing quirks.
        lookup = {k.upper(): (v or "").strip() for k, v in row.items() if k}
        de = lookup.get("DE_TERM_1", "")
        en = lookup.get("EN_TERM_1", "")
        if i == 0:
            indication = en or de or None
            continue
        for term in (en, de):
            key = term.lower()
            if term and key not in seen:
                seen.add(key)
                terms.append(term)
    return indication, terms


def parse_cf_terms_from_csv(csv_path: str) -> Tuple[Optional[str], List[str]]:
    """Read CF terms from a CSV with DE_TERM_1 / EN_TERM_1 columns."""
    if not os.path.exists(csv_path):
        log.error("CF CSV not found: %s", csv_path)
        sys.exit(1)
    with open(csv_path, newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        log.warning("CF CSV %s is empty — no CF terms extracted.", csv_path)
        return None, []
    return _extract_terms(rows)


def connect_snowflake(aws_profile: str, warehouse: str, database: str):
    """Open a Snowflake connection via boto3 + AWS Secrets Manager (key auth).

    Canonical connection pattern for this service — Stage 02 copies it verbatim.
    Relies on shared/ (ParameterManager, SecretReader) at the repo root.
    """
    # Imported lazily so CSV / knowledge-only runs need no Snowflake stack.
    import snowflake.connector
    from cryptography.hazmat.primitives import serialization

    session = boto3.Session(profile_name=aws_profile, region_name="eu-central-1")
    pm = ParameterManager(session)
    secret_name = pm.get_snowflake_secret_name()
    secret = SecretReader().get_secret(secret_name, session)

    private_key_str = secret["private_key"].replace("\\n", "\n")
    private_key = serialization.load_pem_private_key(
        private_key_str.encode("utf-8"), password=None
    )
    private_key_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return snowflake.connector.connect(
        user=secret["user"],
        account=secret["account"],
        warehouse=warehouse,
        database=database,
        private_key=private_key_bytes,
    )


def parse_cf_terms_from_snowflake(
    config: configparser.ConfigParser,
) -> Tuple[Optional[str], List[str]]:
    """Read CF terms from {schema_tmp}.CONTENT_FRAME_SPEC in Snowflake."""
    import snowflake.connector

    sf = config["snowflake"]
    conn = connect_snowflake(
        aws_profile=sf["aws_profile"],
        warehouse=sf["warehouse"],
        database=sf["database"],
    )
    try:
        cur = conn.cursor(snowflake.connector.DictCursor)
        cur.execute(
            f"SELECT DE_TERM_1, EN_TERM_1 FROM {sf['schema_tmp']}.CONTENT_FRAME_SPEC"
        )
        rows = cur.fetchall()
    finally:
        conn.close()

    if not rows:
        log.warning("CONTENT_FRAME_SPEC returned no rows — no CF terms extracted.")
        return None, []
    return _extract_terms(rows)


# --------------------------------------------------------------------------- #
# Bedrock
# --------------------------------------------------------------------------- #
def build_prompt(
    client_drug: str, indication: Optional[str], cf_terms: List[str]
) -> str:
    """Build the competitor-identification prompt."""
    indication_line = (
        f'The therapeutic indication is: "{indication}".'
        if indication
        else "The therapeutic indication is not provided — infer it from the "
        "client drug."
    )
    if cf_terms:
        terms_block = "\n".join(f"- {t}" for t in cf_terms)
        cf_line = (
            "The following content-frame (CF) terms were extracted from the "
            "client's monitoring configuration. Some of these are competitor "
            "drug names or generics; others may be unrelated keywords:\n"
            f"{terms_block}"
        )
    else:
        cf_line = "No content-frame terms are available."

    return f"""You are a pharmaceutical competitive-intelligence analyst.

Client drug: "{client_drug}"
{indication_line}

{cf_line}

Task: identify the competitor drugs to "{client_drug}" for this indication.

Rules:
- A competitor is a distinct marketed drug used for the same indication.
- Do NOT list the client drug itself or its own generic name as a competitor.
- For any competitor that clearly corresponds to one of the CF terms above,
  set "source" to "cf".
- If fewer than {MIN_COMPETITORS_FROM_CF} competitors can be derived from the
  CF terms, supplement with well-known competitors from your own knowledge and
  set their "source" to "llm".
- Provide both the brand_name and the generic_name for each competitor. If a
  value is unknown, use an empty string.

Respond with ONLY a JSON object, no prose, in exactly this shape:
{{
  "indication": "<the indication>",
  "competitors": [
    {{"brand_name": "<brand>", "generic_name": "<generic>", "source": "cf"}}
  ]
}}"""


def _strip_json_fences(text: str) -> str:
    """Remove ```json ... ``` fences an LLM may wrap the JSON in."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1] if "\n" in text else text
        text = text.rsplit("```", 1)[0]
    # Fall back to the outermost JSON object if surrounding prose slipped in.
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1 and end > start:
        text = text[start : end + 1]
    return text.strip()


def call_bedrock(config: configparser.ConfigParser, prompt: str) -> dict:
    """Call Bedrock converse and parse the JSON response, retrying on failure."""
    cfg = config["comp_hcp"]
    region = cfg.get("bedrock_region", "eu-central-1")
    session = boto3.Session(
        profile_name=cfg["bedrock_profile"], region_name=region
    )
    bedrock = session.client("bedrock-runtime")

    last_err: Optional[Exception] = None
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            response = bedrock.converse(
                modelId=cfg["model_id"],
                messages=[{"role": "user", "content": [{"text": prompt}]}],
                inferenceConfig={
                    "temperature": cfg.getfloat("temperature"),
                    "maxTokens": cfg.getint("max_tokens"),
                },
            )
            raw = response["output"]["message"]["content"][0]["text"]
            return json.loads(_strip_json_fences(raw))
        except Exception as err:  # noqa: BLE001 — retry on any transient failure
            last_err = err
            log.warning(
                "Bedrock call failed (attempt %d/%d): %s",
                attempt,
                RETRY_ATTEMPTS,
                err,
            )
            if attempt < RETRY_ATTEMPTS:
                time.sleep(RETRY_BACKOFF_SECONDS)

    log.error("Bedrock call failed after %d attempts.", RETRY_ATTEMPTS)
    raise last_err


# --------------------------------------------------------------------------- #
# Normalisation
# --------------------------------------------------------------------------- #
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


def resolve_indication(cf_indication, llm_indication, cf_source_used,
                       competitors, client_drug):
    """Pick the indication and record its provenance.

    When a CF source is used, CONTENT_FRAME_SPEC row 1 is authoritative and the
    LLM's guess is ignored entirely — this stops a hallucinated indication (e.g.
    'Pathovy') from leaking in. Otherwise the (sanitised) LLM indication is used.
    A drug-name collision or an empty value yields ('', 'none').

    Returns (indication, indication_source) with source in {cf_spec, llm, none}.
    """
    if cf_source_used:
        ind = sanitize_indication((cf_indication or "").strip(), competitors, client_drug)
        return (ind, "cf_spec") if ind else ("", "none")
    ind = sanitize_indication((llm_indication or "").strip(), competitors, client_drug)
    return (ind, "llm") if ind else ("", "none")


def normalise_competitors(client_drug: str, raw: List[dict]) -> List[dict]:
    """Clean, dedupe, and drop self-references from the LLM competitor list."""
    client_lower = client_drug.strip().lower()
    out: List[dict] = []
    seen = set()
    for item in raw or []:
        brand = (item.get("brand_name") or "").strip()
        generic = (item.get("generic_name") or "").strip()
        source = (item.get("source") or "llm").strip().lower()
        if source not in ("cf", "llm"):
            source = "llm"
        if not brand and not generic:
            continue
        # Never list the client drug (by brand or generic) as its own competitor.
        if client_lower in (brand.lower(), generic.lower()):
            continue
        key = (brand.lower(), generic.lower())
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {"brand_name": brand, "generic_name": generic, "source": source}
        )
    return out


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Stage 01 — identify competitor drugs for the client drug."
    )
    parser.add_argument("--client-drug", required=True, help="Client drug name.")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--cf-data", help="Path to a CF terms CSV.")
    source.add_argument(
        "--from-snowflake",
        action="store_true",
        help="Read CF terms from Snowflake CONTENT_FRAME_SPEC.",
    )
    parser.add_argument(
        "--force", action="store_true", help="Overwrite existing output."
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if os.path.exists(OUTPUT_PATH) and not args.force:
        log.info("%s exists — skipping (use --force to rebuild).", OUTPUT_PATH)
        return

    config = load_config()

    # Source the CF terms.
    if args.cf_data:
        log.info("Parsing CF terms from CSV: %s", args.cf_data)
        indication, cf_terms = parse_cf_terms_from_csv(args.cf_data)
    elif args.from_snowflake:
        log.info("Parsing CF terms from Snowflake CONTENT_FRAME_SPEC.")
        indication, cf_terms = parse_cf_terms_from_snowflake(config)
    else:
        log.info("No CF source provided — relying on model knowledge only.")
        indication, cf_terms = None, []

    log.info(
        "Indication=%s, %d CF candidate term(s).",
        indication or "(unknown)",
        len(cf_terms),
    )

    prompt = build_prompt(args.client_drug, indication, cf_terms)
    result = call_bedrock(config, prompt)

    competitors = normalise_competitors(
        args.client_drug, result.get("competitors", [])
    )
    # A CF source makes CONTENT_FRAME_SPEC row 1 authoritative for the indication;
    # only a knowledge-only run trusts the LLM's guess. Provenance is recorded so
    # Stage 02 knows whether to trust the indication for query augmentation.
    cf_source_used = bool(args.cf_data or args.from_snowflake)
    final_indication, indication_source = resolve_indication(
        indication, result.get("indication"), cf_source_used,
        competitors, args.client_drug,
    )

    if not competitors:
        log.warning("No competitors identified for %s.", args.client_drug)

    cf_count = sum(1 for c in competitors if c["source"] == "cf")
    log.info(
        "Identified %d competitor(s) (%d from CF, %d from model knowledge). "
        "Indication=%r (source=%s).",
        len(competitors),
        cf_count,
        len(competitors) - cf_count,
        final_indication or "(none)",
        indication_source,
    )

    output = {
        "indication": final_indication,
        "indication_source": indication_source,
        "client_drug": args.client_drug,
        "competitors": competitors,
    }

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as fh:
        json.dump(output, fh, indent=2, ensure_ascii=False)
    log.info("Wrote %s", OUTPUT_PATH)


if __name__ == "__main__":
    main()
