# tests

## Purpose

Behavioral test suite for authentication, managed users, connectors, synchronization, dashboards, and audit events.

## Ownership

Owns pytest fixtures and tests only. Production contracts and implementation remain under `../na_sso/`.

## Local Contracts

- Tests use an isolated temporary SQLite database and clear cached settings between configurations.
- External target calls use `respx` or the in-process loopback mock-target server; the default suite must not contact or mutate real services.
- Assert both returned HTTP behavior and persisted security/sync state for workflows that cross route and database boundaries.

## Verification

- Run `.venv/bin/pytest -q` from the repository root; the full suite must pass.

## Feature Map

- **Configuration and application fixtures** — Validates YAML target/policy parsing and creates isolated clients, database state, and authenticated admin sessions. Start: `test_config.py`. Files: `conftest.py`.
- **Authentication and user workflows** — Covers login guards, the fluid shared page shell, the shared username character/edge contract, conditional manual-password confirmation, generated-password modal safety markup, CRUD, duplicate validation, status changes, and plaintext-secret exclusion. Start: `test_users.py`.
- **Account security** — Covers password policy/generation and expiry display, mandatory initial/reset replacement with `CHPW`, one-time copy modal, styled password-decision/account/one-time-key UX, role-restricted login, `SUPERADMIN` root immutability/N/A target presentation, and private-key-to-public-key enrollment boundaries. Start: `test_security.py`.
- **Connector behavior** — Covers interface conformance, target API request/response shapes, configured default memberships, probes, and status-page integration. Start: `test_connectors.py`.
- **Encrypted target onboarding** — Covers credential encryption/redaction, SSH admin password, private-key, and combined two-factor modes, save/probe gating, configuration status, and route authorization. Start: `test_target_credentials.py`.
- **Synchronization and lifecycle behavior** — Covers stable-ID migration, retired ambiguity, assignment/deferred states, full and partial success, retry, disable, deletion, restore, expiry, SSE, dashboards, and audit events. Start: `test_sync.py`. Files: `test_users.py`, `test_migrations.py`.
- **Mock target and demo integration** — Covers target API contracts, real-HTTP connector lifecycles, failure/retry UX, and the all-target application workflow. Start: `test_mock_targets.py`.

## Child DOX Index

- (none)
