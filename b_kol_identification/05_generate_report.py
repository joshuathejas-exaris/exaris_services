"""
Stage 05: Generate the KOL report (HTML top-25) + Excel.
Reads:  data/kol_final.json
Writes: results/kol_report_<ts>.html  and  results/kol_report_<ts>.xlsx
"""
import configparser, html as _html, json, logging, math, os, re, sys
from datetime import datetime

logging.basicConfig(format="%(asctime)s [%(levelname)s] %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)
_DIR = os.path.dirname(__file__)

PALETTE = {
    "ink": "#1b2430", "muted": "#5c6774", "line": "#e2e7ee", "bg": "#f4f6f8", "card": "#fff",
    "accent": "#2f4a7c", "teal": "#0d7d74", "violet": "#6d5ac0", "amber": "#b7791f", "emerald": "#1f8a5b",
    "tierA": "#1f8a5b", "tierB": "#3b5b92", "tierC": "#6b7684",
    "pos": "#0d7d74", "neu": "#6b7684", "neg": "#b4432f",
}


def _esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&#39;"))


def _rgba(hex_color, alpha):
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r},{g},{b},{alpha})"


# Fallback composite weights (Task 6 defaults) used when the caller does not pass
# the run's actual [scoring] weights through to the report renderer.
DEFAULT_WEIGHTS = {"relevance": 0.60, "reach": 0.25, "ratio": 0.15}


def as_of_banner(anchor_year, as_of_year_cfg) -> str:
    if not as_of_year_cfg or str(as_of_year_cfg).strip().lower() == "latest":
        return ""
    return (f'<div class="asof-banner">Backtest view — PubMed capped at {anchor_year}. '
            f'Web sources are timestamp-free and shown as-is (frozen across years).</div>')


def section_explainer(text: str) -> str:
    return f'<p class="explainer"><strong>How to read this:</strong> {_html.escape(text)}</p>'


def render_score_breakdown(hcp: dict, weights: dict) -> str:
    fc = hcp.get("factor_contributions", {})
    reach = hcp.get("reach", {}); ratio = hcp.get("ratio", {})
    rows = [
        ("Relevance", weights["relevance"], hcp.get("norm_relevance", 0), fc.get("relevance", 0),
         f'{hcp.get("verified_web_count",0)+hcp.get("verified_pubmed_count",0)} verified sources'),
        ("Reach", weights["reach"], hcp.get("norm_reach", 0), fc.get("reach", 0),
         f'{reach.get("distinct_coauthors",0)} co-authors, {reach.get("distinct_affiliations",0)} institutions'),
        ("Ratio", weights["ratio"], hcp.get("norm_ratio", 0), fc.get("ratio", 0),
         f'{ratio.get("ratio",0):.0%} of {ratio.get("denominator",0)} total sources'),
    ]
    tr = "".join(
        f'<tr><td>{name}</td><td>{w:.2f}</td><td>{norm:.2f}</td><td>{contrib:.3f}</td><td>{_html.escape(ev)}</td></tr>'
        for (name, w, norm, contrib, ev) in rows)
    quotes = "".join(
        f'<li>“{_html.escape(q.get("quote", ""))}” '
        f'<a href="{_html.escape(q.get("url",""))}">source</a></li>'
        for q in hcp.get("top_quotes", []))
    return (f'<details class="score-breakdown"><summary>Composite {hcp.get("kol_score",0):.2f} — how it was scored</summary>'
            f'<table><thead><tr><th>Factor</th><th>Weight</th><th>Norm</th><th>Contribution</th><th>Evidence</th></tr></thead>'
            f'<tbody>{tr}</tbody></table>'
            f'<div class="score-quotes"><strong>Evidence quotes:</strong><ul>{quotes}</ul></div></details>')


