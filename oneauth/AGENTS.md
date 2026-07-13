# oneauth

## Purpose

FastAPI application for administering local users and propagating their credentials to enabled external targets.

## Ownership

Owns the Python application package, persistence model, routes, synchronization, bundled web assets, and optional demo target service. Connector-specific contracts belong to `connectors/`; mock API contracts belong to `mock_targets/`; page markup belongs to `templates/`; the copied design kit keeps its contract in `static/design/`.

## Local Contracts

- `main.py` is the application entry point and registers every route module.
- Configuration comes from `ONEAUTH_*` settings, normally supplied through `.config/.env`; never commit live credentials.
- Plaintext managed-user passwords are never persisted. Pending propagation secrets are Fernet-encrypted and cleared only after all enabled targets succeed.
- User changes fan out through `sync.py`; each target result must update `SyncState` and produce an audit event.
- Authenticated sync-state SSE is served by `status.py`; event payloads never include credentials or pending secrets.
- Failed target operations persist attempt and retry timing metadata; the single-process recovery worker replays the user's persisted desired action.
- Deletion is soft locally: remote deletion completion sets `deleted_at`; only an explicit, guarded purge removes the row. Restore requires a new password.
- SQLite access goes through `db.py`; schema entities live in `models.py`.

## Verification

- Run `.venv/bin/pytest -q` from the repository root after application changes.
- For container-affecting changes, use `./compose-helper.sh rebuild` and bounded helper logs from the repository root.

## Feature Map

- **Application startup** — Initializes the database, bootstraps the admin account, mounts static assets, and registers routers. Start: `main.py`. Files: `config.py`, `db.py`, `models.py`.
- **Admin authentication** — Provides signed-cookie login/logout and bcrypt password verification. Start: `auth.py`. Files: `security.py`, `models.py`. Detail in `./templates`.
- **Managed-user lifecycle** — Creates, updates, disables, soft-deletes, restores, explicitly purges, and retries managed accounts. Start: `users.py`. Files: `models.py`, `security.py`, `sync.py`. Detail in `./templates` and `./connectors`.
- **Synchronization and recovery** — Fans desired operations out to enabled connectors, persists capped retry schedules, automatically replays due failures, and clears pending secrets after complete success. Start: `sync.py`. Files: `models.py`, `audit.py`. Detail in `./connectors`.
- **Target status dashboard** — Probes enabled targets and displays the user-by-target state matrix. Start: `status.py`. Files: `models.py`. Detail in `./templates` and `./connectors`.
- **Live synchronization events** — Streams authenticated user/target state snapshots to pending and retrying UI entries. Start: `status.py`. Files: `models.py`. Detail in `./templates`.
- **Audit trail** — Records administrative and connector actions and serves the audit page. Start: `audit.py`. Files: `models.py`. Detail in `./templates`.
- **Mock target demo** — Emulates all three target APIs so the real application and connectors can demonstrate complete workflows without external systems. Start: `mock_targets/app.py`. Detail in `./mock_targets`.

## Child DOX Index

- `connectors/` — Connector interface, registry, and target API implementations.
- `mock_targets/` — Stateful demo APIs for OPNsense, Nexus Repository, and Nextcloud connector contracts.
- `templates/` — Jinja pages for authentication, users, target status, and audit history.
- `static/design/` — Bundled lazyway.io design-system assets and their local contract.
