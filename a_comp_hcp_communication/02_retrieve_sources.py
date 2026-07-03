#!/usr/bin/env python3
"""Stage 02 — Retrieve full-content source documents for the LLM-wiki.

Track A (CF-derived competitors, source="cf"): revised LLM_VALIDATION gate
(NEAR_BY=1 AND IS_OLD=0 AND IS_DOCTOR=1 AND brand/generic in COL_KEYWORDS_*),
then Snowflake VECTOR_COSINE_SIMILARITY restricted to the matched WEBSITE_IDs,
then assemble each matched document's entire content as a raw source. The gate
also yields the mapped-HCP roster for those documents.

Track B (LLM-knowledge competitors, source="llm"): vector search across the
corpus, no LLM_VALIDATION, no mapped HCPs.

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

# Each stage file adds the repo root to sys.path so it can import from shared/.
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


# --------------------------------------------------------------------------- #
# Pure logic (unit-tested)
# --------------------------------------------------------------------------- #
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


# --------------------------------------------------------------------------- #
# SQL
# --------------------------------------------------------------------------- #
# Layer-1 gate across the vertical + public website families. Selects gated rows,
# their full CONTENT, and the mapped-HCP identity. {kw_predicate} is a coarse
# ILIKE prefilter; Python matches_keywords refines it to token boundaries.
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

# Vector search scoped to a set of website IDs (Track A).
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

# Vector search across the whole corpus (Track B).
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
    """Coarse ILIKE OR-predicate (SQL prefilter); matches_keywords refines it."""
    clauses = []
    for t in terms:
        safe = t.replace("'", "''")
        clauses.append(f"lv.COL_KEYWORDS_ORIG ILIKE '%{safe}%'")
        clauses.append(f"lv.COL_KEYWORDS_EN ILIKE '%{safe}%'")
    return " OR ".join(clauses) or "1=0"


# --------------------------------------------------------------------------- #
# Snowflake + row shaping
# --------------------------------------------------------------------------- #
def connect_snowflake(aws_profile: str, warehouse: str, database: str):
    """Open a Snowflake connection via boto3 + AWS Secrets Manager (key auth)."""
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
                r.get("CONTENT"),
                [c["text"] for c in chunks_by_id.get(wid, [])], max_chars),
            "matched_chunks": chunks_by_id.get(wid, []),
        }
    return list(by_id.values())


def _dictcur(conn):
    import snowflake.connector
    return conn.cursor(snowflake.connector.DictCursor)


def _run_vector(cur, sql: str, **fmt) -> List[dict]:
    cur.execute(sql.format(**fmt))
    return cur.fetchall()


# --------------------------------------------------------------------------- #
# Per-competitor processing
# --------------------------------------------------------------------------- #
def process_competitor_track_a(cur, config, vectorizer, competitor, indication) -> dict:
    sf, lv, rt = config["snowflake"], config["llm_validation"], config["retrieval"]
    terms = competitor_terms(competitor)
    label = terms[0] if terms else ""
    # Layer 1 — the revised gate.
    cur.execute(LAYER1_SQL.format(
        schema_final=sf["schema_final"], schema_tmp=sf["schema_tmp"],
        near_by=lv.getint("near_by"), is_old=lv.getint("is_old"),
        is_doctor=lv.getint("is_doctor"), kw_predicate=_kw_predicate(terms)))
    rows = cur.fetchall()
    # Refine the coarse ILIKE prefilter with a precise token-boundary match.
    rows = [r for r in rows if matches_keywords(
        r.get("COL_KEYWORDS_ORIG"), r.get("COL_KEYWORDS_EN"), terms)]
    if not rows:
        log.warning("Track A '%s': 0 gated rows.", label)
        return {"competitor": label, "generic": competitor.get("generic_name", ""),
                "track": "A", "mapped_hcps": [], "sources": []}
    website_ids = sorted({str(r["WEBSITE_ID"]) for r in rows})
    id_list = ",".join("'" + w.replace("'", "''") + "'" for w in website_ids)
    # Layer 2 — vector search scoped to those website IDs.
    chunks_by_id: Dict[str, list] = {}
    for q in build_query_strings(competitor, indication):
        vec = vectorizer.get_vector_from_list([q])
        vlit = f"{vec.tolist()}::VECTOR(FLOAT, {EMBEDDING_DIM})"
        for row in _run_vector(cur, VECTOR_SQL_SCOPED, vec_literal=vlit, id_list=id_list,
                               schema_final=sf["schema_final"],
                               min_similarity=rt.getfloat("min_similarity"),
                               top_chunks=rt.getint("top_chunks_per_wirkstoff")):
            wid = str(row["WEBSITE_ID"])
            chunks_by_id.setdefault(wid, []).append(
                {"text": row.get("CHUNK") or "",
                 "similarity": round(float(row.get("SIM") or 0), 6)})
    keep = set(chunks_by_id.keys()) or set(website_ids)
    keep = set(list(keep)[:rt.getint("max_sources_per_competitor")])
    sources = group_sources(rows, keep, chunks_by_id,
                            config["wiki"].getint("max_source_chars"))
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
                {"text": row.get("CHUNK") or "",
                 "similarity": round(float(row.get("SIM") or 0), 6)})
            meta_by_id.setdefault(wid, {"WEBSITE_ID": wid,
                                        "SOURCE_TYPE": row.get("SOURCE_TYPE"),
                                        "URL_VALUE": None, "CONTENT": None})
    keep = set(list(chunks_by_id.keys())[:rt.getint("max_sources_per_competitor")])
    sources = group_sources(list(meta_by_id.values()), keep, chunks_by_id,
                            config["wiki"].getint("max_source_chars"))
    return {"competitor": label, "generic": competitor.get("generic_name", ""),
            "track": "B", "mapped_hcps": [], "sources": sources}


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


def load_competitors(path=COMPETITORS_PATH):
    if not os.path.exists(path):
        log.error("%s not found — run Stage 01 first.", path)
        sys.exit(1)
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def main() -> None:
    ap = argparse.ArgumentParser(description="Stage 02 — retrieve full-content sources.")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    if os.path.exists(OUTPUT_PATH) and not args.force:
        log.info("%s exists — skipping (use --force).", OUTPUT_PATH)
        return
    config = load_config()
    data = load_competitors()
    indication = (data.get("indication") or "").strip() or None
    competitors = data.get("competitors", [])
    if not competitors:
        _write([])
        return
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
                     entry["competitor"], track, len(entry["sources"]),
                     len(entry["mapped_hcps"]))
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
