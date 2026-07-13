# oneauth

## Purpose

FastAPI application for administering local users and propagating their credentials to enabled external targets.

## Ownership

Owns the Python application package, persistence model, routes, synchronization, and bundled web assets. Connector-specific contracts belong to `connectors/`; page markup belongs to `templates/`; the copied design kit keeps its contract in `static/design/`.

## Local Contracts

- `main.py` is the application entry point and registers every route module.
- Configuration comes from `ONEAUTH_*` settings, normally supplied through `.config/.env`; never commit live credentials.
- Plaintext managed-user passwords are never persisted. Pending propagation secrets are Fernet-encrypted and cleared only after all enabled targets succeed.
- User changes fan out through `sync.py`; each target result must update `SyncState` and produce an audit event.
- SQLite access goes through `db.py`; schema entities live in `models.py`.

## Verification

- Run `.venv/bin/pytest -q` from the repository root after application changes.
- For container-affecting changes, use `./compose-helper.sh rebuild` and bounded helper logs from the repository root.

## Feature Map

- **Application startup** — Initializes the database, bootstraps the admin account, mounts static assets, and registers routers. Start: `main.py`. Files: `config.py`, `db.py`, `models.py`.
- **Admin authentication** — Provides signed-cookie login/logout and bcrypt password verification. Start: `auth.py`. Files: `security.py`, `models.py`. Detail in `./templates`.
- **Managed-user administration** — Creates, updates, disables, deletes, and retries managed accounts while maintaining pending target states. Start: `users.py`. Files: `models.py`, `security.py`, `sync.py`. Detail in `./templates` and `./connectors`.
- **Synchronization** — Fans user operations out to enabled connectors, persists target results, and clears pending secrets after complete success. Start: `sync.py`. Files: `models.py`, `audit.py`. Detail in `./connectors`.
- **Target status dashboard** — Probes enabled targets and displays the user-by-target state matrix. Start: `status.py`. Files: `models.py`. Detail in `./templates` and `./connectors`.
- **Audit trail** — Records administrative and connector actions and serves the audit page. Start: `audit.py`. Files: `models.py`. Detail in `./templates`.

## Child DOX Index

- `connectors/` — Connector interface, registry, and target API implementations.
- `templates/` — Jinja pages for authentication, users, target status, and audit history.
- `static/design/` — Bundled lazyway.io design-system assets and their local contract.
