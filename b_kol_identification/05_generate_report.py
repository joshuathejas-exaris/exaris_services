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


def _select_network(coauthor_edges: list, kol_nodes: list, max_external: int = 40):
    """Pick the nodes+edges to draw: all KOLs + the most-connected external co-authors,
    keeping only edges whose endpoints are both in the set, then drop isolated nodes.

    Returns (nodes, edges) where each node is
    {name, reach, affiliation, kol: bool, degree: int} and each edge is
    {a, b, w, external: bool} (w = shared PubMed papers)."""
    kol_by_name = {n["name"]: n for n in kol_nodes if n.get("name")}
    # rank external co-authors by how many KOLs they bridge, then shared-paper volume
    ext = {}
    for e in coauthor_edges:
        a, b = e.get("a_name"), e.get("b_name")
        if a and b and e.get("b_external") and b not in kol_by_name:
            s = ext.setdefault(b, {"deg": set(), "shared": 0})
            s["deg"].add(a)
            s["shared"] += int(e.get("shared_pmids", 0) or 0)
    ranked = sorted(ext.items(), key=lambda kv: (len(kv[1]["deg"]), kv[1]["shared"], kv[0]), reverse=True)
    keep_ext = {name for name, _ in ranked[:max_external]}

    edges = []
    for e in coauthor_edges:
        a, b = e.get("a_name"), e.get("b_name")
        if not a or not b or a == b or a not in kol_by_name:
            continue
        if b in kol_by_name or b in keep_ext:
            edges.append({"a": a, "b": b, "w": int(e.get("shared_pmids", 0) or 0),
                          "external": bool(e.get("b_external") and b not in kol_by_name)})

    degree = {}
    for e in edges:
        degree[e["a"]] = degree.get(e["a"], 0) + 1
        degree[e["b"]] = degree.get(e["b"], 0) + 1

    nodes = []
    for name in degree:  # only connected nodes — no lonely circles on a ring
        kn = kol_by_name.get(name)
        nodes.append({"name": name,
                      "reach": (kn or {}).get("reach", 0),
                      "affiliation": (kn or {}).get("affiliation", ""),
                      "kol": name in kol_by_name,
                      "degree": degree[name]})
    return nodes, edges


def _force_layout(names: list, edges: list, width: int, height: int, iterations: int = 90) -> dict:
    """Deterministic Fruchterman-Reingold layout → {name: (x, y)} within a margin.

    Deterministic (golden-angle seed, no RNG) so a given graph always lays out the
    same way — reproducible across runs and testable."""
    n = len(names)
    if n == 0:
        return {}
    margin = 60
    W, H = width - 2 * margin, height - 2 * margin
    if n == 1:
        return {names[0]: (margin + W / 2, margin + H / 2)}
    ga = math.pi * (3 - math.sqrt(5))  # golden angle → even initial spread
    pos = {}
    for i, nm in enumerate(names):
        rad = 0.45 * math.sqrt((i + 0.5) / n)
        pos[nm] = [0.5 * W + rad * W * math.cos(i * ga), 0.5 * H + rad * H * math.sin(i * ga)]
    adj = {}
    for e in edges:
        key = (e["a"], e["b"])
        adj[key] = adj.get(key, 0) + e.get("w", 1)
    k = 0.9 * math.sqrt((W * H) / n)  # ideal edge length
    t = W * 0.10
    cool = t / (iterations + 1)
    for _ in range(iterations):
        disp = {nm: [0.0, 0.0] for nm in names}
        for i in range(n):
            ni = names[i]
            for j in range(i + 1, n):
                nj = names[j]
                dx, dy = pos[ni][0] - pos[nj][0], pos[ni][1] - pos[nj][1]
                dist = math.hypot(dx, dy) or 0.01
                f = (k * k) / dist
                ux, uy = dx / dist, dy / dist
                disp[ni][0] += ux * f; disp[ni][1] += uy * f
                disp[nj][0] -= ux * f; disp[nj][1] -= uy * f
        for (a, b), w in adj.items():
            if a not in pos or b not in pos:
                continue
            dx, dy = pos[a][0] - pos[b][0], pos[a][1] - pos[b][1]
            dist = math.hypot(dx, dy) or 0.01
            f = (dist * dist) / k * (1 + 0.25 * math.log(1 + w))
            ux, uy = dx / dist, dy / dist
            disp[a][0] -= ux * f; disp[a][1] -= uy * f
            disp[b][0] += ux * f; disp[b][1] += uy * f
        for nm in names:
            dx, dy = disp[nm]
            d = math.hypot(dx, dy) or 0.01
            pos[nm][0] = min(W, max(0.0, pos[nm][0] + (dx / d) * min(d, t)))
            pos[nm][1] = min(H, max(0.0, pos[nm][1] + (dy / d) * min(d, t)))
        t = max(t - cool, W * 0.005)
    return {nm: (margin + pos[nm][0], margin + pos[nm][1]) for nm in names}


