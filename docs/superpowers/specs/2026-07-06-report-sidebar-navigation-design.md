# Design — Sidebar navigation for the Competitor Intelligence report

**Service:** `a_comp_hcp_communication` (Service 1.2 — Competitive HCP Communication Monitoring)
**Date:** 2026-07-06
**Author:** Joshua (with Claude)

## Background

The Competitor Intelligence report (Report A, from `05_generate_report.py`) currently
uses a single horizontal tab bar:

```
[ Insgesamt | Saxenda  Mounjaro  Rybelsus  Wegovy | Doctors weighing  Most active voices  Methodology ]
```

The items live at two conceptual levels — one tab **per competitor** vs. **cross-cutting /
whole-report** sections — but they sit as peers in one flat row. When the reader is inside
a competitor tab, the trailing three (Doctors weighing / Most active voices / Methodology)
read as if they were sub-views of that competitor. This is misleading.

## Goal

Replace the flat horizontal tab bar with a **left sidebar navigation** whose items are
grouped under non-clickable section headers, so the hierarchy is explicit and no group can
be mistaken for a competitor sub-view. Optimize for desktop viewing.

## Non-goals

- No change to panel **content** — the panel builders (`_panel_overview`,
  `_panel_competitor`, `_panel_multi`, `_panel_top_voices`, `_panel_methodology`) and every
  chart/table/statement they emit stay exactly as they are.
- No change to the COI filter, the sentiment logic, the Excel export, or Stages 01–04.
- The Plain-Language Guide (Report B) and Technical Doc (Report C) are untouched.
- No mobile-first redesign; narrow screens only need to not break (graceful stack).

## Layout

Header (title / subtitle / generated-at / legend) stays full-width at the top. Below it a
two-column area: left nav sidebar, right content pane holding the panels. Footer stays
full-width at the bottom.

```
┌─────────────────────────────────────────────────────────────┐
│  Competitor Intelligence Report      [header, full width]     │
├──────────────────────┬────────────────────────────────────────┤
│ OVERVIEW             │                                        │
│  • Insgesamt         │   selected section's panel renders     │
│ BY COMPETITOR        │   here (charts, statements, tables)    │
│  • Saxenda           │                                        │
│  • Mounjaro          │                                        │
│  • Rybelsus          │                                        │
│  • Wegovy            │                                        │
│ ACROSS ALL DRUGS     │                                        │
│  • Doctors weighing  │                                        │
│  • Most active voices│                                        │
│ ABOUT                │                                        │
│  • Methodology       │                                        │
├──────────────────────┴────────────────────────────────────────┤
│  Generated … · Service 1.2 …        [footer, full width]      │
└─────────────────────────────────────────────────────────────┘
```

### Groups (English headers, fixed order)

| Group header      | Items                                            |
|-------------------|--------------------------------------------------|
| `OVERVIEW`        | Insgesamt                                        |
| `BY COMPETITOR`   | one item per competitor summary (dynamic; label = `competitor_heading`, e.g. "Saxenda (Liraglutid)") |
| `ACROSS ALL DRUGS`| Doctors weighing · Most active voices            |
| `ABOUT`           | Methodology                                      |

Group headers are non-clickable labels. Only the bullet items are links. The item labels,
their `tab_id`s, and the panels they map to are unchanged from today — only the nav
presentation and grouping change.

## Behavior

- **Desktop:** sidebar is `position: sticky; top: 0` with a fixed width (~230px); it stays
  visible while the content pane scrolls. Content pane takes the remaining width.
- **Active state:** the selected item's link is highlighted and carries `aria-current="page"`;
  its panel is the only one shown (JS on).
- **Narrow screens (≤ ~720px):** the two columns stack — sidebar becomes a full-width block
  above the content pane (`flex-direction: column`), sidebar not sticky. No JS involved in
  the switch; pure CSS media query.
- **No-JS degradation (preserved):** panels are hidden only via `body.js-tabs .panel {
  display: none }`, which the inline script activates on `DOMContentLoaded`. With JS off, the
  class never lands, every panel is visible in document order, and the sidebar anchors
  (`href="#tab-…"`) still jump to their sections. Nothing is ever permanently hidden.