def render_network_svg(edges: list, nodes: list, width: int = 720, height: int = 480) -> str:
    if not nodes:
        return '<svg width="1" height="1"></svg>'
    cx, cy, r = width / 2, height / 2, min(width, height) / 2 - 60
    pos, maxreach = {}, max((n.get("reach", 0) for n in nodes), default=1) or 1
    for i, n in enumerate(nodes):
        ang = 2 * math.pi * i / len(nodes)
        pos[n["name"]] = (cx + r * math.cos(ang), cy + r * math.sin(ang))
    lines = []
    for e in edges:
        a, b = e.get("a_name"), e.get("b_name")
        if a in pos and b in pos:
            (x1, y1), (x2, y2) = pos[a], pos[b]
            w = 1 + min(int(e.get("shared_pmids", 1)), 6)
            dash = ' stroke-dasharray="4"' if e.get("b_external") else ""
            lines.append(f'<line x1="{x1:.0f}" y1="{y1:.0f}" x2="{x2:.0f}" y2="{y2:.0f}" '
                         f'stroke="#9bb" stroke-width="{w}"{dash}/>')
    circles = []
    for n in nodes:
        x, y = pos[n["name"]]
        rad = 6 + 14 * (n.get("reach", 0) / maxreach)
        aff = _html.escape(str(n.get("affiliation", "")))
        circles.append(f'<g><title>{_html.escape(n["name"])} — {aff} (reach {n.get("reach",0)})</title>'
                       f'<circle cx="{x:.0f}" cy="{y:.0f}" r="{rad:.0f}" fill="#3a7"/>'
                       f'<text x="{x:.0f}" y="{y-rad-3:.0f}" font-size="10" text-anchor="middle">'
                       f'{_html.escape(n["name"])}</text></g>')
    return (f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
            f'{"".join(lines)}{"".join(circles)}</svg>')


def _recent_prior(pub_by_year):
    """Recent (current + prior year) vs. prior publication counts, from a year->count dict."""
    years = {int(y): int(c) for y, c in (pub_by_year or {}).items() if str(y).isdigit()}
    if not years:
        return 0, 0
    cur = max(years)
    recent = sum(c for y, c in years.items() if y >= cur - 1)
    prior = sum(c for y, c in years.items() if y < cur - 1)
    return recent, prior


def render_stat_cards(data):
    hcps = data["hcps"]
    tiers = {t: sum(1 for h in hcps if h.get("tier") == t) for t in "ABC"}
    rising = sum(1 for h in hcps if h.get("rising_star"))
    total_sources = sum(h.get("verified_web_count", 0) + h.get("verified_pubmed_count", 0) for h in hcps)
    cards = [("KOLs", len(hcps)), ("Tier A", tiers["A"]), ("Tier B", tiers["B"]),
             ("Tier C", tiers["C"]), ("Rising Stars", rising), ("Verified sources", total_sources)]
    cells = "".join(
        f'<div class="stat"><div class="v">{v}</div><div class="k">{_esc(k)}</div></div>' for k, v in cards)
    return f'<div class="stats">{cells}</div>'


def render_kol_table(hcps, top_n, weights=None):
    weights = weights or DEFAULT_WEIGHTS
    rows = ""
    for i, h in enumerate(hcps[:top_n], 1):
        themes = ", ".join(_esc(t["term_en"]) for t in h.get("theme_labels", [])[:3])
        badge = f'<span class="pill {h.get("tier","C").lower()}">{h.get("tier","C")}</span>'
        rising = ' <span class="pill rise">Rising</span>' if h.get("rising_star") else ""
        rows += (f'<tr><td>{i}</td><td>{badge}{rising}</td>'
                 f'<td><b>{_esc(h["name"])}</b><br><span class="muted">{_esc(h["specialty"])}</span></td>'
                 f'<td>{_esc(h["city"])}</td>'
                 f'<td><b>{h["kol_score"]:.2f}</b> '
                 f'<span class="muted">({h.get("verified_web_count",0)}w / {h.get("verified_pubmed_count",0)}p)</span>'
                 f'{render_score_breakdown(h, weights)}</td>'
                 f'<td>{h.get("latest_year","")}</td><td>{themes}</td></tr>')
    return (f'<table><thead><tr><th>#</th><th>Tier</th><th>Name / Specialty</th><th>City</th>'
            f'<th>Composite score</th><th>Latest</th><th>Themes</th></tr></thead><tbody>{rows}</tbody></table>')


