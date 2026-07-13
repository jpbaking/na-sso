# oneauth/templates

## Purpose

Jinja page templates for the One Auth administrative interface.

## Ownership

Owns page structure and presentation. Route behavior and template context are owned by the corresponding Python modules in the parent folder; design tokens and component classes are owned by `../static/design/`.

## Local Contracts

- Every page extends `base.html` and keeps the standard favicon and `/design/styles.css` plus `/design/components.css` links.
- Follow the lazyway.io design rules from the root contract: documented classes first, no gradients, and exactly one amber accent per page.
- Keep navigation consistent across authenticated pages: Users, Targets, Audit, and Sign out.
- Render operator-visible connector errors safely; never render credentials or decrypted pending secrets.
- Target credential forms are write-only: show readiness and safe probe detail, never stored values; SAVE persists and immediately probes the replacement as one operation.
- Render the protected root as `SUPERADMIN` with `N/A` target cells and no live-sync cell attributes.
- Pending and retrying target cells subscribe to authenticated `/events/sync` updates through the shared application shell; construct event-derived content with DOM text APIs.

## Verification

- Run `.venv/bin/pytest -q` from the repository root after template changes.
- Check rendered pages for one amber accent, valid navigation, and the required design assets.

## Feature Map

- **Application shell and live sync** — Shared metadata, navigation, static design links, content slot, footer, and SSE-driven target-cell updates. Start: `base.html`.
- **Admin login** — Renders credential input and authentication errors. Start: `login.html`. Files: `base.html`.
- **User lifecycle management** — Renders registry-driven assignment matrices, select-all controls, live password compliance/generation, deferred/retired states, and lifecycle/retry actions. Start: `users.html`. Files: `user_form.html`, `base.html`.
- **Target dashboard** — Shows connector reachability and the user sync matrix. Start: `status.html`. Files: `base.html`.
- **Target onboarding** — Collects write-only API/admin/SSH management credentials, saves and probes them in one action, and displays one combined configuration/authentication status with safe probe detail as a tooltip. Start: `status.html`.
- **Audit history** — Shows administrative and synchronization events. Start: `audit.html`. Files: `base.html`.

## Child DOX Index

- (none)
