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
SELECT m.S_CUSTOMER_ID, m.PMID, cf.YEAR AS YEAR_VAL,
       ({cf_sum}) AS CF_TREFFER
FROM {pubmed_mapping} m
JOIN {pubmed_cf_flag} cf ON cf.PMID = m.PMID
WHERE m.MERGE_RESULT > 1
  AND ({cf_any})
  AND cf.YEAR >= {cutoff}
""".strip()


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