def render_sparkline(pub_by_year, all_years, width=80, height=24):
    counts = [pub_by_year.get(y, 0) for y in all_years]
    max_v = max(counts, default=0) or 1
    n = max(len(all_years), 1)
    bw = width / n - 1
    bars = []
    for i, c in enumerate(counts):
        bh = max(2.0, c / max_v * (height - 4))
        x = i * (width / n)
        y = height - bh
        bars.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw:.1f}" '
                    f'height="{bh:.1f}" fill="{PALETTE["accent"]}"/>')
    return f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}">{"".join(bars)}</svg>'


def render_rising_stars(hcps, all_years):
    stars = [h for h in hcps if h.get("rising_star")]
    if not stars:
        return ""
    cards = ""
    for h in stars:
        # The Rising badge (Stage 04) is computed from verified_pubmed_years, so the
        # displayed recent/prior/ratio must come from the same (verified) field -- not
        # the unverified/candidate pub_by_year -- or the numbers won't justify the badge.
        recent, prior = _recent_prior(h.get("verified_pubmed_years", {}))
        ratio = f"{recent / max(prior, 1):.1f}×" if prior > 0 else "New voice"
        spark = render_sparkline(h.get("pub_by_year", {}), all_years, width=190, height=34)
        themes = "".join(f'<span class="tag">{_esc(t["term_en"])}</span>' for t in h.get("theme_labels", []))
        cards += (
            f'<div class="rising-card"><b>{_esc(h.get("name",""))}</b> '
            f'<span class="pill rise">Rising</span><br>'
            f'<span class="muted">{_esc(h.get("specialty",""))} · {_esc(h.get("city",""))}</span>'
            f'<div style="margin:.5rem 0">{spark}'
            f'<span class="muted spark-label">pubs / year</span></div>'
            f'<span class="muted"><b>{recent}</b> recent vs <b>{prior}</b> prior &middot; {ratio}</span>'
            f'<div style="margin-top:.4rem">{themes}</div></div>'
        )
    return f'<h2>Rising Stars</h2><div class="rising-grid">{cards}</div>'


def render_thematic_heatmap(hcps, pca_terms, top_n=20):
    top = hcps[:top_n]
    keys = [t["term_key"] for t in pca_terms]

    def _tc(h):
        return {t["term_key"]: t["count"] for t in h.get("theme_labels", [])}

    col_max = {k: max((_tc(h).get(k, 0) for h in top), default=1) or 1 for k in keys}
    headers = "".join(f'<th title="{_esc(t["term_en"])}">{_esc(t["term_en"])}</th>' for t in pca_terms)
    rows = ""
    for h in top:
        tc = _tc(h)
        badge = f'<span class="pill {h.get("tier","C").lower()}">{h.get("tier","C")}</span>'
        cells = ""
        for k in keys:
            count = tc.get(k, 0)
            alpha = round(count / col_max[k] * 0.75, 2) if count else 0
            bg = _rgba(PALETTE["accent"], alpha) if count else "transparent"
            cells += f'<td style="text-align:center;background:{bg}">{count or ""}</td>'
        rows += f'<tr><td class="muted">{_esc(h.get("name",""))}</td><td>{badge}</td>{cells}</tr>'
    return (f'<h2>Thematic Distribution — Top {top_n}</h2>'
            f'<div class="hmap-wrap"><table><thead><tr><th>KOL</th><th>Tier</th>{headers}</tr></thead>'
            f'<tbody>{rows}</tbody></table></div>')


