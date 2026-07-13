#!/usr/bin/env python3
"""Shared helpers for Service 1.2 KOL identification stages: JSON parsing, name
matching, Bedrock, Snowflake connection."""

import json
import re
import time
from typing import List, Optional

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


def make_bedrock_client(profile: str, region: str = "eu-central-1"):
    """Create a Bedrock runtime client for the given AWS profile and region."""
    return boto3.Session(profile_name=profile).client(
        "bedrock-runtime", region_name=region
    )


def call_bedrock_json(bedrock, model_id: str, prompt: str, temperature: float = 0.0,
                      max_tokens: int = 4096, attempts: int = 3, backoff: int = 2) -> dict:
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


# ── Snowflake connection ───────────────────────────────────────────────────────

def connect_snowflake(aws_profile: str, warehouse: str, database: str):
    """
    Connect to Snowflake using private key fetched from AWS Secrets Manager.
    Secret name is auto-discovered from SSM: /exaris/main-stack-name →
    /{stack}/snowflake/secret-name.
    """
    import snowflake.connector
    from cryptography.hazmat.primitives import serialization
    from shared.parameter_manager import ParameterManager
    from shared.secret_reader import SecretReader

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


def resolve_tables(sf):
    """Build fully-qualified table names from the [snowflake] config section.
    Only database + schema_final + schema_tmp change per targeting; the
    CORE.PUBMED.* tables are constants."""
    db, final, tmp = sf["database"], sf["schema_final"], sf["schema_tmp"]
    return {
        "llm_validation":               f"{db}.{final}.LLM_VALIDATION",
        "rating_result_final":          f"{db}.{final}.RATING_RESULT_FINAL",
        "pubmed_cf_flag":               f"{db}.{final}.PUBMED_CONTENT_FRAME_SINGLE_TBL",
        "websites_vertical_all_source": f"{db}.{final}.WEBSITES_VERTICAL_ALL_SOURCE",
        "websites_vertical_embeddings": f"{db}.{final}.WEBSITES_VERTICAL_EMBEDDINGS_512",
        "pubmed_embeddings":            f"{db}.{final}.PUBMED_EMBEDDINGS_512",
        "content_frame_spec":           f"{db}.{tmp}.CONTENT_FRAME_SPEC",
        "customer_source":              f"{db}.{tmp}.CUSTOMER_SOURCE",
        "pubmed_mapping":               f"{db}.{tmp}.PUBMED_ARTICLE_MAPPING",
        "pubmed_article":               "CORE.PUBMED.ARTICLE",
        "pubmed_author":                "CORE.PUBMED.AUTHOR",
    }
