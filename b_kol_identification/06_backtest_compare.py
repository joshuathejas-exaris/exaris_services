"""Stage 06: diff two as_of_year runs — rising-star→KOL, tier moves, new KOLs.

Writes both a machine-readable JSON (data/backtest_compare.json) and a detailed,
self-contained HTML report (results/backtest_report_<ts>.html) so the changes
between the two runs can be read and traced by a human.

Usage: python 06_backtest_compare.py --earlier data/kol_final_2021.json --later data/kol_final_latest.json
"""
import argparse, html as _html, json, logging, os
from datetime import datetime

logging.basicConfig(format="%(asctime)s [%(levelname)s] %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)
_DIR = os.path.dirname(__file__)

TIER_RANK = {"A": 3, "B": 2, "C": 1}

PALETTE = {
    "ink": "#1b2430", "muted": "#5c6774", "line": "#e2e7ee", "bg": "#f4f6f8", "card": "#fff",
    "accent": "#2f4a7c", "teal": "#0d7d74", "violet": "#6d5ac0", "amber": "#b7791f",
    "emerald": "#1f8a5b", "red": "#b4432f",
    "tierA": "#1f8a5b", "tierB": "#3b5b92", "tierC": "#6b7684",
}


def compare_runs(earlier: dict, later: dict) -> dict:
    """Original, stable comparison used by tooling/tests — do not change its shape."""
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


# --------------------------------------------------------------------------- #
#  Rich comparison (for the HTML report)
# --------------------------------------------------------------------------- #
def _rank_map(hcps: list) -> dict:
    """Rank each HCP by kol_score descending (1 = top)."""
    ordered = sorted(hcps, key=lambda h: h.get("kol_score", 0) or 0, reverse=True)
    return {h["s_customer_id"]: i + 1 for i, h in enumerate(ordered)}


def build_rows(earlier: dict, later: dict) -> list:
    """One row per HCP present in either run, with the full before/after picture."""
    e = {h["s_customer_id"]: h for h in earlier.get("hcps", [])}
    l = {h["s_customer_id"]: h for h in later.get("hcps", [])}
    er, lr = _rank_map(earlier.get("hcps", [])), _rank_map(later.get("hcps", []))
    ordered_ids = list(e.keys()) + [c for c in l if c not in e]
    rows = []
    for cid in ordered_ids:
        eh, lh = e.get(cid), l.get(cid)
        base = eh or lh
        et = eh.get("tier") if eh else None
        lt = lh.get("tier") if lh else None
        es = eh.get("kol_score") if eh else None
        ls = lh.get("kol_score") if lh else None
        status = "present" if (eh and lh) else ("new" if lh else "dropped")
        tier_dir = (TIER_RANK.get(lt, 0) - TIER_RANK.get(et, 0)) if (et and lt) else 0
        rows.append({
            "s_customer_id": cid,
            "name": base.get("name", ""),
            "specialty": base.get("specialty", ""),
            "city": base.get("city", ""),
            "status": status,
            "tier_from": et, "tier_to": lt, "tier_dir": tier_dir,
            "score_from": es, "score_to": ls,
            "score_delta": (ls - es) if (es is not None and ls is not None) else None,
            "rank_from": er.get(cid), "rank_to": lr.get(cid),
            "rising_from": bool(eh.get("rising_star")) if eh else False,
            "rising_to": bool(lh.get("rising_star")) if lh else False,
        })
    return rows


def rising_star_fates(rows: list) -> list:
    """For every HCP that was a rising star in the EARLIER run, what became of them."""
    out = []
    for r in rows:
        if not r["rising_from"]:
            continue
        if r["status"] == "dropped":
            outcome = "dropped"
        elif r["tier_to"] in ("A", "B"):
            outcome = "top_kol"
        else:
            outcome = "emerging"
        out.append({**r, "outcome": outcome})
    # validated predictions first, then still-emerging, then dropped
    order = {"top_kol": 0, "emerging": 1, "dropped": 2}
    return sorted(out, key=lambda r: (order[r["outcome"]], -(r["score_to"] or 0)))


# --------------------------------------------------------------------------- #
#  HTML rendering
# --------------------------------------------------------------------------- #
def _esc(s):
    return _html.escape("" if s is None else str(s))


def _fmt_score(v):
    return f"{v:.2f}" if isinstance(v, (int, float)) else "—"


def _tier_pill(t):
    if not t:
        return '<span class="muted">—</span>'
    return f'<span class="pill {t.lower()}">{_esc(t)}</span>'


def _tier_change(frm, to):
    if not frm and to:
        return f'{_tier_pill(to)} <span class="muted">(new)</span>'
    if frm and not to:
        return f'{_tier_pill(frm)} <span class="muted">(dropped)</span>'
    if frm == to:
        return f'{_tier_pill(frm)} <span class="muted">→</span> {_tier_pill(to)}'
    return f'{_tier_pill(frm)} <span class="arrow">→</span> {_tier_pill(to)}'


def _delta_badge(d):
    if d is None:
        return '<span class="muted">—</span>'
    if d > 0.0005:
        return f'<span class="delta up">▲ +{d:.2f}</span>'
    if d < -0.0005:
        return f'<span class="delta down">▼ {d:.2f}</span>'
    return '<span class="delta flat">±0.00</span>'


def _rank_cell(rf, rt):
    if rf and rt:
        move = rf - rt  # positive = climbed
        arr = (f' <span class="delta up">▲{move}</span>' if move > 0
               else f' <span class="delta down">▼{-move}</span>' if move < 0 else "")
        return f'#{rf} <span class="muted">→</span> #{rt}{arr}'
    if rt:
        return f'<span class="muted">new</span> → #{rt}'
    return f'#{rf} <span class="muted">→ dropped</span>'


def _stat_cards(cards):
    cells = "".join(
        f'<div class="stat"><div class="v" style="color:{c.get("color", PALETTE["accent"])}">{c["v"]}</div>'
        f'<div class="k">{_esc(c["k"])}</div></div>' for c in cards)
    return f'<div class="stats">{cells}</div>'


def render_report_html(earlier: dict, later: dict) -> str:
    rows = build_rows(earlier, later)
    fates = rising_star_fates(rows)
    promotions = sorted([r for r in rows if r["status"] == "present" and r["tier_dir"] > 0],
                        key=lambda r: (-r["tier_dir"], -(r["score_to"] or 0)))
    demotions = sorted([r for r in rows if r["status"] == "present" and r["tier_dir"] < 0],
                       key=lambda r: (r["tier_dir"], -(r["score_to"] or 0)))
    new_kols = sorted([r for r in rows if r["status"] == "new"],
                      key=lambda r: (-(TIER_RANK.get(r["tier_to"], 0)), -(r["score_to"] or 0)))
    dropped = sorted([r for r in rows if r["status"] == "dropped"],
                     key=lambda r: (-(TIER_RANK.get(r["tier_from"], 0)), -(r["score_from"] or 0)))

    ey = earlier.get("anchor_year") or "earlier"
    ly = later.get("anchor_year") or "latest"
    indication = later.get("indication") or earlier.get("indication") or ""
    drug = later.get("client_drug") or earlier.get("client_drug") or ""

    n_rising = len(fates)
    became = sum(1 for f in fates if f["outcome"] == "top_kol")
    emerging = sum(1 for f in fates if f["outcome"] == "emerging")
    fell = sum(1 for f in fates if f["outcome"] == "dropped")
    hit_rate = f"{became / n_rising * 100:.0f}%" if n_rising else "n/a"

    cards = [
        {"k": f"KOLs · {ey}", "v": len(earlier.get("hcps", []))},
        {"k": f"KOLs · {ly}", "v": len(later.get("hcps", []))},
        {"k": f"Rising stars ({ey})", "v": n_rising, "color": PALETTE["amber"]},
        {"k": "Became A/B KOL", "v": became, "color": PALETTE["emerald"]},
        {"k": "Promotions", "v": len(promotions), "color": PALETTE["emerald"]},
        {"k": "Demotions", "v": len(demotions), "color": PALETTE["red"]},
        {"k": "New KOLs", "v": len(new_kols), "color": PALETTE["accent"]},
        {"k": "Dropped KOLs", "v": len(dropped), "color": PALETTE["muted"]},
    ]

    # --- rising star report card ---
    outcome_label = {"top_kol": ('<span class="pill a">Became A/B KOL</span>', "top"),
                     "emerging": ('<span class="pill rise">Still emerging (C)</span>', "mid"),
                     "dropped": ('<span class="pill drop">Dropped out</span>', "low")}
    fate_rows = "".join(
        f'<tr><td><b>{_esc(f["name"])}</b><br><span class="muted">{_esc(f["specialty"])} · {_esc(f["city"])}</span></td>'
        f'<td>{outcome_label[f["outcome"]][0]}</td>'
        f'<td>{_tier_change(f["tier_from"], f["tier_to"])}</td>'
        f'<td>{_fmt_score(f["score_from"])} <span class="muted">→</span> {_fmt_score(f["score_to"])} {_delta_badge(f["score_delta"])}</td>'
        f'<td>{_rank_cell(f["rank_from"], f["rank_to"])}</td></tr>'
        for f in fates) or '<tr><td colspan="5" class="muted">No rising stars flagged in the earlier run.</td></tr>'
    rising_section = (
        f'<div class="scorecard">'
        f'<div class="scorecard-hero"><div class="big">{hit_rate}</div>'
        f'<div class="muted">of the {n_rising} rising stars from {ey} reached tier A or B by {ly}</div></div>'
        f'<div class="scorecard-legend">'
        f'<span><span class="pill a">{became}</span> became a top KOL</span>'
        f'<span><span class="pill rise">{emerging}</span> still emerging (C)</span>'
        f'<span><span class="pill drop">{fell}</span> dropped out</span></div></div>'
        f'<div class="tbl-wrap"><table><thead><tr><th>Rising star ({ey})</th><th>Outcome</th>'
        f'<th>Tier {ey} → {ly}</th><th>Score</th><th>Rank</th></tr></thead><tbody>{fate_rows}</tbody></table></div>')

    def _move_table(items, empty):
        body = "".join(
            f'<tr><td><b>{_esc(r["name"])}</b><br><span class="muted">{_esc(r["specialty"])} · {_esc(r["city"])}</span></td>'
            f'<td>{_tier_change(r["tier_from"], r["tier_to"])}</td>'
            f'<td>{_fmt_score(r["score_from"])} <span class="muted">→</span> {_fmt_score(r["score_to"])} {_delta_badge(r["score_delta"])}</td>'
            f'<td>{_rank_cell(r["rank_from"], r["rank_to"])}</td>'
            f'{"<td>★</td>" if r["rising_from"] else "<td></td>"}</tr>'
            for r in items) or f'<tr><td colspan="5" class="muted">{empty}</td></tr>'
        return (f'<div class="tbl-wrap"><table><thead><tr><th>KOL</th><th>Tier {ey} → {ly}</th>'
                f'<th>Score</th><th>Rank</th><th title="Was a rising star in the earlier run">★ was rising</th>'
                f'</tr></thead><tbody>{body}</tbody></table></div>')

    def _single_table(items, tier_key, score_key, rank_key, empty):
        body = "".join(
            f'<tr><td><b>{_esc(r["name"])}</b><br><span class="muted">{_esc(r["specialty"])} · {_esc(r["city"])}</span></td>'
            f'<td>{_tier_pill(r[tier_key])}</td>'
            f'<td>{_fmt_score(r[score_key])}</td>'
            f'<td>{("#" + str(r[rank_key])) if r[rank_key] else "—"}</td></tr>'
            for r in items) or f'<tr><td colspan="4" class="muted">{empty}</td></tr>'
        return (f'<div class="tbl-wrap"><table><thead><tr><th>KOL</th><th>Tier</th>'
                f'<th>Score</th><th>Rank</th></tr></thead><tbody>{body}</tbody></table></div>')

    # --- full comparison table (collapsible) ---
    def _status_pill(s):
        return {"present": '<span class="pill c">both runs</span>',
                "new": '<span class="pill b">new</span>',
                "dropped": '<span class="pill drop">dropped</span>'}[s]
    full_sorted = sorted(rows, key=lambda r: (-(r["score_to"] or 0), -(r["score_from"] or 0)))
    full_body = "".join(
        f'<tr><td><b>{_esc(r["name"])}</b><br><span class="muted">{_esc(r["specialty"])} · {_esc(r["city"])}</span></td>'
        f'<td>{_status_pill(r["status"])}</td>'
        f'<td>{_tier_change(r["tier_from"], r["tier_to"])}</td>'
        f'<td>{_fmt_score(r["score_from"])} <span class="muted">→</span> {_fmt_score(r["score_to"])} {_delta_badge(r["score_delta"])}</td>'
        f'<td>{_rank_cell(r["rank_from"], r["rank_to"])}</td>'
        f'<td>{"★" if r["rising_from"] else ""}{" ☆" if (r["rising_to"] and not r["rising_from"]) else ""}</td></tr>'
        for r in full_sorted)
    full_section = (
        f'<details class="full"><summary>Full comparison — every KOL in either run ({len(rows)})</summary>'
        f'<div class="tbl-wrap"><table><thead><tr><th>KOL</th><th>Status</th><th>Tier {ey} → {ly}</th>'
        f'<th>Score</th><th>Rank</th><th title="★ rising in earlier · ☆ newly rising in later">Rising</th>'
        f'</tr></thead><tbody>{full_body}</tbody></table></div></details>')

    css = f"""
      *{{box-sizing:border-box}}
      body{{margin:0;background:{PALETTE['bg']};color:{PALETTE['ink']};
        font:15px/1.55 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif}}
      .wrap{{max-width:1800px;margin:0 auto;padding:30px 12px 70px}}
      header{{border-bottom:3px solid {PALETTE['accent']};padding-bottom:14px;margin-bottom:6px}}
      .kicker{{font-size:12px;letter-spacing:.14em;text-transform:uppercase;color:{PALETTE['accent']};font-weight:800}}
      h1{{font-size:26px;margin:6px 0}}
      h2{{font-size:19px;margin:38px 0 6px;padding-top:12px;border-top:1px solid {PALETTE['line']}}}
      h3{{font-size:15px;margin:18px 0 6px}}
      p{{margin:8px 0}} .muted{{color:{PALETTE['muted']}}}
      .arrow{{color:{PALETTE['emerald']};font-weight:800}}
      code{{background:#eef1f5;padding:1px 5px;border-radius:4px;font-size:12.5px;font-family:Menlo,Consolas,monospace}}
      .explainer{{background:#eef2f7;border-left:3px solid {PALETTE['accent']};color:{PALETTE['muted']};
        border-radius:0 6px 6px 0;padding:8px 14px;margin:10px 0 4px;font-size:13.5px}}
      .explainer strong{{color:{PALETTE['ink']}}}
      .banner{{background:#fdf3e3;border:1px solid {PALETTE['amber']};border-radius:8px;
        padding:10px 14px;margin:12px 0;font-size:13.5px}}
      .stats{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:16px 0}}
      .stat{{background:{PALETTE['card']};border:1px solid {PALETTE['line']};border-radius:10px;padding:14px;text-align:center}}
      .stat .v{{font-size:26px;font-weight:800}} .stat .k{{font-size:12px;color:{PALETTE['muted']};margin-top:2px}}
      table{{border-collapse:collapse;width:100%;font-size:13px;margin:8px 0}}
      th,td{{border:1px solid {PALETTE['line']};padding:7px 10px;text-align:left;vertical-align:top}}
      th{{background:#eef2f7;font-weight:700}}
      .tbl-wrap{{overflow-x:auto}}
      .pill{{display:inline-block;font-size:11px;font-weight:700;padding:1px 8px;border-radius:20px}}
      .pill.a{{background:#e7f5ee;color:{PALETTE['tierA']}}} .pill.b{{background:#eaf0f9;color:{PALETTE['tierB']}}}
      .pill.c{{background:#eef1f5;color:{PALETTE['tierC']}}} .pill.rise{{background:#fbf1dd;color:{PALETTE['amber']}}}
      .pill.drop{{background:#fbeeeb;color:{PALETTE['red']}}}
      .delta{{font-size:11.5px;font-weight:700;padding:0 4px;border-radius:5px}}
      .delta.up{{color:{PALETTE['emerald']}}} .delta.down{{color:{PALETTE['red']}}} .delta.flat{{color:{PALETTE['muted']}}}
      .scorecard{{background:{PALETTE['card']};border:1px solid {PALETTE['line']};border-top:4px solid {PALETTE['amber']};
        border-radius:12px;padding:18px 20px;margin:12px 0;display:flex;flex-wrap:wrap;align-items:center;gap:22px}}
      .scorecard-hero .big{{font-size:44px;font-weight:800;color:{PALETTE['emerald']};line-height:1}}
      .scorecard-hero{{min-width:220px;flex:1}}
      .scorecard-legend{{display:flex;flex-direction:column;gap:6px;font-size:13.5px}}
      .cols{{display:grid;grid-template-columns:1fr 1fr;gap:18px}}
      @media(max-width:820px){{.cols{{grid-template-columns:1fr}} .stats{{grid-template-columns:repeat(2,1fr)}}}}
      details.full{{margin:14px 0;background:{PALETTE['card']};border:1px solid {PALETTE['line']};border-radius:10px;padding:6px 14px}}
      details.full summary{{cursor:pointer;font-weight:700;color:{PALETTE['accent']};padding:6px 0}}
      footer{{margin-top:36px;color:{PALETTE['muted']};font-size:12px;border-top:1px solid {PALETTE['line']};padding-top:12px}}
      @media print{{body{{background:#fff}} details.full[open] summary{{list-style:none}}}}
    """

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>KOL Backtest — {_esc(indication)} · {_esc(ey)} → {_esc(ly)}</title><style>{css}</style></head>
<body><div class="wrap">
<header>
  <div class="kicker">Service 2.1 · KOL Identification &amp; Mapping — Backtest</div>
  <h1>KOL comparison: {_esc(ey)} → {_esc(ly)}</h1>
  <p class="muted">Indication: {_esc(indication)}{' · Client drug: ' + _esc(drug) if drug else ''} ·
     generated {_esc(datetime.now().strftime('%Y-%m-%d %H:%M'))}</p>
</header>

<div class="banner"><b>What this is:</b> the same pipeline run twice — once anchored to <b>{_esc(ey)}</b>,
once to <b>{_esc(ly)}</b> — compared HCP-by-HCP (matched on <code>s_customer_id</code>). It answers the
validation question: <em>did the doctors we would have flagged as rising back in {_esc(ey)} actually become
top KOLs by {_esc(ly)}?</em> PubMed evidence is capped at each anchor year; web sources are timestamp-free.</div>

{_stat_cards(cards)}

<h2>① Rising-star report card</h2>
<p class="explainer"><strong>How to read this:</strong> every doctor flagged <b>Rising</b> in the {_esc(ey)} run,
and what became of them by {_esc(ly)}. "Became a top KOL" = reached tier A or B — a validated prediction.
"Still emerging" = present but tier C. "Dropped out" = no verified relevant sources in the later run.</p>
{rising_section}

<h2>② Tier movements</h2>
<p class="explainer"><strong>How to read this:</strong> doctors present in <em>both</em> runs whose tier changed.
Promotions climbed (C→B, B→A, C→A); demotions fell. Score is the composite <code>kol_score</code>; the rank
column shows movement in the overall ranking. ★ marks a doctor who was a rising star in {_esc(ey)}.</p>
<div class="cols">
  <div><h3 style="color:{PALETTE['emerald']}">▲ Promotions ({len(promotions)})</h3>{_move_table(promotions, "No promotions.")}</div>
  <div><h3 style="color:{PALETTE['red']}">▼ Demotions ({len(demotions)})</h3>{_move_table(demotions, "No demotions.")}</div>
</div>

<h2>③ New KOLs in {_esc(ly)} ({len(new_kols)})</h2>
<p class="explainer"><strong>How to read this:</strong> doctors who earned verified relevant sources in the
{_esc(ly)} run but were absent from the {_esc(ey)} run — the fresh entrants to the list.</p>
{_single_table(new_kols, "tier_to", "score_to", "rank_to", "No new KOLs.")}

<h2>④ Dropped from {_esc(ey)} ({len(dropped)})</h2>
<p class="explainer"><strong>How to read this:</strong> doctors who were KOLs in the {_esc(ey)} run but have no
verified relevant sources in the {_esc(ly)} run — usually because their evidence sits outside the later
anchor's window, or newer verified evidence re-sorted the pool below them.</p>
{_single_table(dropped, "tier_from", "score_from", "rank_from", "No dropped KOLs.")}

<h2>⑤ Full comparison</h2>
<p class="explainer"><strong>How to read this:</strong> the complete before/after picture for every doctor
appearing in either run, sorted by their {_esc(ly)} score. ★ = rising in {_esc(ey)}, ☆ = newly rising in {_esc(ly)}.</p>
{full_section}

<footer>Backtest comparison for Service 2.1 — KOL Identification &amp; Mapping.
Tiers are relative to each run's own score distribution (A ≈ top 15%, B ≈ next ~25%, C the rest);
"rising star" is computed from verified-relevant PubMed activity only. Source: <code>06_backtest_compare.py</code>.</footer>
</div></body></html>"""


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--earlier", required=True); p.add_argument("--later", required=True)
    p.add_argument("--no-html", action="store_true", help="skip the HTML report")
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

    if not args.no_html:
        os.makedirs(os.path.join(_DIR, "results"), exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        html_path = os.path.join(_DIR, "results", f"backtest_report_{ts}.html")
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(render_report_html(earlier, later))
        log.info(f"Wrote {html_path}")


if __name__ == "__main__":
    main()