def render_regional(hcps, top_n=20):
    from collections import defaultdict
    city_data = defaultdict(lambda: {"A": 0, "B": 0, "C": 0})
    for h in hcps:
        city = h.get("city") or "Unknown"
        city_data[city][h.get("tier", "C")] += 1
    ranked = sorted(city_data.items(), key=lambda x: -(x[1]["A"] + x[1]["B"] + x[1]["C"]))[:top_n]
    if not ranked:
        return ""
    max_total = max(d["A"] + d["B"] + d["C"] for _, d in ranked) or 1
    bar_max_px = 260
    rows = ""
    for city, d in ranked:
        total = d["A"] + d["B"] + d["C"]
        scale = total / max_total
        wa = int(d["A"] / total * bar_max_px * scale) if total else 0
        wb = int(d["B"] / total * bar_max_px * scale) if total else 0
        wc = int(d["C"] / total * bar_max_px * scale) if total else 0
        segs = ""
        if wa:
            segs += f'<div style="width:{wa}px;background:{PALETTE["tierA"]}"></div>'
        if wb:
            segs += f'<div style="width:{wb}px;background:{PALETTE["tierB"]}"></div>'
        if wc:
            segs += f'<div style="width:{wc}px;background:{PALETTE["tierC"]}"></div>'
        labels = ""
        if d["A"]:
            labels += f'<span class="pill a">{d["A"]}A</span> '
        if d["B"]:
            labels += f'<span class="pill b">{d["B"]}B</span> '
        if d["C"]:
            labels += f'<span class="pill c">{d["C"]}C</span>'
        rows += (f'<div class="city-row"><div class="city-label" title="{_esc(city)}">{_esc(city)}</div>'
                 f'<div class="city-bar" style="width:{bar_max_px}px">{segs}</div>'
                 f'<div class="city-count muted">{total} &nbsp; {labels}</div></div>')
    return f'<h2>Regional Distribution</h2>{rows}'


def render_network(coauthor_edges, comention_edges, hcps):
    def rows(edges, kind):
        out = ""
        for e in sorted(edges, key=lambda x: x.get("shared_pmids", x.get("count", 0)), reverse=True):
            if kind == "co":
                tag = ' <span class="pill ext">external</span>' if e.get("b_external") else ""
                out += (f'<tr><td>{_esc(e["a_name"])}</td><td>{_esc(e["b_name"])}{tag}</td>'
                        f'<td>{e["shared_pmids"]} shared PMIDs</td></tr>')
            else:
                out += (f'<tr><td>{_esc(e["from_name"])}</td><td>{_esc(e["to_name"])}</td>'
                        f'<td>{e["count"]} web mentions</td></tr>')
        return out or '<tr><td colspan="3" class="muted">none</td></tr>'
    return (f'<h3>PubMed co-authorship</h3><table><tbody>{rows(coauthor_edges,"co")}</tbody></table>'
            f'<h3>Web co-mentions</h3><table><tbody>{rows(comention_edges,"men")}</tbody></table>')


_SENT_COLOR_KEY = {"positive": "pos", "negative": "neg"}


def render_profiles(hcps, all_years, top_n=10, weights=None):
    weights = weights or DEFAULT_WEIGHTS
    year_range = f"{all_years[0]}–{all_years[-1]}" if all_years else ""
    cards = ""
    for h in hcps[:top_n]:
        tier = h.get("tier", "C")
        badge = f'<span class="pill {tier.lower()}">{tier}</span>'
        rising = ' <span class="pill rise">Rising</span>' if h.get("rising_star") else ""
        spark = render_sparkline(h.get("pub_by_year", {}), all_years, width=190, height=34)
        themes = "".join(f'<span class="tag">{_esc(t["term_en"])}</span>' for t in h.get("theme_labels", []))
        verified_total = h.get("verified_web_count", 0) + h.get("verified_pubmed_count", 0)
        meta = (f'<div class="muted">Composite score {h.get("kol_score",0):.2f} &middot; '
                f'{verified_total} verified sources '
                f'({h.get("verified_web_count",0)} web / {h.get("verified_pubmed_count",0)} pubmed) '
                f'&middot; latest {h.get("latest_year","")}</div>')
        quotes = ""
        for q in h.get("top_quotes", [])[:3]:
            color = PALETTE[_SENT_COLOR_KEY.get(q.get("sentiment"), "neu")]
            link = (f' <a href="{_esc(q["url"])}">source</a>' if q.get("url") else "")
            quotes += (f'<div class="quote" style="border-left:3px solid {color}">'
                       f'“{_esc(q.get("quote",""))}”{link}</div>')
        cards += (
            f'<div class="profile-card">'
            f'<div style="display:flex;justify-content:space-between">'
            f'<div><b>{_esc(h.get("name",""))}</b><br>'
            f'<span class="muted">{_esc(h.get("specialty",""))} · {_esc(h.get("city",""))}</span></div>'
            f'<div>{badge}{rising}</div></div>'
            f'{meta}'
            f'<div style="margin:.5rem 0">{spark}'
            f'<span class="muted spark-label">pubs/yr ({year_range})</span></div>'
            f'<div>{themes}</div>{quotes}{render_score_breakdown(h, weights)}</div>'
        )
    return f'<h2>Individual KOL Profiles — Top {top_n}</h2><div class="profile-grid">{cards}</div>'


