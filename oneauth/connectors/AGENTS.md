# oneauth/connectors

## Purpose

Adapters that translate managed-user operations into each target system's user-management API.

## Ownership

Owns the connector interface, registry, typed results, and OPNsense, Nexus Repository, and Nextcloud implementations. Orchestration and persistence remain in `../sync.py` and `../models.py`.

## Local Contracts

- Every connector implements idempotent `ensure_user`, `disable_user`, `delete_user`, and `probe` operations and returns `SyncResult` instead of leaking HTTP exceptions.
- `get_connectors()` returns only enabled targets in propagation order.
- Connector credentials and base URLs come exclusively from `Settings`; never log credentials or plaintext passwords.
- API calls use bounded timeouts. A missing remote account is a successful delete outcome.
- Endpoint or payload changes require verification against official target documentation or source plus mocked-response tests.

## Verification

- Run `.venv/bin/pytest -q tests/test_connectors.py` from the repository root.

## Feature Map

- **Connector contract and registry** — Defines the common async interface, result shape, and enabled-target discovery. Start: `base.py`. Files: `__init__.py`.
- **OPNsense local users** — Manages Auth User API accounts with API key/secret authentication. Start: `opnsense.py`.
- **Nexus Repository local users** — Manages Security API accounts, roles, status, and password changes. Start: `nexus.py`.
- **Nextcloud local users** — Manages OCS Provisioning API accounts and interprets OCS status codes. Start: `nextcloud.py`.

## Child DOX Index

- (none)
