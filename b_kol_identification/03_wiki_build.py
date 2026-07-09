"""
Stage 03: LLM wiki-build — ingest -> ground -> verify -> map, per source.
A source counts as relevant iff it yields >=1 grounded, verified claim.
Reads:  data/sources.json
Writes: data/wiki.json  (+ wiki/<ts>/ tree)  (resume-safe)
"""
import configparser, json, logging, os, re, sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

logging.basicConfig(format="%(asctime)s [%(levelname)s] %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)
_DIR = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(_DIR, ".."))
from pipeline_common import call_bedrock_json, make_bedrock_client, name_matches  # noqa: E402


def _norm_ws(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip().casefold()


def quote_grounded(quote: str, text: str) -> bool:
    q = _norm_ws(quote)
    return bool(q) and q in _norm_ws(text)


def build_ingest_prompt(kind: str, indication: str, term_list: list, hcp_name: str, text: str) -> str:
    terms = ", ".join(term_list)
    if kind == "pubmed":
        role = (f"This is a PubMed article authored by {hcp_name}. Decide whether the ARTICLE "
                f"genuinely concerns the indication '{indication}' (topics: {terms}) — i.e. the "
                f"author is actively contributing to this indication's science.")
    else:
        role = (f"Decide whether the named HCP {hcp_name} is ACTIVELY engaging with / sharing a view "
                f"on the indication '{indication}' (topics: {terms}) in this document — not merely "
                f"named on the page. Ignore financial-disclosure / conflict-of-interest text.")
    return f"""{role}

Return ONLY JSON:
{{"claims":[{{"verbatim_quote":"<exact span copied from the text>",
  "statement":"<one line: how the HCP engages with the indication>",
  "sentiment":"positive|neutral|negative|ambivalent",
  "themes":["<which of: {terms}>"],
  "mentioned_hcps":["<other doctor names in the text>"],
  "confidence":"high|medium|low"}}]}}
If there is no genuine engagement, return {{"claims":[]}}.

TEXT:
{text}
"""


def build_verify_prompt(indication: str, hcp_name: str, quote: str, text: str) -> str:
    return f"""A previous pass claims {hcp_name} genuinely engages with '{indication}',
supported by this quote:
"{quote}"

Is that TRUE given the source below? Answer ONLY {{"verified": true}} or {{"verified": false}}.
Be strict: false if the quote is absent, or does not show {hcp_name} engaging with '{indication}'.

SOURCE:
{text}
"""


def normalise_claim(raw: dict) -> dict:
    def _list(v):
        if v is None:
            return []
        return v if isinstance(v, list) else [v]
    return {
        "verbatim_quote": str(raw.get("verbatim_quote") or ""),
        "statement": str(raw.get("statement") or ""),
        "sentiment": str(raw.get("sentiment") or "neutral"),
        "themes": [str(t) for t in _list(raw.get("themes"))],
        "mentioned_hcps": [str(n) for n in _list(raw.get("mentioned_hcps"))],
        "confidence": str(raw.get("confidence") or "medium"),
    }


def resolve_mentions(names: list, roster: list) -> list:
    out = []
    for nm in names:
        sid = ""
        for r in roster:
            if name_matches(nm, r.get("firstname", ""), r.get("lastname", "")):
                sid = r.get("s_customer_id", ""); break
        out.append({"name": nm, "s_customer_id": sid})
    return out


def source_is_relevant(claims: list) -> bool:
    return any(c.get("verified") for c in claims)


def process_source(source, hcp, indication, term_list, bedrock, cfg):
    text = source["full_text"]
    ingest = call_bedrock_json(bedrock, cfg["ingest_model_id"],
                               build_ingest_prompt(source["kind"], indication, term_list, hcp["name"], text),
                               temperature=0.0, max_tokens=int(cfg["extraction_max_tokens"]))
    raw_claims = (ingest or {}).get("claims", []) or []
    kept = []
    for rc in raw_claims:
        c = normalise_claim(rc)
        if not quote_grounded(c["verbatim_quote"], text):
            continue  # dropped: grounding — before spending a verify call
        vr = call_bedrock_json(bedrock, cfg["verify_model_id"],
                               build_verify_prompt(indication, hcp["name"], c["verbatim_quote"], text),
                               temperature=0.0, max_tokens=256)
        c["verified"] = bool((vr or {}).get("verified"))
        c["source_id"] = source["source_id"]; c["kind"] = source["kind"]; c["url"] = source.get("url", "")
        if source["kind"] == "pubmed":
            c["pmid"] = source.get("pmid", ""); c["year"] = source.get("year", "")
        kept.append(c)
    if not source_is_relevant(kept):
        return None
    return {"claims": kept}


def main():
    import argparse
    p = argparse.ArgumentParser(); p.add_argument("--force", action="store_true")
    args = p.parse_args()
    cfg_ini = configparser.ConfigParser(); cfg_ini.read(os.path.join(_DIR, "config.ini"))
    bc = cfg_ini["bedrock"]
    cfg = {"ingest_model_id": bc["ingest_model_id"], "verify_model_id": bc["verify_model_id"],
           "extraction_max_tokens": bc["extraction_max_tokens"]}

    out_path = os.path.join(_DIR, "data", "wiki.json")
    if os.path.exists(out_path) and not args.force:
        log.info("wiki.json exists — skipping (use --force)"); return

    with open(os.path.join(_DIR, "data", "sources.json"), encoding="utf-8") as f:
        data = json.load(f)
    indication = data["indication"]
    term_list = [t["term_en"] for t in data["pca_terms"] if t["term_en"]]
    roster = [{"s_customer_id": h["s_customer_id"],
               "firstname": h["name"].split(" ")[0] if h["name"] else "",
               "lastname": h["name"].split(" ")[-1] if h["name"] else ""} for h in data["hcps"]]

    bedrock = make_bedrock_client(bc["aws_profile"])
    out_hcps = []
    for h in data["hcps"]:
        all_sources = h.get("web_sources", []) + h.get("pubmed_sources", [])
        results = []
        with ThreadPoolExecutor(max_workers=int(bc["ingest_max_workers"])) as ex:
            futures = [ex.submit(process_source, s, h, indication, term_list, bedrock, cfg)
                       for s in all_sources]
            for fut in futures:
                r = fut.result()
                if r:
                    results.append(r)
        claims = [c for r in results for c in r["claims"] if c.get("verified")]
        web_ids = {c["source_id"] for c in claims if c["kind"] == "web"}
        pmids = {c["source_id"] for c in claims if c["kind"] == "pubmed"}
        years = {}
        for c in claims:
            if c["kind"] == "pubmed" and c.get("year"):
                years[c["year"]] = years.get(c["year"], 0) + 1
        # dedup mentioned hcps -> comention names
        mentioned = []
        for c in claims:
            mentioned += resolve_mentions(c.get("mentioned_hcps", []), roster)
        out_hcps.append({
            "s_customer_id": h["s_customer_id"], "name": h["name"], "city": h["city"],
            "specialty": h["specialty"], "rating": h["rating"], "pub_by_year": h.get("pub_by_year", {}),
            "verified_web_count": len(web_ids), "verified_pubmed_count": len(pmids),
            "verified_pubmed_years": years, "verified_pmids": sorted(pmids),
            "claims": claims, "mentioned": mentioned,
        })
        log.info(f"  {h['name']:<30} web={len(web_ids)} pubmed={len(pmids)}")

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"indication": indication, "client_drug": data["client_drug"],
                   "generated_at": datetime.now().isoformat(timespec="seconds"),
                   "pca_terms": data["pca_terms"], "hcps": out_hcps}, f, ensure_ascii=False, indent=2)
    log.info(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