def build_year_axis(data):
    """Fixed pub_history_years-length axis of string years ending at anchor_year.
    Falls back to the union of years present in pub_by_year for pre-anchor JSON."""
    anchor = data.get("anchor_year")
    span = int(data.get("pub_history_years") or 20)
    if anchor:
        anchor = int(anchor)
        return [str(y) for y in range(anchor - span + 1, anchor + 1)]
    present = sorted({y for h in data.get("hcps", []) for y in h.get("pub_by_year", {})})
    return [str(y) for y in present]


def tab_id(label: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")
    return "tab-" + (slug or "x")


TAB_SCRIPT = """
<script>
function showTab(id){
  var ps = document.querySelectorAll('.panel');
  for (var i = 0; i < ps.length; i++){ ps[i].classList.toggle('active', ps[i].id === id); }
  var ns = document.querySelectorAll('.nav-item');
  for (var j = 0; j < ns.length; j++){
    var on = ns[j].getAttribute('href') === '#' + id;
    ns[j].classList.toggle('active', on);
    if (on) { ns[j].setAttribute('aria-current', 'page'); }
    else { ns[j].removeAttribute('aria-current'); }
  }
  return false;
}
document.addEventListener('DOMContentLoaded', function(){
  document.body.classList.add('js-tabs');
  var f = document.querySelector('.panel');
  if (f) { showTab(f.id); }
});
</script>
"""


def _render_sidebar(groups):
    """groups: list of (group_label, [(item_label, panel_html), ...]). Sticky left nav
    beside a content pane of panels; first item active on load; degrades to full scroll
    without JS. Empty groups/items are skipped."""
    nav = ['<nav class="sidebar" role="tablist" aria-label="Report sections">']
    panels = []
    first = True
    for group_label, items in groups:
        items = [it for it in items if it and it[1]]
        if not items:
            continue
        nav.append(f'<div class="nav-group-label">{_esc(group_label)}</div>')
        for item_label, body in items:
            tid = tab_id(item_label)
            active = " active" if first else ""
            current = ' aria-current="page"' if first else ""
            nav.append(f'<a class="nav-item{active}" role="tab" id="{tid}-btn" '
                       f'href="#{tid}" aria-controls="{tid}"{current} '
                       f'onclick="return showTab(\'{tid}\')">{_esc(item_label)}</a>')
            panels.append(f'<section class="panel{active}" role="tabpanel" id="{tid}" '
                          f'aria-labelledby="{tid}-btn">\n{body}\n</section>')
            first = False
    nav.append("</nav>")
    content = '<main class="content">\n' + "\n".join(panels) + "\n</main>"
    return '<div class="layout">\n' + "\n".join(nav) + "\n" + content + "\n</div>"


def build_report_html(data, weights=None, as_of_year_cfg="latest"):
    weights = weights or DEFAULT_WEIGHTS
    all_years = build_year_axis(data)
    top_n = 25
    css = f"""
      body{{margin:0;background:{PALETTE['bg']};color:{PALETTE['ink']};
        font:15px/1.55 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif}}
      .wrap{{max-width:1100px;margin:0 auto;padding:28px 22px 64px}}
      h1{{margin:4px 0}} h2{{border-top:1px solid {PALETTE['line']};padding-top:14px;margin-top:34px}}
      table{{border-collapse:collapse;width:100%;font-size:13px;margin:10px 0}}
      th,td{{border:1px solid {PALETTE['line']};padding:7px 10px;text-align:left;vertical-align:top}}
      th{{background:#eef2f7}} .muted{{color:{PALETTE['muted']}}}
      .stats{{display:grid;grid-template-columns:repeat(6,1fr);gap:12px;margin:14px 0}}
      .stat{{background:{PALETTE['card']};border:1px solid {PALETTE['line']};border-radius:10px;padding:14px;text-align:center}}
      .stat .v{{font-size:24px;font-weight:800;color:{PALETTE['accent']}}} .stat .k{{font-size:12px;color:{PALETTE['muted']}}}
      .pill{{font-size:11px;font-weight:700;padding:1px 7px;border-radius:20px}}
      .pill.a{{background:#e7f5ee;color:{PALETTE['tierA']}}} .pill.b{{background:#eaf0f9;color:{PALETTE['tierB']}}}
      .pill.c{{background:#eef1f5;color:{PALETTE['tierC']}}} .pill.rise{{background:#fbf1dd;color:{PALETTE['amber']}}}
      .pill.ext{{background:#efecfa;color:{PALETTE['violet']}}}
      .tag{{display:inline-block;background:#eef2f7;color:{PALETTE['teal']};font-size:11px;
        padding:1px 7px;border-radius:10px;margin:2px 3px 0 0}}
      .rising-grid,.profile-grid{{display:grid;gap:12px;margin:14px 0}}
      .rising-grid{{grid-template-columns:repeat(auto-fill,minmax(220px,1fr))}}
      .profile-grid{{grid-template-columns:repeat(auto-fill,minmax(300px,1fr))}}
      .rising-card,.profile-card{{background:{PALETTE['card']};border:1px solid {PALETTE['line']};
        border-radius:10px;padding:12px 14px}}
      .rising-card{{border-top:3px solid {PALETTE['amber']}}}
      .spark-label{{font-size:11px;margin-left:6px}}
      .hmap-wrap{{overflow-x:auto}}
      .hmap-wrap th{{writing-mode:vertical-rl;transform:rotate(180deg);font-size:11px;
        padding:6px 4px;min-width:30px}}
      .city-row{{display:flex;align-items:center;gap:10px;margin:5px 0;font-size:13px}}
      .city-label{{width:130px;text-align:right;white-space:nowrap;overflow:hidden;
        text-overflow:ellipsis;flex-shrink:0}}
      .city-bar{{height:16px;display:flex;border-radius:3px;overflow:hidden;flex-shrink:0}}
      .quote{{font-size:12px;font-style:italic;padding:4px 8px;margin-top:6px;color:{PALETTE['ink']}}}
      .quote a{{font-style:normal;font-size:11px;color:{PALETTE['accent']}}}
      .asof-banner{{background:#fdf3e3;border:1px solid {PALETTE['amber']};color:{PALETTE['ink']};
        border-radius:8px;padding:10px 14px;margin:10px 0;font-size:13px}}
      .explainer{{background:#eef2f7;border-left:3px solid {PALETTE['accent']};color:{PALETTE['muted']};
        border-radius:0 6px 6px 0;padding:6px 12px;margin:8px 0 14px;font-size:13px}}
      .explainer strong{{color:{PALETTE['ink']}}}
      details.score-breakdown{{margin-top:6px;font-size:12px}}
      details.score-breakdown summary{{cursor:pointer;color:{PALETTE['accent']};font-weight:600}}
      details.score-breakdown table{{font-size:12px;margin:6px 0}}
      .score-quotes{{margin-top:6px}} .score-quotes ul{{margin:4px 0;padding-left:18px}}
      .network-svg-wrap{{overflow-x:auto;margin:10px 0}}
      .layout{{display:flex;gap:28px;align-items:flex-start;margin:18px 0 8px}}
      .sidebar{{flex:0 0 210px;position:sticky;top:16px;align-self:flex-start}}
      .content{{flex:1 1 auto;min-width:0}}
      .nav-group-label{{text-transform:uppercase;letter-spacing:.6px;font-size:11px;
        font-weight:700;color:{PALETTE['muted']};margin:16px 0 6px}}
      .nav-group-label:first-child{{margin-top:0}}
      .nav-item{{display:block;padding:6px 10px;margin:2px 0;border-radius:6px;
        color:{PALETTE['ink']};font-size:14px;text-decoration:none;border-left:3px solid transparent}}
      .nav-item:hover{{background:#eef2f7;color:{PALETTE['accent']}}}
      .nav-item.active{{background:#eef4fb;color:{PALETTE['accent']};font-weight:600;
        border-left-color:{PALETTE['accent']}}}
      body.js-tabs .panel{{display:none}} body.js-tabs .panel.active{{display:block}}
      .content h2:first-child{{margin-top:0;border-top:none;padding-top:0}}
      @media(max-width:720px){{.stats{{grid-template-columns:repeat(2,1fr)}}
        .layout{{flex-direction:column;gap:8px}} .sidebar{{position:static;flex-basis:auto;width:100%}}}}
    """
    top = data["hcps"]
    # Stage 04 now threads through the HCP's actual co-author affiliation strings
    # (top_affiliations) -- real institutions, which is what conveys cross-org
    # activity on hover. Practice city is only a last-resort fallback for KOLs
    # with no PubMed co-author affiliation data at all.
    network_nodes = [{"name": h.get("name", ""),
                       "reach": h.get("reach", {}).get("distinct_coauthors", 0),
                       "affiliation": ", ".join(h.get("affiliations", [])) or h.get("city", "")} for h in top]
    banner = as_of_banner(data.get("anchor_year"), as_of_year_cfg)

    def _splice_explainer(section_html: str, explainer_text: str) -> str:
        """Insert section_explainer(...) right after a section's leading <h2>...</h2>."""
        exp = section_explainer(explainer_text)
        marker = "</h2>"
        idx = section_html.find(marker)
        if idx == -1:
            return exp + section_html
        idx += len(marker)
        return section_html[:idx] + exp + section_html[idx:]

    kol_ranking_section = _splice_explainer(
        f'<h2>KOL Ranking — Top {top_n}</h2>{render_kol_table(top, top_n, weights)}',
        "KOLs are ranked by a composite score that blends Relevance (LLM-verified topical "
        "engagement), Reach (co-author and institution breadth), and Ratio (the share of the "
        "KOL's total output that is on-topic); expand a row's score to see each factor's "
        "weight and contribution.")
    rising_section = _splice_explainer(
        render_rising_stars(top, all_years)
        or '<h2>Rising Stars</h2><p class="muted">No rising stars identified.</p>',
        "A KOL is flagged Rising when their verified PubMed output in the most recent years "
        "accelerates sharply versus prior years, or when they have no prior verified output "
        "at all (a new voice).")
    thematic_section = _splice_explainer(
        render_thematic_heatmap(top, data.get("pca_terms", []), top_n=top_n),
        "Cell shading shows how concentrated a KOL's verified claims are on that theme "
        "relative to other KOLs in the top list -- darker cells mean more verified claims "
        "on that theme.")
    network_section = _splice_explainer(
        f'<h2>Collaboration network</h2>'
        f'<div class="network-svg-wrap">{render_network_svg(data.get("coauthor_edges", []), network_nodes)}</div>'
        f'{render_network(data["coauthor_edges"], data["comention_edges"], top)}',
        "Nodes are KOLs sized by co-author reach; edges are shared PubMed authorship "
        "(thicker = more shared publications, dashed = an external, non-mapped co-author). "
        "The tables below list every edge in detail.")
    groups = [
        ("OVERVIEW", [
            ("Executive Dashboard", f'<h2>Executive dashboard</h2>{render_stat_cards(data)}'),
            ("KOL Ranking", kol_ranking_section),
        ]),
        ("ANALYSIS", [
            ("Rising Stars", rising_section),
            ("Thematic Distribution", thematic_section),
            ("Regional Distribution", render_regional(top)),
            ("Collaboration Network", network_section),
        ]),
        ("PROFILES", [
            ("KOL Profiles", render_profiles(top, all_years, top_n=top_n, weights=weights)),
        ]),
    ]
    header = (f'<h1>KOL Identification — {_esc(data["indication"])}</h1>'
              f'<p class="muted">Client drug: {_esc(data["client_drug"])} · '
              f'generated {_esc(data["generated_at"])}</p>' + banner)
    body = header + "\n" + _render_sidebar(groups) + "\n" + TAB_SCRIPT
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>KOL Report — {_esc(data['indication'])}</title><style>{css}</style></head>
<body><div class="wrap">
{body}
</div></body></html>"""


def write_excel(data: dict, path: str) -> None:
    import openpyxl
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = "KOLs"
    headers = ["Rank", "Name", "Specialty", "City", "Tier", "Rising star",
               "Composite score", "Verified sources", "Web", "PubMed", "Latest year", "Top themes",
               "Representative quote", "Source URL",
               "norm_relevance", "norm_reach", "norm_ratio",
               "contribution_relevance", "contribution_reach", "contribution_ratio",
               "distinct_coauthors", "distinct_affiliations", "relevance_ratio"]
    ws.append(headers)
    for i, h in enumerate(data["hcps"], 1):
        q = (h.get("top_quotes") or [{}])[0]
        fc = h.get("factor_contributions", {})
        reach = h.get("reach", {}); ratio = h.get("ratio", {})
        verified_total = h.get("verified_web_count", 0) + h.get("verified_pubmed_count", 0)
        ws.append([i, h["name"], h["specialty"], h["city"], h.get("tier", ""),
                   "yes" if h.get("rising_star") else "",
                   h.get("kol_score", 0), verified_total,
                   h.get("verified_web_count", 0), h.get("verified_pubmed_count", 0),
                   h.get("latest_year", ""),
                   ", ".join(t["term_en"] for t in h.get("theme_labels", [])[:5]),
                   q.get("quote", ""), q.get("url", ""),
                   h.get("norm_relevance", 0), h.get("norm_reach", 0), h.get("norm_ratio", 0),
                   fc.get("relevance", 0), fc.get("reach", 0), fc.get("ratio", 0),
                   reach.get("distinct_coauthors", 0), reach.get("distinct_affiliations", 0),
                   ratio.get("ratio", 0)])
    wb.save(path)


def main():
    import argparse
    p = argparse.ArgumentParser(); p.add_argument("--force", action="store_true")
    args = p.parse_args()
    cfg = configparser.ConfigParser(); cfg.read(os.path.join(_DIR, "config.ini"))
    sc = cfg["scoring"] if cfg.has_section("scoring") else {}
    weights = {"relevance": float(sc.get("weight_relevance", DEFAULT_WEIGHTS["relevance"])),
               "reach": float(sc.get("weight_reach", DEFAULT_WEIGHTS["reach"])),
               "ratio": float(sc.get("weight_ratio", DEFAULT_WEIGHTS["ratio"]))}
    as_of_year_cfg = cfg["funnel"].get("as_of_year", "latest") if cfg.has_section("funnel") else "latest"
    with open(os.path.join(_DIR, "data", "kol_final.json"), encoding="utf-8") as f:
        data = json.load(f)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    html_path = os.path.join(_DIR, "results", f"kol_report_{ts}.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(build_report_html(data, weights=weights, as_of_year_cfg=as_of_year_cfg))
    log.info(f"Wrote {html_path}")
    xlsx_path = os.path.join(_DIR, "results", f"kol_report_{ts}.xlsx")
    write_excel(data, xlsx_path)
    log.info(f"Wrote {xlsx_path}")

if __name__ == "__main__":
    main()
