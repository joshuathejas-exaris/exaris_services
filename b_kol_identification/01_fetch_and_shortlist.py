"""
Stage 01: Fetch candidate counts and shortlist the top-N KOL candidates.
Reads:  data/input.json
Writes: data/shortlist.json  (resume-safe — skips if exists, unless --force)
"""
import configparser, json, logging, os, re, sys
from datetime import datetime

logging.basicConfig(format="%(asctime)s [%(levelname)s] %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)
_DIR = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(_DIR, ".."))


def build_pca_terms_query(content_frame_spec: str, use_pca_only: bool) -> str:
    base = f"SELECT COL_MAP AS TERM_KEY, EN_TERM_1 AS TERM_EN FROM {content_frame_spec}"
    return base + " WHERE UPPER(PCA) = 'X'" if use_pca_only else base


def term_ilike_predicate(term_texts: list) -> str:
    parts = []
    for t in term_texts:
        safe = t.replace("'", "''")
        parts.append(f"COL_KEYWORDS_ORIG ILIKE '%{safe}%' OR COL_KEYWORDS_EN ILIKE '%{safe}%'")
    return " OR ".join(parts) if parts else "TRUE"


def build_web_candidates_query(llm_validation: str, term_predicate: str, in_relation_min: int) -> str:
    return f"""
SELECT lv.S_CUSTOMER_ID, lv.WEBSITE_ID,
       lv.COL_KEYWORDS_ORIG, lv.COL_KEYWORDS_EN
FROM {llm_validation} lv
WHERE lv.NEAR_BY = 1 AND lv.IS_OLD = 0 AND lv.IS_DOCTOR = 1
  AND lv.IN_RELATION > {in_relation_min}
  AND ({term_predicate})
""".strip()


def build_pubmed_candidates_query(pubmed_mapping: str, pubmed_cf_flag: str,
                                  cf_cols: list, window_years: int, current_year: int) -> str:
    cutoff = current_year - window_years
    cf_sum = " + ".join(f"COALESCE(cf.{c}, 0)" for c in cf_cols) or "0"
    cf_any = " OR ".join(f"cf.{c} > 0" for c in cf_cols) or "FALSE"
    return f"""
SELECT m.S_CUSTOMER_ID, m.PMID, cf.YEAR_VAL AS YEAR_VAL,
       ({cf_sum}) AS CF_TREFFER
FROM {pubmed_mapping} m
JOIN {pubmed_cf_flag} cf ON cf.PMID = m.PMID
WHERE m.MERGE_RESULT > 1
  AND ({cf_any})
  AND cf.YEAR_VAL >= {cutoff}
""".strip()


def build_anchor_year_query(pubmed_cf_flag: str) -> str:
    return f"SELECT MAX(YEAR_VAL) AS ANCHOR FROM {pubmed_cf_flag}"


def build_pubmed_history_query(pubmed_mapping: str, pubmed_cf_flag: str,
                               cf_cols: list, history_years: int, anchor_year: int) -> str:
    cutoff = anchor_year - history_years
    cf_any = " OR ".join(f"cf.{c} > 0" for c in cf_cols) or "FALSE"
    return f"""
SELECT m.S_CUSTOMER_ID, cf.YEAR_VAL AS YEAR_VAL, COUNT(*) AS N
FROM {pubmed_mapping} m
JOIN {pubmed_cf_flag} cf ON cf.PMID = m.PMID
WHERE m.MERGE_RESULT > 1
  AND ({cf_any})
  AND cf.YEAR_VAL >= {cutoff}
GROUP BY m.S_CUSTOMER_ID, cf.YEAR_VAL
""".strip()


def build_pub_history_map(history_rows: list) -> dict:
    def _g(row, k):
        v = row.get(k)
        return v if v is not None else row.get(k.lower())
    out = {}
    for r in history_rows:
        cid = str(_g(r, "S_CUSTOMER_ID") or "")
        yr = str(_g(r, "YEAR_VAL") or "")
        n = int(_g(r, "N") or 0)
        if not cid or not yr:
            continue
        out.setdefault(cid, {})[yr] = n
    return out


