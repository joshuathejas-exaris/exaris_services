"""
Stage 04: Assemble the KOL ranking — score, tiers, rising stars, themes, network.
Reads:  data/wiki.json
Writes: data/kol_final.json  (resume-safe)
"""
import configparser, json, logging, os, sys
from datetime import datetime

logging.basicConfig(format="%(asctime)s [%(levelname)s] %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)
_DIR = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(_DIR, ".."))


def score_hcps(hcps: list) -> list:
    out = []
    for h in hcps:
        score = int(h.get("verified_web_count", 0)) + int(h.get("verified_pubmed_count", 0))
        years = [int(y) for y in h.get("verified_pubmed_years", {}).keys() if str(y).isdigit()]
        out.append({**h, "kol_score": score, "latest_year": max(years) if years else 0})
    out.sort(key=lambda h: (h["kol_score"], h["latest_year"]), reverse=True)
    return out


def assign_tiers(hcps: list, tier_a_pct: float, tier_b_pct: float) -> list:
    if not hcps:
        return []
    scores = sorted(h["kol_score"] for h in hcps)
    n = len(scores)
    thresh_a = scores[min(int(n * tier_a_pct / 100), n - 1)]
    thresh_b = scores[min(int(n * tier_b_pct / 100), n - 1)]
    return [{**h, "tier": ("A" if h["kol_score"] >= thresh_a
                           else "B" if h["kol_score"] >= thresh_b else "C")} for h in hcps]


def flag_rising_stars(hcps: list, min_pubs: int, growth: float) -> list:
    out = []
    for h in hcps:
        years = {int(y): int(c) for y, c in h.get("verified_pubmed_years", {}).items() if str(y).isdigit()}
        cur = max(years) if years else datetime.now().year
        recent = sum(c for y, c in years.items() if y >= cur - 1)
        prior = sum(c for y, c in years.items() if y < cur - 1)
        new_voice = recent >= min_pubs and prior == 0
        accel = (recent / max(prior, 1)) >= growth and recent >= min_pubs
        out.append({**h, "rising_star": bool(new_voice or accel)})
    return out


def aggregate_themes(hcp: dict, pca_terms: list, top_n: int = 5) -> list:
    # 03_wiki_build.py builds the ingest prompt from term_en labels, so claims' "themes"
    # come back as term_en STRINGS (e.g. "Obesity"), not term_key codes. Resolve each
    # string back to its term_key so it matches pca_terms[].term_key downstream (the
    # report's heatmap keys off term_key). Falls through unchanged if a future prompt
    # ever emits term_key codes directly.
    en_to_key = {t["term_en"]: t["term_key"] for t in pca_terms}
    key_to_en = {t["term_key"]: t["term_en"] for t in pca_terms}
    counts = {}
    fallback_en = {}
    for c in hcp.get("claims", []):
        for s in c.get("themes", []):
            k = en_to_key.get(s, s)
            counts[k] = counts.get(k, 0) + 1
            fallback_en.setdefault(k, s)
    ranked = sorted(({"term_key": k, "term_en": key_to_en.get(k, fallback_en.get(k, k)), "count": v}
                     for k, v in counts.items()), key=lambda x: x["count"], reverse=True)
    return ranked[:top_n]


def drop_zero_score(hcps: list) -> list:
    return [h for h in hcps if h.get("kol_score", 0) > 0]


def top_quotes(hcp: dict, n: int = 3) -> list:
    order = {"high": 0, "medium": 1, "low": 2}
    cs = sorted([c for c in hcp.get("claims", []) if c.get("verified")],
                key=lambda c: order.get(c.get("confidence", "medium"), 1))
    return [{"quote": c["verbatim_quote"], "url": c.get("url", ""), "sentiment": c.get("sentiment", "neutral")}
            for c in cs[:n]]


def _in_list(ids):
    return ", ".join("'" + str(i).replace("'", "''") + "'" for i in ids)


def build_coauthor_query(pubmed_author: str, pmids: list) -> str:
    return (f"SELECT PMID, ORCID, FIRSTNAME, LASTNAME, AFFILIATION FROM {pubmed_author} "
            f"WHERE PMID IN ({_in_list(pmids)})")


