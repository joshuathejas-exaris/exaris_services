"""
Stage 02: Retrieve full source text for shortlisted HCPs (web + PubMed).
Reads:  data/shortlist.json
Writes: data/sources.json  (resume-safe)
"""
import configparser, json, logging, os, sys
from datetime import datetime

logging.basicConfig(format="%(asctime)s [%(levelname)s] %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)
_DIR = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(_DIR, ".."))


def _in_list(ids: list) -> str:
    return ", ".join("'" + str(i).replace("'", "''") + "'" for i in ids)


def build_web_content_query(llm_validation: str, website_ids: list, s_customer_id) -> str:
    escaped_id = str(s_customer_id).replace("'", "''")
    return (f"SELECT WEBSITE_ID, URL, CONTENT FROM {llm_validation} "
            f"WHERE WEBSITE_ID IN ({_in_list(website_ids)}) "
            f"AND S_CUSTOMER_ID = '{escaped_id}'")


def build_pubmed_article_query(pubmed_article: str, pmids: list) -> str:
    return (f"SELECT PMID, TITLE, ABSTRACT, YEAR_VAL, JOURNAL_NAME FROM {pubmed_article} "
            f"WHERE PMID IN ({_in_list(pmids)})")


def _g(row, k):
    v = row.get(k)
    return v if v is not None else row.get(k.lower())


def assemble_web_sources(rows: list, max_chars: int) -> list:
    out = []
    for r in rows:
        out.append({"source_id": str(_g(r, "WEBSITE_ID") or ""), "kind": "web",
                    "url": str(_g(r, "URL") or ""),
                    "full_text": str(_g(r, "CONTENT") or "")[:max_chars]})
    return out


def assemble_pubmed_sources(rows: list, max_chars: int) -> list:
    out = []
    for r in rows:
        pmid = str(_g(r, "PMID") or "")
        title = str(_g(r, "TITLE") or ""); abstract = str(_g(r, "ABSTRACT") or "")
        out.append({"source_id": pmid, "kind": "pubmed", "pmid": pmid,
                    "year": str(_g(r, "YEAR_VAL") or ""),
                    "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                    "full_text": (title + "\n\n" + abstract).strip()[:max_chars]})
    return out


def cap_sources(sources: list, max_n: int) -> list:
    def _year(s):
        try:
            return int(s.get("year") or 0)
        except (TypeError, ValueError):
            return 0
    return sorted(sources, key=_year, reverse=True)[:max_n]


def main():
    import argparse, snowflake.connector
    from pipeline_common import connect_snowflake
    p = argparse.ArgumentParser(); p.add_argument("--force", action="store_true")
    args = p.parse_args()
    cfg = configparser.ConfigParser(); cfg.read(os.path.join(_DIR, "config.ini"))
    sf, tb, fn = cfg["snowflake"], cfg["tables"], cfg["funnel"]
    max_chars = int(fn["max_source_chars"]); max_n = int(fn["max_sources_per_hcp"])

    out_path = os.path.join(_DIR, "data", "sources.json")
    if os.path.exists(out_path) and not args.force:
        log.info("sources.json exists — skipping (use --force)"); return

    with open(os.path.join(_DIR, "data", "shortlist.json"), encoding="utf-8") as f:
        sl = json.load(f)
    shortlisted = [h for h in sl["hcps"] if h.get("shortlisted")]
    log.info(f"Fetching sources for {len(shortlisted)} shortlisted HCPs")

    conn = connect_snowflake(sf["aws_profile"], sf["warehouse"], sf["database"])
    cur = conn.cursor(snowflake.connector.DictCursor)
    out_hcps = []
    for h in shortlisted:
        web_sources, pubmed_sources = [], []
        if h.get("web_website_ids"):
            cur.execute(build_web_content_query(tb["llm_validation"], h["web_website_ids"], h["s_customer_id"]))
            web_sources = assemble_web_sources(cur.fetchall(), max_chars)
        pmids = [a["pmid"] for a in h.get("pubmed_articles", [])]
        if pmids:
            cur.execute(build_pubmed_article_query(tb["pubmed_article"], pmids))
            pubmed_sources = assemble_pubmed_sources(cur.fetchall(), max_chars)
        out_hcps.append({
            "s_customer_id": h["s_customer_id"], "name": h["name"], "city": h["city"],
            "specialty": h["specialty"], "rating": h["rating"], "pub_by_year": h.get("pub_by_year", {}),
            "web_sources": cap_sources(web_sources, max_n),
            "pubmed_sources": cap_sources(pubmed_sources, max_n),
        })
    cur.close(); conn.close()

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"indication": sl["indication"], "client_drug": sl["client_drug"],
                   "generated_at": datetime.now().isoformat(timespec="seconds"),
                   "pca_terms": sl["pca_terms"], "hcps": out_hcps}, f, ensure_ascii=False, indent=2)
    log.info(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