- **Offline (preserved):** inline CSS + inline SVG + inline vanilla JS only; no external
  assets, fonts, CDNs, or network calls.

## Code impact (Stage 05 only: `05_generate_report.py`)

- **Replace** `_render_tabs(sections)` with a sidebar renderer that takes **grouped**
  sections and emits the two-column nav + panels:
  `_render_sidebar(groups: List[Tuple[str, List[Tuple[str, str]]]]) -> str`
  where each group is `(group_header, [(item_label, panel_html), …])`.
  - Emits `<div class="layout">` containing `<nav class="sidebar">…</nav>` and
    `<main class="content">…panels…</main>`.
  - For each group: a `<div class="nav-group-label">HEADER</div>` followed by its item
    anchors (`<a class="nav-item" href="#{tid}" onclick="return showTab('{tid}')">`).
  - Panels are `<section class="panel" id="{tid}">…</section>`, first item active on load.
- **`build_report_a`** assembles the four groups (Overview / By competitor / Across all
  drugs / About) instead of the current flat `sections` list, then calls `_render_sidebar`.
  The `dist_by_comp = competitor_distributions(claims)` wiring and all panel-builder calls
  are unchanged; only how their results are grouped for the nav changes.
- **CSS:** remove the `.tabs` / `.tab` rules; add `.layout` (flex row), `.sidebar` (sticky,
  fixed width, section labels + items), `.nav-group-label`, `.nav-item` (+ active state),
  `.content` (flex:1), and a `@media (max-width: 720px)` block that stacks the columns. The
  `body.js-tabs .panel { display:none }` / `.panel.active { display:block }` rules stay.
- **`TAB_SCRIPT`:** `showTab(id)` keeps toggling the active panel; it now also toggles the
  active class on `.nav-item` links (matching `href === '#'+id`) and sets/clears
  `aria-current`. `DOMContentLoaded` still adds `js-tabs` and activates the first panel.
- `tab_id`, `overall_distribution`, `competitor_distributions`, `_visible_claims`, and every
  `_panel_*` builder are unchanged.

## Testing

Unit tests via the existing `conftest.load_stage` pattern (no AWS/Snowflake/ONNX):

- Rendered Report A contains a `.sidebar` and a `.content` region (two-column layout markers).
- The four group headers (`OVERVIEW`, `BY COMPETITOR`, `ACROSS ALL DRUGS`, `ABOUT`) appear
  as non-link labels (`nav-group-label`), in order.
- Each expected item still appears as a nav link with a matching panel id
  (`href="#tab-insgesamt"` ↔ `id="tab-insgesamt"`; likewise a competitor, e.g.
  `tab-saxenda-liraglutid`).
- The existing `test_report_a_has_tabs_and_panels` assertions are updated to the sidebar
  markup where they referenced `.tabs`; the no-JS guarantee assertions (`js-tabs`,
  `function showTab`) remain.
- The substrings the render test relies on ("Competitor Intelligence Report",
  "Saxenda (Liraglutid)", "Cross-Competitor Insights", "Most discussed by distinct doctors";
  absence of "Per-HCP Drill-Down") still hold.
- **Manual:** regenerate on existing data (`05_generate_report.py --force`); confirm the
  sidebar renders, links switch sections, the sidebar stays put while content scrolls
  (desktop), the layout stacks on a narrow window, and with JS disabled all sections show.

## Files touched

| File | Change |
|------|--------|
| `05_generate_report.py` | `_render_tabs` → `_render_sidebar` (grouped); `build_report_a` groups the sections; sidebar CSS replaces tab CSS; `showTab` updates nav-item active + `aria-current` |
| `tests/test_stage05.py` | Update the layout test to sidebar markup + group headers; keep no-JS and substring assertions |

## Open questions

None. Group headers are English (`OVERVIEW / BY COMPETITOR / ACROSS ALL DRUGS / ABOUT`),
sidebar width ~230px, narrow-screen breakpoint ~720px — all finalised during implementation.
