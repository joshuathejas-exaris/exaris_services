# Sidebar Navigation for the Competitor Intelligence Report — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Competitor Intelligence report's flat horizontal tab bar with a grouped left-sidebar navigation so the two conceptual levels (per-competitor vs. cross-cutting sections) are visually distinct.

**Architecture:** A single-file change to `05_generate_report.py`. The tab renderer `_render_tabs` (flat list) is replaced by `_render_sidebar` (grouped list → two-column nav + content). `build_report_a` groups its already-built panels under four headers. The `.tabs` CSS is swapped for sidebar CSS; the inline `showTab` script keeps its panel-toggling behavior and now highlights the active nav link. Panel content and all other helpers are untouched.

**Tech Stack:** Python 3, inline HTML/CSS/JS (no external assets), `pytest` via `conftest.load_stage`.

## Global Constraints

- Fully offline: inline CSS, inline SVG, inline vanilla JS — no external assets/CDNs/fonts/network.
- Degrades without JS: panels hidden ONLY via `body.js-tabs .panel { display: none }` which the inline script activates on `DOMContentLoaded`; with JS off, every panel is visible in document order and sidebar anchors (`href="#tab-…"`) still jump. No default CSS hides a panel; no `hidden` attribute.
- Content-neutral: panel builders (`_panel_overview`, `_panel_competitor`, `_panel_multi`, `_panel_top_voices`, `_panel_methodology`) and everything they emit are unchanged. Only nav presentation + CSS change.
- Group headers, English, fixed order: `OVERVIEW` / `BY COMPETITOR` / `ACROSS ALL DRUGS` / `ABOUT`. Headers are non-clickable labels; only items are links.
- Group membership: OVERVIEW → Insgesamt; BY COMPETITOR → one item per competitor summary (label = `competitor_heading`); ACROSS ALL DRUGS → Doctors weighing, Most active voices; ABOUT → Methodology.
- Desktop-optimized: sidebar `position: sticky; top` with fixed width ~220px; on ≤720px the two columns stack (sidebar full-width above content, not sticky) via a pure CSS media query.
- Preserve `build_report_a(synthesis, examples_per_section, timestamp)` signature and these output substrings (asserted by `test_reports_render_without_error`): "Competitor Intelligence Report", "Saxenda (Liraglutid)", "Cross-Competitor Insights", "Most discussed by distinct doctors"; and must NOT contain "Per-HCP Drill-Down".
- Test/interpreter: run from repo root with `.venv/bin/python -m pytest a_comp_hcp_communication/tests -q` (plain `python`/`python3` lack pytest).
- Commit trailer: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`

---

### Task 1: Sidebar navigation (replace flat tab bar)

**Files:**
- Modify: `a_comp_hcp_communication/05_generate_report.py` (CSS block ~303-312; `TAB_SCRIPT` ~315-330; replace `_render_tabs` ~333-347 with `_render_sidebar`; `build_report_a` section-assembly ~593-604)
- Test: `a_comp_hcp_communication/tests/test_stage05.py` (replace `test_report_a_has_tabs_and_panels`)

**Interfaces:**
- Consumes (all existing, unchanged): `tab_id`, `esc`, `_panel_overview`, `_panel_competitor`, `_panel_multi`, `_panel_top_voices`, `_panel_methodology`, `competitor_heading`, `competitor_distributions`, `cross_competitor_stats`, `claims_by_competitor`, `_visible_claims`, `footer_html`, `html_document`, `TAB_SCRIPT`.
- Produces: `_render_sidebar(groups: List[Tuple[str, List[Tuple[str, str]]]]) -> str` — grouped nav + content HTML; replaces `_render_tabs`. `build_report_a` keeps its signature.

- [ ] **Step 1: Write the failing test**

In `a_comp_hcp_communication/tests/test_stage05.py`, REPLACE the whole `test_report_a_has_tabs_and_panels` function (currently at line 142) with:

```python
def test_report_a_has_sidebar_and_panels():
    a = mod.build_report_a(SYNTH, 15, "2026-07-03 12:00:00")
    # progressive-enhancement scaffolding preserved
    assert "function showTab" in a
    assert "js-tabs" in a
    # two-column sidebar layout markers
    assert 'class="layout"' in a
    assert 'class="sidebar"' in a
    assert 'class="content"' in a
    # grouped, non-clickable section headers, in order
    import re as _re
    labels = _re.findall(r'nav-group-label">([^<]+)<', a)
    assert labels == ["OVERVIEW", "BY COMPETITOR", "ACROSS ALL DRUGS", "ABOUT"]
    # items are nav links with matching panel ids
    assert 'class="nav-item' in a
    assert 'href="#tab-insgesamt"' in a and 'id="tab-insgesamt"' in a
    assert 'href="#tab-saxenda-liraglutid"' in a and 'id="tab-saxenda-liraglutid"' in a
    # all expected item labels present
    for label in ("Insgesamt", "Doctors weighing", "Most active voices", "Methodology"):
        assert label in a
    # overview content + substrings the render test relies on
    assert "Overall sentiment" in a
    assert "Cross-Competitor Insights" in a
    assert "Most discussed by distinct doctors" in a
    # the old flat tab bar is gone
    assert 'class="tabs"' not in a
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /d/Dev/exaris_services && .venv/bin/python -m pytest a_comp_hcp_communication/tests/test_stage05.py::test_report_a_has_sidebar_and_panels -v`
Expected: FAIL — the current output still has `class="tabs"` and no `class="layout"`/`nav-group-label`.

- [ ] **Step 3: Replace the tab CSS with sidebar CSS**

In `a_comp_hcp_communication/05_generate_report.py`, REPLACE these lines in `BASE_CSS` (currently lines 303-312):

```css
.tabs { display: flex; flex-wrap: wrap; gap: 4px; border-bottom: 2px solid #e5e9ee;
        margin: 24px 0 8px; }
