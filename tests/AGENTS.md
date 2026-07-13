# tests

## Purpose

Behavioral test suite for authentication, managed users, connectors, synchronization, dashboards, and audit events.

## Ownership

Owns pytest fixtures and tests only. Production contracts and implementation remain under `../oneauth/`.

## Local Contracts

- Tests use an isolated temporary SQLite database and clear cached settings between configurations.
- External target calls are mocked with `respx`; the default suite must not contact or mutate real services.
- Assert both returned HTTP behavior and persisted security/sync state for workflows that cross route and database boundaries.

## Verification

- Run `.venv/bin/pytest -q` from the repository root; the full suite must pass.

## Feature Map

- **Test application fixtures** — Creates isolated clients, database state, and authenticated admin sessions. Start: `conftest.py`.
- **Authentication and user workflows** — Covers login guards, CRUD, duplicate validation, status changes, and plaintext-secret exclusion. Start: `test_users.py`.
- **Connector behavior** — Covers interface conformance, target API request/response shapes, probes, and status-page integration. Start: `test_connectors.py`.
- **Synchronization behavior** — Covers full and partial success, retry targeting, disable/delete, dashboard state, and audit events. Start: `test_sync.py`.

## Child DOX Index

- (none)