def apply_pub_history(hcps: list, history_map: dict) -> list:
    """Override each HCP's display-only pub_by_year with the 20-year history counts.
    Scoring (candidate_score / pubmed_articles) is untouched."""
    for h in hcps:
        h["pub_by_year"] = history_map.get(str(h.get("s_customer_id", "")), {})
    return hcps


def build_hcp_meta_query(customer_source: str, rating_result_final: str) -> str:
    return f"""
SELECT cs.S_CUSTOMER_ID, cs.S_FIRSTNAME, cs.S_LASTNAME,
       cs.S_CITY, cs.S_HCP_GROUP, r.RATING
FROM {customer_source} cs
JOIN {rating_result_final} r ON cs.S_CUSTOMER_ID = r.S_CUSTOMER_ID
WHERE r.RATING IN ('A','B','C','D')
""".strip()


def matches_keywords(keyword_blob: str, term_texts: list) -> bool:
    blob = (keyword_blob or "").lower()
    tokens = set(re.findall(r"[a-z0-9\-]+", blob))
    for t in term_texts:
        t = t.lower().strip()
        if not t:
            continue
        if " " in t or "-" in t:
            if t in blob:
                return True
        elif t in tokens:
            return True
    return False


def normalise_meta_row(row: dict) -> dict:
    def _g(k):
        v = row.get(k)
        return v if v is not None else row.get(k.lower())
    first = str(_g("S_FIRSTNAME") or "").strip()
    last  = str(_g("S_LASTNAME") or "").strip()
    return {
        "s_customer_id": str(_g("S_CUSTOMER_ID") or ""),
        "firstname": first, "lastname": last,
        "name": " ".join(p for p in [first, last] if p),
        "city": str(_g("S_CITY") or ""),
        "specialty": str(_g("S_HCP_GROUP") or ""),
        "rating": str(_g("RATING") or ""),
    }


def aggregate_candidates(web_rows: list, pubmed_rows: list, meta_map: dict, term_texts: list) -> dict:
    def _g(row, k):
        v = row.get(k)
        return v if v is not None else row.get(k.lower())
    acc = {}
    for row in web_rows:
        cid = str(_g(row, "S_CUSTOMER_ID") or "")
        blob = f"{_g(row,'COL_KEYWORDS_ORIG') or ''} {_g(row,'COL_KEYWORDS_EN') or ''}"
        if not matches_keywords(blob, term_texts):
            continue
        h = acc.setdefault(cid, {"web_website_ids": [], "pubmed_articles": [], "pub_by_year": {}})
        h["web_website_ids"].append(str(_g(row, "WEBSITE_ID") or ""))
    for row in pubmed_rows:
        cid = str(_g(row, "S_CUSTOMER_ID") or "")
        h = acc.setdefault(cid, {"web_website_ids": [], "pubmed_articles": [], "pub_by_year": {}})
        yr = str(_g(row, "YEAR_VAL") or "")
        h["pubmed_articles"].append({"pmid": str(_g(row, "PMID") or ""), "year": yr})
        h["pubmed_cf_treffer"] = h.get("pubmed_cf_treffer", 0) + int(_g(row, "CF_TREFFER") or 0)
        if yr:
            h["pub_by_year"][yr] = h["pub_by_year"].get(yr, 0) + 1

    result = {}
    for cid, h in acc.items():
        if cid not in meta_map:
            continue
        web_n = len(h["web_website_ids"])
        pub_n = len(h["pubmed_articles"])
        if web_n == 0 and pub_n == 0:
            continue
        result[cid] = {
            **meta_map[cid],
            "web_candidate_count": web_n, "web_website_ids": h["web_website_ids"],
            "pubmed_candidate_count": pub_n, "pubmed_articles": h["pubmed_articles"],
            "pubmed_cf_treffer": h.get("pubmed_cf_treffer", 0),
            "pub_by_year": h["pub_by_year"],
            "candidate_score": web_n + pub_n,
        }
    return result


_RATING_RANK = {"A": 3, "B": 2, "C": 1, "D": 0, "": -1}

