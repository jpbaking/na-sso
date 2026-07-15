# na_sso/connectors

## Purpose

Adapters that translate managed-user operations into each target system's user-management API.

## Ownership

Owns the connector interface, registry, typed results, and OPNsense, Nexus Repository, Nextcloud, and SSH implementations. Orchestration and persistence remain in `../sync.py` and `../models.py`.

## Local Contracts

- Every connector implements contract 1.0 idempotent `ensure_user`, `disable_user`, `delete_user`, and `probe` operations and returns typed, retry-aware `SyncResult` failures instead of leaking client exceptions.
- Every production connector implements read-only `inspect_user` and bounded `discover_accounts`; discovery uses only search/read endpoints or non-mutating SSH commands and returns sanitised shared records instead of credentials, key material, or raw remote errors. Base dry-run planning consumes inspection only and never invokes mutation.
- Production connectors accept resolved per-user assignment memberships for ensure, disable, and inspection. Nexus treats them as roles; OPNsense, Nextcloud, and SSH treat them as groups. Legacy test connectors may use the base fallback and ignore profile intent.
- Every instance exposes stable target ID/type/display name plus identity, inspection, discovery, dry-run, membership, last-use, timeout, and error-taxonomy metadata; validate all selected targets before remote mutation.
- `get_connectors()` returns only enabled, currently verified YAML instances in declared propagation order.
- YAML owns non-secret endpoints and capabilities; encrypted database records supply management credentials only for immediate connector construction. Never log credentials, management keys, or plaintext passwords.
- SSH pins the configured host fingerprint, authenticates with an encrypted admin password, uploaded private key, or both when the server requires two factors, uses non-interactive constrained sudo operations, and persists only managed-user public keys.
- Nexus applies configured default roles; OPNsense, Nextcloud, and SSH apply configured default groups. Memberships must already exist, and SSH appends supplementary groups without removing other memberships.
- API calls use bounded timeouts. A missing remote account is a successful delete outcome.
- Endpoint or payload changes require verification against official target documentation or source plus mocked-response tests.

## Verification

- Run `.venv/bin/pytest -q tests/test_connectors.py` from the repository root.

## Feature Map

- **Versioned connector contract and verified registry** — Defines contract 1.0 async mutation/probe/inspection/discovery/dry-run interfaces, machine-readable capabilities, typed retry-aware failures, bounded timeouts, profile-aware assignment intent, encrypted credential hydration, and verified-target construction. Start: `base.py`. Files: `__init__.py`, `../assignments.py`, `../reconciliation.py`, `../target_credentials.py`, `../../docs/CONNECTORS.md`.
- **OPNsense local users** — Manages Auth User API accounts and configured group memberships, and inspects profile/status/membership state through search, with API key/secret authentication. Start: `opnsense.py`.
- **Nexus Repository local users** — Manages and read-only inspects Security API accounts, roles, profile, status, and password changes. Start: `nexus.py`.
- **Nextcloud local users** — Manages OCS Provisioning API accounts and configured group memberships, and read-only inspects profile/status/membership state while interpreting OCS status codes. Start: `nextcloud.py`.
- **SSH local users** — Connects through pinned-host SSH with management password, private key, or both; safely creates platform-aware Unix users; appends configured supplementary groups; manages password, authorized-key, lock, and deletion lifecycle; and inspects passwd, group, lock, and key-fingerprint state with read-only commands. Start: `ssh.py`.

## Child DOX Index

- (none)
