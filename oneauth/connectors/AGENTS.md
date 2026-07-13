# oneauth/connectors

## Purpose

Adapters that translate managed-user operations into each target system's user-management API.

## Ownership

Owns the connector interface, registry, typed results, and OPNsense, Nexus Repository, Nextcloud, and SSH implementations. Orchestration and persistence remain in `../sync.py` and `../models.py`.

## Local Contracts

- Every connector implements idempotent `ensure_user`, `disable_user`, `delete_user`, and `probe` operations and returns `SyncResult` instead of leaking HTTP exceptions.
- Every instance exposes stable target ID/type/display name and identity-attribute capabilities; validate all selected targets before remote mutation.
- `get_connectors()` returns only enabled, currently verified YAML instances in declared propagation order.
- YAML owns non-secret endpoints and capabilities; encrypted database records supply management credentials only for immediate connector construction. Never log credentials, management keys, or plaintext passwords.
- SSH pins the configured host fingerprint, authenticates with an encrypted admin password or uploaded private key, uses non-interactive constrained sudo operations, and persists only managed-user public keys.
- Nexus applies configured default roles; OPNsense, Nextcloud, and SSH apply configured default groups. Memberships must already exist, and SSH appends supplementary groups without removing other memberships.
- API calls use bounded timeouts. A missing remote account is a successful delete outcome.
- Endpoint or payload changes require verification against official target documentation or source plus mocked-response tests.

## Verification

- Run `.venv/bin/pytest -q tests/test_connectors.py` from the repository root.

## Feature Map

- **Connector contract and verified registry** — Defines the common async interface, encrypted credential hydration, explicit-probe construction, and verified-target discovery. Start: `base.py`. Files: `__init__.py`, `../target_credentials.py`.
- **OPNsense local users** — Manages Auth User API accounts and configured group memberships with API key/secret authentication. Start: `opnsense.py`.
- **Nexus Repository local users** — Manages Security API accounts, roles, status, and password changes. Start: `nexus.py`.
- **Nextcloud local users** — Manages OCS Provisioning API accounts and configured group memberships while interpreting OCS status codes. Start: `nextcloud.py`.
- **SSH local users** — Safely creates platform-aware Unix users, appends configured supplementary groups, and manages password, authorized-key, lock, and deletion lifecycle through pinned management-key SSH. Start: `ssh.py`.

## Child DOX Index

- (none)
