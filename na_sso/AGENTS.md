# na_sso

## Purpose

FastAPI application for administering local users and propagating their credentials to enabled external targets.

## Ownership

Owns the Python application package, persistence model, routes, synchronization, bundled web assets, and optional demo target service. Connector-specific contracts belong to `connectors/`; mock API contracts belong to `mock_targets/`; page markup belongs to `templates/`; the copied design kit keeps its contract in `static/design/`.

## Local Contracts

- `main.py` is the application entry point and registers every route module.
- Bootstrap settings come from `NA_SSO_*` environment values; target instances and password/SSH-key policies come from the YAML file named by `NA_SSO_CONFIG_FILE`. YAML secrets use exact `${ENV_NAME}` references; never commit live credentials.
- Plaintext account passwords are never persisted. Pending propagation secrets are Fernet-encrypted and cleared after assigned targets consume them; `awaiting_credentials` is not retried until verified authentication or a password action supplies a credential.
- Target management credentials are entered through the admin UI, encrypted with the global secret, never rendered back, and saved and probed as one action; propagation remains gated on a successful probe of the current revision.
- The immutable local root identity is displayed as `SUPERADMIN`; it never has target state and every target matrix renders `N/A` for it.
- User changes fan out only to assigned stable target IDs through `sync.py`; unassignment disables, removed/ambiguous targets retain retired history, and each result updates `SyncState` plus an audit event.
- Authenticated sync-state SSE is served by `status.py`; event payloads never include credentials or pending secrets.
- Failed target operations persist attempt and retry timing metadata; the single-process recovery worker replays the user's persisted desired action.
- Deletion is soft locally: remote deletion completion sets `deleted_at`; only an explicit, guarded purge removes the row. Restore requires a new password.
- SQLite access goes through `db.py`; schema entities live in `models.py`.

## Verification

- Run `.venv/bin/pytest -q` from the repository root after application changes.
- For container-affecting changes, use `./compose-helper.sh rebuild` and bounded helper logs from the repository root.

## Feature Map

- **Application configuration and startup** — Loads strict YAML target/policy configuration with environment-backed secrets, initializes the database, bootstraps the admin account, mounts branded static assets, and registers routers. Start: `config.py`. Files: `main.py`, `db.py`, `models.py`, `static/site.webmanifest`.
- **Local authentication and account security** — Provides role-aware, versioned signed-cookie sessions; protected local-only root recovery; password accept/change workflows, policy/history enforcement; and browser-first public-key-only SSH enrollment. Start: `auth.py`. Files: `security.py`, `models.py`, `db.py`. Detail in `./templates`.
- **Managed-user lifecycle** — Creates user/admin local accounts, updates, resets passwords, disables, soft-deletes, restores, explicitly purges, and retries managed accounts while enforcing immutable root invariants. Start: `users.py`. Files: `models.py`, `security.py`, `sync.py`. Detail in `./templates` and `./connectors`.
- **Assignment, synchronization, and recovery** — Tracks assigned, unassigned, retired, deferred-credential, and expired states by stable target ID; fans desired operations to assignments; persists capped retries and clears staged credentials after consumption. Start: `sync.py`. Files: `models.py`, `db.py`, `users.py`, `audit.py`. Detail in `./connectors`.
- **Target status dashboard** — Probes enabled targets and presents their configuration and health without duplicating user synchronization state. Start: `status.py`. Files: `models.py`. Detail in `./templates` and `./connectors`.
- **Target credential onboarding** — Stores encrypted API/admin/SSH authentication, immediately probes each saved revision, reports one combined configuration/authentication status, and gates propagation on success. Start: `target_credentials.py`. Files: `status.py`, `models.py`, `security.py`. Detail in `./templates` and `./connectors`.
- **Live synchronization events** — Streams authenticated user/target state snapshots to pending and retrying UI entries. Start: `status.py`. Files: `models.py`. Detail in `./templates`.
- **Audit trail** — Records administrative and connector actions and serves the audit page. Start: `audit.py`. Files: `models.py`. Detail in `./templates`.
- **Mock target demo** — Emulates all three target APIs so the real application and connectors can demonstrate complete workflows without external systems. Start: `mock_targets/app.py`. Detail in `./mock_targets`.

## Child DOX Index

- `connectors/` — Connector interface, registry, and target API implementations.
- `mock_targets/` — Stateful demo APIs for OPNsense, Nexus Repository, and Nextcloud connector contracts.
- `templates/` — Jinja pages for authentication, users, target status, and audit history.
- `static/design/` — Bundled lazyway.io design-system assets and their local contract.