def _g(row, k):
    v = row.get(k)
    return v if v is not None else row.get(k.lower())


def build_coauthor_edges(author_rows: list, verified_by_pmid: dict, roster: list) -> list:
    from pipeline_common import name_matches
    by_pmid = {}
    for r in author_rows:
        by_pmid.setdefault(str(_g(r, "PMID") or ""), []).append(
            f"{_g(r,'FIRSTNAME') or ''} {_g(r,'LASTNAME') or ''}".strip())
    rmap = {rr["s_customer_id"]: rr for rr in roster}
    edge_counts = {}
    for pmid, our_ids in verified_by_pmid.items():
        authors = by_pmid.get(pmid, [])
        for aid in our_ids:
            a = rmap.get(aid)
            if not a:
                continue
            for author_name in authors:
                # skip the author that is this HCP
                if name_matches(author_name, a.get("firstname", ""), a.get("lastname", "")):
                    continue
                # is this author another of our KOLs?
                match = next((rr for rr in roster
                              if name_matches(author_name, rr.get("firstname", ""), rr.get("lastname", ""))), None)
                if match:
                    # canonical order: count each unordered pair's shared pmid once
                    if aid >= match["s_customer_id"]:
                        continue
                    key = tuple(sorted([aid, match["s_customer_id"]])) + (False,)
                    edge_counts[key] = edge_counts.get(key, {"a_name": a["name"], "b_name": match["name"]})
                    edge_counts[key]["n"] = edge_counts[key].get("n", 0) + 1
                else:
                    key = (aid, author_name, True)
                    edge_counts[key] = edge_counts.get(key, {"a_name": a["name"], "b_name": author_name})
                    edge_counts[key]["n"] = edge_counts[key].get("n", 0) + 1
    edges = []
    for key, v in edge_counts.items():
        if key[-1]:  # external
            edges.append({"hcp_a": key[0], "hcp_b": key[1], "shared_pmids": v["n"],
                          "a_name": v["a_name"], "b_name": v["b_name"], "b_external": True})
        else:
            edges.append({"hcp_a": key[0], "hcp_b": key[1], "shared_pmids": v["n"],
                          "a_name": v["a_name"], "b_name": v["b_name"], "b_external": False})
    return edges


def compute_reach(verified_pmids: list, authors_by_pmid: dict, hcp_first: str, hcp_last: str) -> dict:
    """Distinct co-authors (dedup by ORCID, fallback normalized name; self excluded)
    and distinct affiliations across the HCP's verified-relevant PubMed articles."""
    from pipeline_common import name_matches, normalize_name
    coauthors, affiliations = set(), set()
    for pmid in verified_pmids:
        for a in authors_by_pmid.get(str(pmid), []):
            fn = str(_g(a, "FIRSTNAME") or ""); ln = str(_g(a, "LASTNAME") or "")
            if name_matches(f"{fn} {ln}", hcp_first, hcp_last):
                continue  # the HCP themselves
            orcid = str(_g(a, "ORCID") or "").strip()
            key = orcid or normalize_name(f"{fn} {ln}")
            if key:
                coauthors.add(key)
            aff = normalize_name(str(_g(a, "AFFILIATION") or ""))
            if aff:
                affiliations.add(aff)
    return {"distinct_coauthors": len(coauthors), "distinct_affiliations": len(affiliations)}


def compute_ratio(verified_web: int, verified_pubmed: int,
                  total_web: int, total_pubmed: int, min_denominator: int) -> dict:
    """verified-relevant / all-sources (topic-agnostic). Neutral below min_denominator."""
    numerator = int(verified_web) + int(verified_pubmed)
    denominator = int(total_web) + int(total_pubmed)
    if denominator < int(min_denominator) or denominator == 0:
        return {"ratio": 0.0, "denominator": denominator, "neutral": True}
    return {"ratio": min(numerator / denominator, 1.0), "denominator": denominator, "neutral": False}


