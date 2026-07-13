---
name: lazyway-io-design
description: Build or edit web pages and UI in the lazyway.io design system (IBM Plex, brand blue #12279E, one amber accent, design/styles.css + design/components.css). Use when creating or styling HTML pages, React apps/components, landing pages, dashboards, blogs, docs sites, single-function app UIs (url-shortener style), charts, modals, forms, tables, tiles, date/time pickers, or favicons in a project that uses this kit — or when the user mentions lazyway design, the design kit, lw components, or the brand style guide.
---

# lazyway-io-design — how to build pages

Follow these instructions literally. Do not improvise styling.

## Step 0 — get the kit (works in any workspace)

Kit repo: **https://github.com/jpbaking/lazyway-io-design** (branch `main`).

The project must serve the kit's `design/` folder (usually `public/design/`, so URLs are `/design/...`). Check that `design/styles.css` AND `design/components.css` exist there. If they do, skip ahead. If not, obtain the kit — NEVER recreate its files by hand:

1. **Already on disk?** Look for a `lazyway-io-design` folder in the workspace, its parent directory, or `~/.cache/lazyway-io-design`.
2. **Otherwise fetch it** (pick whichever tool exists):
   ```sh
   git clone --depth 1 https://github.com/jpbaking/lazyway-io-design.git ~/.cache/lazyway-io-design
   # or, without git:
   mkdir -p ~/.cache/lazyway-io-design && curl -fsSL https://github.com/jpbaking/lazyway-io-design/archive/refs/heads/main.tar.gz | tar -xz -C ~/.cache/lazyway-io-design --strip-components=1
   ```
3. **Copy into the project:** `cp -r <kit>/design <webroot>/design` (for Vite/Astro/React the webroot is `public/`). If the project has no favicons yet, also `cp <kit>/design/assets/favicons/* <webroot>/` (or generate a branded set with `<kit>/scripts/make-favicons.sh logo.svg <webroot>/ "Site Name"`).

`<kit>` below means that folder. It also contains: `demos/` (full example pages to copy from), `components/react/` (React components), `starter/react/` and `starter/astro/` (new-project starters), `CHEATSHEET.md` and `README.md` (the full reference).

## Absolute rules

1. NEVER write custom CSS when a class below does the job. If custom CSS is truly unavoidable, use ONLY `var(--…)` tokens — never hardcode a color, px size, or shadow.
2. ONE amber element per page: a `.kicker`, OR one `.nav-link.active`, OR one `.stat-highlight`, OR one `.alert-warn`. Pick exactly one. (Status badges and chart colors don't count.)
3. Backgrounds: white or solid brand blue only. NO gradients, ever.
4. Uppercase mono text (`.mono-label`, `.kicker`, `.tag`, `.label`) is for short labels only — never body text.
5. Every page MUST have the three favicon links (see templates). If the favicon files don't exist, copy or generate them (Step 0.3).
6. Logos: rounded blue tile `#12279E`, white linework, exactly ONE amber `#D9821F` dot.
7. Copy tone: plain, understated, British spelling (organise, centre), no exclamation marks.

## Step 1 — start from a template

Bundled with this skill (copy, then edit text only):
- `templates/page.html` — standard site page (nav + hero + section + footer)
- `templates/app.html` — single-function app (blue band + action card, the url-shortener pattern)

Every page head must keep: the 3 favicon links, `/design/styles.css`, `/design/components.css`. Load `/design/components.js` before `</body>` only if the page uses modals/tabs/pickers; `/design/charts.js` only for charts.

## Step 2 — build with these classes only

- Layout: `.container` (1100px) · `.container-narrow` (720px) · `.section` · grids `.grid-2/-3/-4`, `.grid-cards`, `.grid-stats`
- Headings: `.kicker` (amber eyebrow) · `.headline` · `.lead`
- Section head: `.section-head` > `.section-kicker` + `.section-title` + optional `.section-all` link
- Buttons: `.btn btn-primary` / `btn-secondary` / `btn-quiet` / `btn-danger`, size `btn-sm`; on blue: `btn-on-blue` / `btn-outline-on-blue`; small `.copy-btn`
- Nav: `.nav-wrap` > `.nav container` > `.brand` (img + `.brand-name`) + `.nav-links` > `.nav-link` (`.active` = the amber)
- Footer: `.footer` > `.footer-inner container` > `.foot-brand` / `.foot-links` (`.foot-link`) / `.foot-copy`
- Hero: `.hero` (or `.hero-invert` for blue) > `.hero-mesh` img (`/design/assets/mesh-blue.svg`, aria-hidden) + `.hero-inner container` > kicker/headline/lead/`.hero-actions`
- Cards: `.card` > `.card-meta` + `.card-title` + `.card-desc`; clickable = `a.card.tile`
- Stats: `.stat` > `.stat-value` + `.stat-label` + optional `.stat-delta up/down`; ONE `.stat-highlight` max
- Tags/badges: `.tag` (`.active`), `.tag-row`; `.badge badge-live/-pilot/-shipped/-planned/-danger`
- Table: `.table-wrap` > `table.table`; numeric cells get `.num`
- Key/value list: `.data-list` > `.data-row` > `.data-key` + `.data-value`
- Forms: `.field` > `.label` + `.input`/`.select`/`.textarea`; `.field-hint`, `.field-error` + `.input-error`; `.search-input`; `.checkbox`, `.radio`; `.switch` (hidden input + `.switch-track`)
- Alerts: `.alert alert-info/-success/-warn/-danger` > `.alert-title` + text
- Modal (components.js): trigger `data-modal-open="#id"`; `<dialog id class="modal">` > `.modal-head` (`.modal-title`, `.modal-close` with `data-modal-close`) + `.modal-body` + `.modal-foot`
- Tabs (components.js): `[data-tabs]` > `.tab-list` > `.tab` with `data-tab-target="#panel"` (+`.active` on first) + `.tab-panel` divs (`hidden` on all but first)
- Pickers (components.js): `<input class="input" data-datepicker>` → `YYYY-MM-DD`; `<input class="input" data-timepicker="15">` → `HH:MM`
- Progress: `.progress` > `.progress-fill` (inline width %; `.warn`/`.danger`); label row `.progress-label`
- Long-form/markdown output: wrap in `.prose`
- Blog list item: `.post-card` > `.post-meta` (`.post-date`, `.post-tag`) + `.post-title` + `.post-desc`
- App shell (see templates/app.html): `.app-page` body > `.app-hero` (`.app-brand`+`.app-wordmark`, `.app-kicker`, `.app-title`, `.app-tagline`) + `.action-card` (`.input-row`, `.option-row`, `.result-block` > `.result-row` > `.result-link` + `.copy-btn`) + `.item-list` (`.item-list-head`, `.item-count`, `.item-card` > `.item-title`/`.item-sub`/`.item-meta`) + `.app-foot`
- Utilities: `.mono-label`, `.muted`, `.center`, `.stack-1..4`, `.spinner` (in a blue button)

## Charts (only with /design/charts.js)

Container: `<div class="chart"><h3 class="chart-title">T</h3><p class="chart-sub">S</p><div id="c1"></div></div>`

```js
lwCharts.bar("#c1",  { labels: ["Q1","Q2"], series: [{ name: "Plan", values: [40,55] }] });
lwCharts.line("#c1", { labels: [...], series: [{ name, values }], area: true }); // area: 1 series only
lwCharts.donut("#c1",{ slices: [{ label: "API", value: 41 }], totalLabel: "total" });
lwCharts.sparkline("#c1", { values: [3,5,4,7] }); // inside a .stat
```

NEVER pass colors (auto from validated `--chart-1..5`). Max 5 series. Never two y-axes. Legends/tooltips are automatic.

## Diagrams (mermaid via CDN)

`theme: "base"`, themeVariables: primaryColor `#F4F5FB`, primaryBorderColor `#12279E`, primaryTextColor `#16182E`, lineColor `#61657F`, secondaryColor `#FDF6EC`, tertiaryColor `#FFFFFF`, fontFamily `IBM Plex Sans, sans-serif`. Put the diagram in a `.card`.

## React projects

Use the kit's React components (copy `<kit>/components/react/*.tsx` into `src/components/`): `Modal`, `Tabs`, `DatePicker`, `TimePicker`, `DataTable`, `StatTile`, `Chart`. Same two stylesheets required. `Chart` needs `/design/charts.js` in index.html. NEVER load `/design/components.js` in a React app. New app: start from `<kit>/starter/react/` (bootstrap steps in its AGENTS.md).

## Checklist before finishing any page

- [ ] 3 favicon links + 2 stylesheet links present; favicon files exist
- [ ] Exactly one amber element
- [ ] No custom CSS that a documented class could replace; no hardcoded hex/px
- [ ] No gradients; no pill radii; mono uppercase only on labels
- [ ] Charts: no custom colors, ≤5 series