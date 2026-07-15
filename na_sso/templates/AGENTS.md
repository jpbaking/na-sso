# na_sso/templates

## Purpose

Jinja page templates for the NA-SSO administrative interface.

## Ownership

Owns page structure and presentation. Route behavior and template context are owned by the corresponding Python modules in the parent folder; design tokens and component classes are owned by `../static/design/`.

## Local Contracts

- Every page extends `base.html` and keeps the standard favicon and `/design/styles.css` plus `/design/components.css` links.
- The shared `site-page` shell fills the viewport and lets `main` grow so the footer stays at the bottom on short screens without overlapping long content.
- Follow the lazyway.io design rules from the root contract: documented classes first, no gradients, and exactly one amber accent per page.
- Keep navigation consistent across authenticated pages: Users, Targets, Audit, and Sign out.
- Render operator-visible connector errors safely; never render credentials or decrypted pending secrets.
- Target credential forms are write-only: show readiness and safe probe detail, never stored values; load every form collapsed behind an accessible right-aligned chevron, group disclosures so at most one target is open, use generously spaced zebra-striped rows with right-aligned SAVE actions, and make SAVE persist and immediately probe the replacement as one operation. SSH authentication selection shows and requires only password, private key, or both fields for the selected mode.
- On the Targets page, render target names at the larger standard body-text size while retaining the normal data-key colour and weight.
- Keep user synchronization state on the Users management page; the Targets page owns target configuration and health only.
- Render the protected root as `SUPERADMIN` with `N/A` target cells and no live-sync cell attributes.
- The new-user form states and mirrors the shared username contract: lowercase letters, digits, underscores, dots, and hyphens, with every separator prohibited at either edge.
- Generated user passwords remain masked in the form and open a one-time modal with a truncated preview, a full-value clipboard action, and a warning; closing the modal clears its plaintext copy source and preview.
- Pending and retrying target cells subscribe to authenticated `/events/sync` updates through the shared application shell; construct event-derived content with DOM text APIs.

## Verification

- Run `.venv/bin/pytest -q` from the repository root after template changes.
- Check rendered pages for one amber accent, valid navigation, and the required design assets.

## Feature Map

- **Application shell and live sync** — Shared metadata, navigation, static design links, content slot, footer, and SSE-driven target-cell updates. Start: `base.html`.
- **Admin login** — Renders credential input and authentication errors. Start: `login.html`. Files: `base.html`.
- **User lifecycle management** — Renders registry-driven assignment matrices, select-all controls, live password compliance, one-time modal password generation/copy, deferred/retired states, and lifecycle/retry actions. Start: `users.html`. Files: `user_form.html`, `base.html`.
- **Target dashboard** — Shows target configuration and connector health without duplicating the Users management matrix. Start: `status.html`. Files: `base.html`.
- **Target onboarding** — Collects write-only API/admin/SSH management credentials in readiness-aware disclosure forms, dynamically showing password, private-key, or both SSH fields; saves and probes them in one action, and displays one combined configuration/authentication status with safe probe detail as a tooltip. Start: `status.html`.
- **Audit history** — Shows administrative and synchronization events. Start: `audit.html`. Files: `base.html`.

## Child DOX Index

- (none)
