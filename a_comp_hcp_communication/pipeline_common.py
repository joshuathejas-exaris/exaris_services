#!/usr/bin/env python3
"""Shared helpers for Service 1.2 stages: JSON parsing, name matching, Bedrock."""

import json
import re
import time
from typing import List, Optional

import boto3

_TITLE_RE = re.compile(r"\b(dr|prof|med|dipl|mag|phd|md|priv|doz)\.?\b", re.IGNORECASE)
_PUNCT_RE = re.compile(r"[^\w\s-]", re.UNICODE)
_WS_RE = re.compile(r"\s+")

# Financial conflict-of-interest / disclosure language (DE + EN). Note: study/trial
# and "board" membership terms are disclosure signals here, so they are NOT treated
# as clinical signals below.
_COI_PATTERNS = re.compile(
    r"forschungsgeld|forschungsförder|drittmittel|"
    r"\baktien\b|\bshares?\b|\bstocks?\b|"
    r"advisory board|\bbeirat\b|"
    r"honorar|honoraria|vortragshonorar|"
    r"case payment|"
    r"\bberater|consult(?:ing|ant)|"
    r"interessenkonflikt|conflict of interest|declaration of interest|"
    r"finanzielle (?:zuwendung|unterstützung)|"
    r"research (?:funding|grant)|grants? from",
    re.IGNORECASE,
)

# Clinical-signal vocabulary: if present, the statement carries real drug content and
# is kept even if it also mentions a financial tie (conservative). Deliberately
# excludes generic words like "study"/"board" that appear inside disclosures.
_CLINICAL_SIGNAL = re.compile(
    r"gewicht|weight|abnehm|"
    r"nebenwirkung|side.?effect|verträglich|toleran|tolerab|"
    r"wirksam|wirkung|efficac|effektiv|"
    r"blutzucker|hba1c|gluk|glyk|"
    r"appetit|sättig|satiety|"
    r"dosier|dosis|dosing|"
    r"übelkeit|erbrechen|durchfall|muskel|"
    r"reduktion|reduzier|verbesser|improve|"
    r"empfehl|prescrib|verordn",
    re.IGNORECASE,
)


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
    """Lowercase, drop academic titles and punctuation, collapse whitespace.

    Diacritics are preserved (only titles like 'Dr.'/'med.' and punctuation are
    stripped) so 'Budić' stays distinct.
    """
    s = (name or "").strip()
    s = _TITLE_RE.sub(" ", s)
    s = _PUNCT_RE.sub(" ", s)
    s = _WS_RE.sub(" ", s).strip().casefold()
    return s


def _tokens(name: str) -> List[str]:
    return [t for t in normalize_name(name).split(" ") if t]


def is_coi_disclosure(quote: str, statement: str = "") -> bool:
    """True when the text is primarily a financial conflict-of-interest disclosure.

    Conservative: fires only when a disclosure pattern matches AND no clinical-signal
    vocabulary is present, so a statement that both discloses a financial tie and makes
    a genuine claim about the drug is kept.
    """
    text = f"{quote or ''} {statement or ''}"
    if not _COI_PATTERNS.search(text):
        return False
    if _CLINICAL_SIGNAL.search(text):
        return False
    return True


def name_matches(extracted: str, first: str, last: str) -> bool:
    """True when an extracted speaker name plausibly denotes the (first, last) HCP.

    Requires the last name to appear AND the first name (or its initial) to appear,
    so a bare surname collision does not create a false mapping.
    """
    ex = _tokens(extracted)
    first_t = _tokens(first)
    last_t = _tokens(last)
    if not ex or not last_t or not first_t:
        return False
    last_ok = all(lt in ex for lt in last_t)
    if not last_ok:
        return False
    f = first_t[0]
    # Accept the full first name OR a single-letter initial of it.
    first_ok = any(t == f for t in ex) or any(len(t) == 1 and t == f[0] for t in ex)
    return first_ok


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