def shortlist(hcps: list, top_n: int) -> list:
    ordered = sorted(
        hcps,
        key=lambda h: (h.get("candidate_score", 0), h.get("pubmed_cf_treffer", 0),
                       _RATING_RANK.get(h.get("rating", ""), -1)),
        reverse=True,
    )
    for i, h in enumerate(ordered):
        h["shortlisted"] = i < top_n
    return ordered


def main():
    import argparse, snowflake.connector
    from pipeline_common import connect_snowflake, resolve_tables
    p = argparse.ArgumentParser(); p.add_argument("--force", action="store_true")
    args = p.parse_args()

    cfg = configparser.ConfigParser(); cfg.read(os.path.join(_DIR, "config.ini"))
    sf, fn, tm = cfg["snowflake"], cfg["funnel"], cfg["terms"]
    tb = resolve_tables(sf)

    out_path = os.path.join(_DIR, "data", "shortlist.json")
    if os.path.exists(out_path) and not args.force:
        log.info("shortlist.json exists — skipping (use --force)"); return

    with open(os.path.join(_DIR, "data", "input.json"), encoding="utf-8") as f:
        inp = json.load(f)

    conn = connect_snowflake(sf["aws_profile"], sf["warehouse"], sf["database"])
    cur = conn.cursor(snowflake.connector.DictCursor)

    log.info("Q1: PCA terms...")
    cur.execute(build_pca_terms_query(tb["content_frame_spec"], tm.getboolean("use_pca_only")))
    pca_terms = [{"term_key": r["TERM_KEY"], "term_en": r["TERM_EN"]} for r in cur.fetchall()]
    term_texts = [t["term_en"] for t in pca_terms if t["term_en"]]
    cf_cols = [t["term_key"] for t in pca_terms]

    log.info("Q2: web candidates...")
    cur.execute(build_web_candidates_query(tb["llm_validation"],
                term_ilike_predicate(term_texts), int(fn["in_relation_min"])))
    web_rows = cur.fetchall()

    log.info("Q1b: anchor year (max YEAR_VAL in PubMed CF table)...")
    cur.execute(build_anchor_year_query(tb["pubmed_cf_flag"]))
    _arow = cur.fetchone()
    anchor_year = int((_arow.get("ANCHOR") or _arow.get("anchor")) or datetime.now().year) \
        if _arow else datetime.now().year
    log.info(f"anchor_year = {anchor_year}")

    log.info("Q3: pubmed candidates (5y scoring window)...")
    cur.execute(build_pubmed_candidates_query(tb["pubmed_mapping"], tb["pubmed_cf_flag"],
                cf_cols, int(fn["pubmed_window_years"]), anchor_year))
    pubmed_rows = cur.fetchall()

    log.info("Q3b: pubmed 20y history (display only)...")
    cur.execute(build_pubmed_history_query(tb["pubmed_mapping"], tb["pubmed_cf_flag"],
                cf_cols, int(fn["pub_history_years"]), anchor_year))
    history_rows = cur.fetchall()

    log.info("Q4: HCP metadata...")
    cur.execute(build_hcp_meta_query(tb["customer_source"], tb["rating_result_final"]))
    meta_map = {str(r["S_CUSTOMER_ID"]): normalise_meta_row(r) for r in cur.fetchall()}

    cur.close(); conn.close()

    hcps = list(aggregate_candidates(web_rows, pubmed_rows, meta_map, term_texts).values())
    hcps = apply_pub_history(hcps, build_pub_history_map(history_rows))
    hcps = shortlist(hcps, int(fn["top_n_candidates"]))
    n_short = sum(h["shortlisted"] for h in hcps)
    log.info(f"{len(hcps)} candidate HCPs; {n_short} shortlisted")
    for h in [x for x in hcps if x["shortlisted"]]:
        log.info(f"  {h['name']:<30} score={h['candidate_score']} "
                 f"(web={h['web_candidate_count']}, pubmed={h['pubmed_candidate_count']})")

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"indication": inp["indication"], "client_drug": inp["client_drug"],
                   "generated_at": datetime.now().isoformat(timespec="seconds"),
                   "anchor_year": anchor_year, "pub_history_years": int(fn["pub_history_years"]),
                   "pca_terms": pca_terms, "hcps": hcps}, f, ensure_ascii=False, indent=2)
    log.info(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
