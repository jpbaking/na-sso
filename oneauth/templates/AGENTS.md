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

## Verification

- Run `.venv/bin/pytest -q` from the repository root after template changes.
- Check rendered pages for one amber accent, valid navigation, and the required design assets.

## Feature Map

- **Application shell** — Shared metadata, navigation, static design links, content slot, and footer. Start: `base.html`.
- **Admin login** — Renders credential input and authentication errors. Start: `login.html`. Files: `base.html`.
- **User lifecycle management** — Lists target and retry states and provides create/edit/password/status/soft-delete/restore/purge/manual-retry controls. Start: `users.html`. Files: `user_form.html`, `base.html`.
- **Target dashboard** — Shows connector reachability and the user sync matrix. Start: `status.html`. Files: `base.html`.
- **Audit history** — Shows administrative and synchronization events. Start: `audit.html`. Files: `base.html`.

## Child DOX Index

- (none)
