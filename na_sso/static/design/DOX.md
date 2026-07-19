# na_sso/static/design

## Purpose

Shared design-system bundle: tokens, component styles/scripts, chart helpers, and brand assets served to the admin UI.

## Ownership

Owns everything under this folder. App-specific styling stays in `../app.css`; templates that consume these assets are governed by `../../DOX.md`.

## Local Contracts

- Treat this bundle as a shared design system: change tokens in `tokens/`, not by hardcoding values in `styles.css`/`components.css`.
- Keep assets self-contained — no external CDN references.

## Feature Map

- **Design tokens & styles** — variables and base styling. Start: `tokens/`. Files: `styles.css`, `components.css`.
- **UI behaviors & charts** — shared component scripts and chart rendering. Start: `components.js`. Files: `charts.js`.
- **Brand assets** — logos and favicons. Start: `assets/`.

## Child DOX Index

- (none)
