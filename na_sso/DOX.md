# na_sso

## Purpose

The NA-SSO application package: FastAPI web console, domain services, automation API, CLI, persistence, and background workers.

## Ownership

Owns all Python modules, templates, and static assets under `na_sso/`. Connector adapters are governed by `connectors/DOX.md`; the demo mock target app by `mock_targets/DOX.md`; the shared design bundle by `static/design/DOX.md`. Tests live outside this package under `tests/`.

## Local Contracts

- Startup wiring (routes, workers, static mounts) lives in `main.py`; configuration is strict YAML + env via `config.py`.
- Persistence is SQLAlchemy models in `models.py` with SQLite via `db.py`; secrets are stored encrypted, never in plaintext.
- Lifecycle changes go through typed commands in `lifecycle.py` and durable operations in `operations.py` — never mutate targets outside `sync.py` fan-out.
- Named SSH credentials live in `user_ssh_keys`; `managed_users.ssh_public_key` is only a compatibility mirror.
- Passwords are transient: never stored, logged, or placed on command lines.
- `templates/` is the server-rendered admin UI (Jinja2); shared nav in `_admin_nav.html`, base layout in `base.html`, one-time mutation feedback via `feedback.py`.

## Verification

- `.venv/bin/pytest -q` from the repo root.

## Feature Map

- **App startup & workers** — lifespan, DB init, retry/retention/notification/reconciliation/governance workers, routes. Start: `main.py`. Files: `config.py`, `db.py`, `status.py`.
- **Authentication & request security** — sessions, login, permission gates, admin MFA/recovery, hardened headers. Start: `auth.py`. Files: `request_security.py`, `mfa.py`, `security.py`, `permissions.py`.
- **User lifecycle** — create/edit/assign/disable/delete/restore/purge and manual retry. Start: `users.py`. Files: `lifecycle.py`, `operations.py`, `models.py`.
- **Synchronization** — serialized fan-out to targets, operation correlation, encrypted pending secrets, retry/recovery worker. Start: `sync.py`. Files: `operations.py`, `target_credentials.py`.
- **SSH key management** — named per-device keys, add-before-revoke rotation, expiry, revocation. Start: `ssh_keys.py`.
- **Assignment profiles** — immutable profile versions, preview/publish/apply, per-user exceptions. Start: `assignments.py`.
- **Bulk onboarding/offboarding** — CSV/JSON preview and idempotent execution with exports, a target-ID picker modal, and an example CSV template built from the configured targets. Job bounds come from `bulk_import_policy` (`config.py`); the upload byte cap is derived from the row cap. Start: `bulk.py`. Files: `templates/bulk_import.html`, `config.py`.
- **Reconciliation** — sanitized snapshots, drift classification, approval-bound repair, scheduled previews. Start: `reconciliation.py`. Files: `reconcile.py`.
- **Unmanaged accounts** — read-only discovery, dispositions, adoption, guarded one-use removal. Start: `unmanaged.py`.
- **Access governance** — effective-date/inactivity policy, access reviews, attestations, reminders. Start: `governance.py`. Files: `inventory.py`.
- **Automation API v1** — capability discovery and user/target/operation/reconciliation/audit resources. Start: `api.py`. Files: `api_contract.py`.
- **Service accounts** — scoped expiring Bearer credentials with overlap rotation. Start: `service_accounts.py`.
- **CLI (`na-ssoctl`)** — scripted preview/apply/status/export over API v1. Start: `cli.py`.
- **Audit** — retention-governed audit events, query, and export. Start: `audit.py`. Files: `audit_query.py`, `audit_retention.py`.
- **Notifications** — redacted events, signed webhook delivery/retry, root destination controls. Start: `notifications.py`.
- **Target onboarding** — encrypted credential revisions, probes, reachability. Start: `target_credentials.py`.
- **OpenVPN target configuration and self-service** — per-OPNsense discovery and export-preset verification, plus assigned-user profile downloads that are never persisted. Start: `status.py`. Files: `auth.py`, `models.py`, `templates/status.html`, `templates/account.html`.
- **Admin UI** — server-rendered templates with live state updates. Start: `templates/base.html`. Files: `templates/`, `static/app.css`, `feedback.py`.
- **Console dashboard** — operational home page for console roles: eager tiles/charts plus a lazily-fetched insights section. Start: `dashboard.py`. Files: `templates/dashboard.html`.

## Child DOX Index

- connectors/ — target-specific adapters behind connector contract 1.0.
- mock_targets/ — in-process mock target app for the demo and tests.
- static/design/ — shared design-system bundle (tokens, components, charts).