def render_network_svg(coauthor_edges: list, kol_nodes: list, width: int = 1080, height: int = 620) -> str:
    """Force-directed co-authorship graph as self-contained SVG. KOLs + their most
    connected external co-authors are nodes (sized by reach); edges are shared PubMed
    authorship (width = shared papers, dashed = external), labelled with the count.
    Node <title>/data-aff carry affiliations; edge <title> carries the relation."""
    nodes, edges = _select_network(coauthor_edges, kol_nodes)
    if not nodes:
        return ('<svg class="net-graph" width="100%" height="120" viewBox="0 0 600 120">'
                '<text x="300" y="62" text-anchor="middle" fill="#5c6774" font-size="14">'
                'No co-authorship connections among the top KOLs.</text></svg>')
    names = [n["name"] for n in nodes]
    pos = _force_layout(names, edges, width, height)
    maxreach = max((n["reach"] for n in nodes), default=1) or 1
    maxw = max((e["w"] for e in edges), default=1) or 1
    label_cut = max(2, maxw // 3)

    edge_svg = []
    for e in edges:
        (x1, y1), (x2, y2) = pos[e["a"]], pos[e["b"]]
        sw = 1.2 + 3.8 * (e["w"] / maxw)
        dash = ' stroke-dasharray="5 4"' if e["external"] else ""
        rel = f'{e["w"]} shared PubMed paper' + ("s" if e["w"] != 1 else "")
        edge_svg.append(
            f'<line class="net-edge" data-a="{_esc(e["a"])}" data-b="{_esc(e["b"])}" '
            f'x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
            f'stroke="#b6c2d1" stroke-width="{sw:.1f}"{dash}>'
            f'<title>{_esc(e["a"])} ↔ {_esc(e["b"])} — {rel}</title></line>')
        if e["w"] >= label_cut:
            edge_svg.append(
                f'<text class="net-edge-label" data-a="{_esc(e["a"])}" data-b="{_esc(e["b"])}" '
                f'x="{(x1 + x2) / 2:.1f}" y="{(y1 + y2) / 2:.1f}">{e["w"]}</text>')

    node_svg = []
    for n in nodes:
        x, y = pos[n["name"]]
        if n["kol"]:
            rad = 8 + 16 * (n["reach"] / maxreach)
            fill = PALETTE["accent"]
        else:
            rad = 5 + min(n["degree"], 5)
            fill = PALETTE["violet"]
        aff = str(n.get("affiliation", "") or ("" if n["kol"] else "external co-author"))
        title = _esc(n["name"]) + (f' — {_esc(aff)}' if aff else "") + \
            (f' · reach {n["reach"]}' if n["kol"] else "")
        g = (f'<g class="net-node{"" if n["kol"] else " ext"}" data-name="{_esc(n["name"])}" '
             f'data-aff="{_esc(aff)}" data-kol="{"1" if n["kol"] else "0"}" data-reach="{n["reach"]}" '
             f'data-x="{x:.1f}" data-y="{y:.1f}" transform="translate({x:.1f},{y:.1f})">'
             f'<circle r="{rad:.1f}" fill="{fill}" stroke="#fff" stroke-width="1.5">'
             f'<title>{title}</title></circle>')
        if n["kol"] or n["degree"] >= 2:  # label KOLs + bridge externals; hide singleton externals
            g += f'<text class="net-label" x="0" y="{-rad - 4:.1f}" text-anchor="middle">{_esc(n["name"])}</text>'
        node_svg.append(g + "</g>")

    return (f'<svg class="net-graph" viewBox="0 0 {width} {height}" '
            f'preserveAspectRatio="xMidYMid meet" width="100%" height="{height}">'
            f'<g class="net-zoom">{"".join(edge_svg)}{"".join(node_svg)}</g></svg>')


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


NETWORK_SCRIPT = """
<script>
(function(){
  var svg = document.querySelector('svg.net-graph');
  if (!svg || !svg.querySelector('.net-zoom')) return;
  var zoom = svg.querySelector('.net-zoom');
  var wrap = svg.closest('.network-svg-wrap');
  var tip = wrap ? wrap.querySelector('.net-tooltip') : null;
  var vb = svg.viewBox.baseVal;
  var scale = 1, tx = 0, ty = 0;
  function apply(){ zoom.setAttribute('transform', 'translate(' + tx + ',' + ty + ') scale(' + scale + ')'); }
  var panning = false, sx = 0, sy = 0;
  svg.addEventListener('mousedown', function(ev){ panning = true; sx = ev.clientX; sy = ev.clientY; svg.style.cursor = 'grabbing'; });
  window.addEventListener('mousemove', function(ev){ if (!panning) return; tx += ev.clientX - sx; ty += ev.clientY - sy; sx = ev.clientX; sy = ev.clientY; apply(); });
  window.addEventListener('mouseup', function(){ panning = false; svg.style.cursor = ''; });
  svg.addEventListener('wheel', function(ev){
    ev.preventDefault();
    var r = svg.getBoundingClientRect();
    var mx = (ev.clientX - r.left) / r.width * vb.width;
    var my = (ev.clientY - r.top) / r.height * vb.height;
    var f = ev.deltaY < 0 ? 1.12 : 0.89;
    var ns = Math.min(5, Math.max(0.25, scale * f));
    tx = mx - (mx - tx) * (ns / scale); ty = my - (my - ty) * (ns / scale); scale = ns; apply();
  }, { passive: false });
  function showTip(html, ev){
    if (!tip || !wrap) return;
    tip.innerHTML = html; tip.style.display = 'block';
    var r = wrap.getBoundingClientRect();
    tip.style.left = (ev.clientX - r.left + 14) + 'px';
    tip.style.top = (ev.clientY - r.top + 14) + 'px';
  }
  function hideTip(){ if (tip) tip.style.display = 'none'; }
  var nodes = svg.querySelectorAll('.net-node');
  for (var i = 0; i < nodes.length; i++){
    (function(g){
      g.addEventListener('mousemove', function(ev){
        var kol = g.getAttribute('data-kol') === '1';
        var aff = g.getAttribute('data-aff');
        var html = '<b>' + g.getAttribute('data-name') + '</b>' + (kol ? '<span class="tip-tag">KOL</span>' : '<span class="tip-tag ext">co-author</span>');
        if (kol) html += '<br>reach: ' + g.getAttribute('data-reach') + ' co-authors';
        if (aff) html += '<br>' + aff;
        showTip(html, ev);
      });
      g.addEventListener('mouseleave', hideTip);
    })(nodes[i]);
  }
  var edges = svg.querySelectorAll('.net-edge');
  for (var j = 0; j < edges.length; j++){
    (function(l){
      l.addEventListener('mousemove', function(ev){
        var t = l.querySelector('title');
        showTip(t ? t.textContent : '', ev);
      });
      l.addEventListener('mouseleave', hideTip);
    })(edges[j]);
  }
})();
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
      .wrap{{max-width:1360px;margin:0 auto;padding:28px 34px 64px}}
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
      .network-svg-wrap{{position:relative;overflow:hidden;margin:10px 0;
        border:1px solid {PALETTE['line']};border-radius:10px;background:{PALETTE['card']}}}
      svg.net-graph{{display:block;cursor:grab;touch-action:none;
        background:radial-gradient(circle at 1px 1px,#eef2f7 1px,transparent 0) 0 0/22px 22px}}
      .net-edge{{stroke:#b6c2d1}} .net-edge:hover{{stroke:{PALETTE['accent']}}}
      .net-edge-label{{fill:{PALETTE['muted']};font-size:10px;text-anchor:middle;pointer-events:none;
        paint-order:stroke;stroke:{PALETTE['card']};stroke-width:3px}}
      .net-node{{cursor:pointer}}
      .net-node:hover circle{{stroke:{PALETTE['ink']};stroke-width:2.5}}
      .net-label{{font-size:11px;fill:{PALETTE['ink']};pointer-events:none;font-weight:600;
        paint-order:stroke;stroke:{PALETTE['card']};stroke-width:3px}}
      .net-tooltip{{position:absolute;display:none;pointer-events:none;z-index:5;max-width:260px;
        background:{PALETTE['ink']};color:#fff;font-size:12px;line-height:1.45;padding:7px 10px;
        border-radius:7px;box-shadow:0 4px 14px rgba(0,0,0,.25)}}
      .net-tooltip .tip-tag{{font-size:10px;background:{PALETTE['accent']};padding:1px 6px;
        border-radius:8px;margin-left:4px}}
      .net-tooltip .tip-tag.ext{{background:{PALETTE['violet']}}}
      .net-legend{{display:flex;gap:18px;flex-wrap:wrap;font-size:12px;color:{PALETTE['muted']};padding:8px 4px 0}}
      .net-legend .dot{{display:inline-block;width:11px;height:11px;border-radius:50%;
        margin-right:5px;vertical-align:middle}}
      .net-hint{{font-size:11px;color:{PALETTE['muted']};padding:2px 4px 6px}}
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
    net_legend = (
        f'<div class="net-legend">'
        f'<span><span class="dot" style="background:{PALETTE["accent"]}"></span>KOL (size = co-author reach)</span>'
        f'<span><span class="dot" style="background:{PALETTE["violet"]}"></span>External co-author</span>'
        f'<span>line = shared PubMed papers (label = count · dashed = external)</span></div>'
        f'<div class="net-hint">Scroll to zoom · drag the background to pan · '
        f'hover a node for its institutions, or an edge for the shared-paper count.</div>')
    network_section = _splice_explainer(
        f'<h2>Collaboration network</h2>'
        f'<div class="network-svg-wrap">{render_network_svg(data.get("coauthor_edges", []), network_nodes)}'
        f'<div class="net-tooltip"></div></div>'
        f'{net_legend}'
        f'{render_network(data["coauthor_edges"], data["comention_edges"], top)}',
        "This is the co-authorship graph: each node is a KOL (blue, sized by how many distinct "
        "co-authors they have) or a frequently-shared external co-author (purple); each line is "
        "shared PubMed authorship, thicker and labelled with the number of shared papers, dashed "
        "when the co-author is external. Only connected KOLs appear here. The tables below list every edge.")
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
    body = header + "\n" + _render_sidebar(groups) + "\n" + TAB_SCRIPT + NETWORK_SCRIPT
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
