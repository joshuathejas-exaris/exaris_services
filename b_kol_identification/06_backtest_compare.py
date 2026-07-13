"""Stage 06: diff two as_of_year runs — rising-star→KOL, tier moves, new KOLs.
Usage: python 06_backtest_compare.py --earlier data/kol_final_2021.json --later data/kol_final_latest.json
"""
import argparse, json, logging, os

logging.basicConfig(format="%(asctime)s [%(levelname)s] %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)
_DIR = os.path.dirname(__file__)


def compare_runs(earlier: dict, later: dict) -> dict:
    e = {h["s_customer_id"]: h for h in earlier.get("hcps", [])}
    l = {h["s_customer_id"]: h for h in later.get("hcps", [])}
    rising_to_kol, tier_moves, new_kols = [], [], []
    for cid, lh in l.items():
        eh = e.get(cid)
        if eh is None:
            new_kols.append({"s_customer_id": cid, "name": lh["name"], "to_tier": lh["tier"]})
            continue
        if eh["tier"] != lh["tier"]:
            move = {"s_customer_id": cid, "name": lh["name"],
                    "from_tier": eh["tier"], "to_tier": lh["tier"]}
            tier_moves.append(move)
            if eh.get("rising_star") and lh["tier"] in ("A", "B"):
                rising_to_kol.append(move)
    return {"rising_to_kol": rising_to_kol, "tier_moves": tier_moves, "new_kols": new_kols}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--earlier", required=True); p.add_argument("--later", required=True)
    args = p.parse_args()
    with open(args.earlier, encoding="utf-8") as f: earlier = json.load(f)
    with open(args.later, encoding="utf-8") as f: later = json.load(f)
    result = compare_runs(earlier, later)
    log.info(f"{earlier.get('anchor_year')} → {later.get('anchor_year')}")
    log.info(f"  rising→KOL: {len(result['rising_to_kol'])}, tier moves: {len(result['tier_moves'])}, "
             f"new KOLs: {len(result['new_kols'])}")
    for r in result["rising_to_kol"]:
        log.info(f"    ★ {r['name']}: {r['from_tier']} → {r['to_tier']}")
    out_path = os.path.join(_DIR, "data", "backtest_compare.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    log.info(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