def build_comention_edges(hcps: list) -> list:
    edges = []
    for h in hcps:
        counts = {}
        for m in h.get("mentioned", []):
            if m.get("s_customer_id"):
                counts[(m["s_customer_id"], m["name"])] = counts.get((m["s_customer_id"], m["name"]), 0) + 1
        for (to_id, to_name), c in counts.items():
            if to_id == h["s_customer_id"]:
                continue
            edges.append({"from": h["s_customer_id"], "to": to_id,
                          "from_name": h["name"], "to_name": to_name, "count": c})
    return edges


def main():
    import argparse, snowflake.connector
    from pipeline_common import connect_snowflake, resolve_tables
    p = argparse.ArgumentParser(); p.add_argument("--force", action="store_true")
    args = p.parse_args()
    cfg = configparser.ConfigParser(); cfg.read(os.path.join(_DIR, "config.ini"))
    sf, sc = cfg["snowflake"], cfg["scoring"]
    tb = resolve_tables(sf)

    out_path = os.path.join(_DIR, "data", "kol_final.json")
    if os.path.exists(out_path) and not args.force:
        log.info("kol_final.json exists — skipping (use --force)"); return

    with open(os.path.join(_DIR, "data", "wiki.json"), encoding="utf-8") as f:
        data = json.load(f)
    pca_terms = data["pca_terms"]

    hcps = score_hcps(data["hcps"])
    hcps = drop_zero_score(hcps)
    hcps = assign_tiers(hcps, float(sc["tier_a_percentile"]), float(sc["tier_b_percentile"]))
    hcps = flag_rising_stars(hcps, int(sc["rising_star_min_pubs"]), float(sc["rising_star_growth"]))
    for h in hcps:
        h["theme_labels"] = aggregate_themes(h, pca_terms)
        h["top_quotes"] = top_quotes(h)

    roster = [{"s_customer_id": h["s_customer_id"], "name": h["name"],
               "firstname": h["name"].split(" ")[0] if h["name"] else "",
               "lastname": h["name"].split(" ")[-1] if h["name"] else ""} for h in hcps]

    # collaboration network from verified PubMed
    verified_by_pmid = {}
    for h in hcps:
        for pmid in h.get("verified_pmids", []):
            verified_by_pmid.setdefault(pmid, []).append(h["s_customer_id"])
    all_pmids = list(verified_by_pmid.keys())
    author_rows = []
    if all_pmids:
        conn = connect_snowflake(sf["aws_profile"], sf["warehouse"], sf["database"])
        cur = conn.cursor(snowflake.connector.DictCursor)
        cur.execute(build_coauthor_query(tb["pubmed_author"], all_pmids))
        author_rows = cur.fetchall(); cur.close(); conn.close()
    coauthor_edges = build_coauthor_edges(author_rows, verified_by_pmid, roster) if all_pmids else []
    comention_edges = build_comention_edges(hcps)

    # reach + ratio features (Task 5) — authors_by_pmid built from the same
    # author_rows fetched above for the collaboration network
    authors_by_pmid = {}
    for r in author_rows:
        authors_by_pmid.setdefault(str(_g(r, "PMID") or ""), []).append(r)
    min_denom = int(sc["min_ratio_denominator"])
    for h in hcps:
        first = h["name"].split(" ")[0] if h["name"] else ""
        last = h["name"].split(" ")[-1] if h["name"] else ""
        h["reach"] = compute_reach(h.get("verified_pmids", []), authors_by_pmid, first, last)
        h["ratio"] = compute_ratio(h.get("verified_web_count", 0), h.get("verified_pubmed_count", 0),
                                   h.get("total_web_sources", 0), h.get("total_pubmed_sources", 0), min_denom)

    # strip bulky per-claim payload from the final file (keep top_quotes + counts)
    for h in hcps:
        h.pop("claims", None); h.pop("mentioned", None)

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"indication": data["indication"], "client_drug": data["client_drug"],
                   "generated_at": datetime.now().isoformat(timespec="seconds"),
                   "anchor_year": data.get("anchor_year"), "pub_history_years": data.get("pub_history_years"),
                   "pca_terms": pca_terms, "hcps": hcps,
                   "coauthor_edges": coauthor_edges, "comention_edges": comention_edges},
                  f, ensure_ascii=False, indent=2)
    log.info(f"Wrote {out_path} — {len(hcps)} KOLs")


if __name__ == "__main__":
    main()