.tab { padding: 8px 14px; border: 1px solid transparent; border-bottom: none;
       border-radius: 8px 8px 0 0; background: transparent; color: #6b7280;
       font-size: 14px; font-weight: 600; cursor: pointer; text-decoration: none; }
.tab:hover { color: #1565c0; }
.tab.active { background: #fcfdfe; border-color: #e5e9ee; color: #1565c0;
              margin-bottom: -2px; }
body.js-tabs .panel { display: none; }
body.js-tabs .panel.active { display: block; }
```

with:

```css
.layout { display: flex; gap: 28px; align-items: flex-start; margin: 24px 0 8px; }
.sidebar { flex: 0 0 220px; position: sticky; top: 16px; align-self: flex-start; }
.content { flex: 1 1 auto; min-width: 0; }
.nav-group-label { text-transform: uppercase; letter-spacing: .6px; font-size: 11px;
                   font-weight: 700; color: #9aa5b1; margin: 18px 0 6px; }
.nav-group-label:first-child { margin-top: 0; }
.nav-item { display: block; padding: 6px 10px; margin: 2px 0; border-radius: 6px;
            color: #374151; font-size: 14px; text-decoration: none;
            border-left: 3px solid transparent; }
.nav-item:hover { background: #f3f6f9; color: #1565c0; }
.nav-item.active { background: #eef4fb; color: #1565c0; font-weight: 600;
                   border-left-color: #1565c0; }
body.js-tabs .panel { display: none; }
body.js-tabs .panel.active { display: block; }
@media (max-width: 720px) {
  .layout { flex-direction: column; gap: 8px; }
  .sidebar { position: static; flex-basis: auto; width: 100%; }
}
```

- [ ] **Step 4: Update `showTab` to highlight the active nav item**

In `a_comp_hcp_communication/05_generate_report.py`, REPLACE the `TAB_SCRIPT` constant (currently lines 315-330) with:

```python
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
```

- [ ] **Step 5: Replace `_render_tabs` with `_render_sidebar`**

In `a_comp_hcp_communication/05_generate_report.py`, REPLACE the entire `_render_tabs` function (currently lines 333-347) with:

```python
def _render_sidebar(groups: "List[Tuple[str, List[Tuple[str, str]]]]") -> str:
    """groups: list of (group_header, [(item_label, panel_html), ...]).

    Renders a left nav sidebar (grouped, non-clickable headers) beside a content pane
    of panels. The first item overall is active on load. Degrades to a full-scroll page
    when JS is disabled (no panel is hidden by default CSS). Empty groups are skipped."""
    nav = ['<nav class="sidebar" role="tablist" aria-label="Report sections">']
    panels = []
    first = True
    for group_label, items in groups:
        if not items:
            continue
        nav.append(f'<div class="nav-group-label">{esc(group_label)}</div>')
        for item_label, body in items:
            tid = tab_id(item_label)
            active = " active" if first else ""
            current = ' aria-current="page"' if first else ""
            nav.append(f'<a class="nav-item{active}" role="tab" id="{tid}-btn" '
                       f'href="#{tid}" aria-controls="{tid}"{current} '
                       f'onclick="return showTab(\'{tid}\')">{esc(item_label)}</a>')
            panels.append(f'<section class="panel{active}" role="tabpanel" id="{tid}" '
                          f'aria-labelledby="{tid}-btn">\n{body}\n</section>')
            first = False
    nav.append("</nav>")
    content = '<main class="content">\n' + "\n".join(panels) + "\n</main>"
    return '<div class="layout">\n' + "\n".join(nav) + "\n" + content + "\n</div>"
```

- [ ] **Step 6: Group the sections in `build_report_a`**

In `a_comp_hcp_communication/05_generate_report.py`, REPLACE the section-assembly block in `build_report_a` — from the `# Tabs` comment through the `return html_document(...)` line (currently lines 593-605) — with:

```python
    # Sidebar-grouped sections: OVERVIEW / BY COMPETITOR / ACROSS ALL DRUGS / ABOUT
    competitor_items = []
    for cs in summaries:
        label = competitor_heading(cs.get("competitor", ""), cs.get("generic", ""))
        competitor_items.append(
            (label, _panel_competitor(cs, by_comp, dist_by_comp, examples_per_section)))
    groups: "List[Tuple[str, List[Tuple[str, str]]]]" = [
        ("OVERVIEW", [("Insgesamt",
                       _panel_overview(summaries, claims, overall, stats, dist_by_comp))]),
        ("BY COMPETITOR", competitor_items),
        ("ACROSS ALL DRUGS", [
            ("Doctors weighing", _panel_multi(stats, examples_per_section)),
            ("Most active voices", _panel_top_voices(stats))]),
        ("ABOUT", [("Methodology", _panel_methodology())]),
    ]

    body = "\n".join(head) + "\n" + _render_sidebar(groups) + "\n" + TAB_SCRIPT + "\n" \
        + footer_html(timestamp)
    return html_document("Competitor Intelligence Report", body)
```

(Leave the `head` block above it unchanged. Optionally update the `# Header (stays above the tab bar...)` comment on the head block to `# Header (stays above the sidebar, always visible)` — cosmetic.)

- [ ] **Step 7: Run the focused test to verify it passes**

Run: `cd /d/Dev/exaris_services && .venv/bin/python -m pytest a_comp_hcp_communication/tests/test_stage05.py::test_report_a_has_sidebar_and_panels -v`
Expected: PASS

- [ ] **Step 8: Run the full suite**

Run: `cd /d/Dev/exaris_services && .venv/bin/python -m pytest a_comp_hcp_communication/tests -q`
Expected: PASS — all tests (including `test_reports_render_without_error`, whose substrings are unaffected). Report the true count.

- [ ] **Step 9: Manual verification — regenerate on existing data**

Run:
```bash
cd /d/Dev/exaris_services/a_comp_hcp_communication && ../.venv/bin/python 05_generate_report.py --force
```
Expected: writes a new `results/report_<ts>.html` without error. Structural check:
```bash
cd /d/Dev/exaris_services/a_comp_hcp_communication && ../.venv/bin/python - <<'PY'
import glob
t = open(sorted(glob.glob("results/report_*.html"))[-1], encoding="utf-8").read()
assert 'class="layout"' in t and 'class="sidebar"' in t and 'class="content"' in t
for h in ("OVERVIEW", "BY COMPETITOR", "ACROSS ALL DRUGS", "ABOUT"):
    assert f'nav-group-label">{h}<' in t, f"missing group header {h}"
assert 'class="tabs"' not in t
assert "function showTab" in t and "js-tabs" in t
print("sidebar structural checks passed")
PY
```
Expected: prints "sidebar structural checks passed". If a browser is available, also eyeball: clicking a sidebar item switches the content pane; the sidebar stays put while content scrolls (desktop); narrowing the window below ~720px stacks the sidebar above the content; with JS disabled all sections are visible in order. (No browser in CI — the structural check is the automated gate; the visual check is best-effort.)

- [ ] **Step 10: Commit**

```bash
git add a_comp_hcp_communication/05_generate_report.py a_comp_hcp_communication/tests/test_stage05.py
git commit -m "feat(stage05): grouped sidebar navigation for the report

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage:**
- Left sidebar replacing the flat tab bar → Steps 3, 5, 6. ✓
- Four English group headers, fixed order, non-clickable → `_render_sidebar` + `build_report_a` groups (Steps 5, 6); asserted by the test (Step 1). ✓
- Group membership (Overview / By competitor / Across all drugs / About) → Step 6. ✓
- Desktop sticky sidebar ~220px + ≤720px stacking → CSS in Step 3. ✓
- No-JS degradation preserved (only `body.js-tabs` hides panels; anchors jump) → CSS retains the `body.js-tabs` rules only; `_render_sidebar` uses `<a href="#tid">`; script adds `js-tabs` on load. ✓
- Offline preserved → all inline; no assets added. ✓
- Content-neutral (panels unchanged) → only nav/CSS/script touched; panel builders untouched. ✓
- Active nav highlight + `aria-current` → Step 4 script. ✓
- Preserve `build_report_a` signature + required substrings → signature unchanged (Step 6); `test_reports_render_without_error` substrings emitted by unchanged panels; new test re-asserts them. ✓
- Testing (layout markers, group headers, item↔panel ids, no-JS, substrings) → Step 1 test; full suite Step 8; manual regen Step 9. ✓

**Placeholder scan:** No TBD/TODO; every code step has complete code; every command has expected output. ✓

**Type consistency:** `_render_sidebar(groups: List[Tuple[str, List[Tuple[str, str]]]])` defined in Step 5 and called with exactly that shape in Step 6. `showTab` targets `.nav-item` (Step 4) which `_render_sidebar` emits (Step 5). `tab_id`/`esc`/panel builders consumed with their existing signatures. The removed `_render_tabs` has no remaining callers (only `build_report_a` used it, updated in Step 6) and no test references it. `List`/`Tuple` already imported in the file. ✓
