# na_sso/connectors

## Purpose

Target-specific adapters that carry out user mutations and read-only inspection against each external system.

## Ownership

Owns the connector contract and every adapter here. Operation scheduling and retry belong to `../sync.py`; drift classification to `../reconciliation.py`.

## Local Contracts

- Contract version 1.0; executable source of truth is `base.py`, explained in `docs/CONNECTORS.md`.
- A connector subclasses `Connector` with stable `target_id`/`target_type`/`display_name` and async, idempotent `ensure_user`, `disable_user`, `delete_user`, `probe`. Mutations return `SyncResult` and never leak client-library exceptions; deleting an absent identity succeeds.
- Passwords are transient — never stored, logged, put on command lines, or returned in error detail. Public keys may persist; private keys and management credentials may not.
- Capabilities (inspection, discovery, dry-run, membership, key-last-use, timeouts, errors) are published machine-readably — clients must not guess from target type.
- OPNsense's optional OpenVPN capability uses `CN == username` for managed client certificates. Issuance excludes every certificate selected on the CA CRL so re-onboarding creates a fresh certificate. Profile downloads can create a certificate and write export state to firewall configuration; profiles and key material remain transient in NA-SSO.
- Disabling, unassigning, or deleting a user on a verified, OpenVPN-enabled OPNsense target revokes that certificate before completing offboarding. CRL merge/update carries forward the required current lifetime and is authoritative; deletion by UUID is attempted only when CRL update fails, because OPNsense retains CRL-referenced certificates. Identity-only targets make no trust or CRL calls.
- The SSH connector writes the complete active key set, including an empty file after final-key revocation.

## Verification

- `.venv/bin/pytest -q tests/test_connectors.py tests/test_connector_contract.py`

## Feature Map

- **Connector contract** — base class, error kinds, capabilities, timeouts. Start: `base.py`.
- **OpenVPN client certificate lifecycle and export** — optional connector capability for server discovery, CRL-aware `CN == username` certificate issuance, authoritative CRL revocation with delete fallback on offboarding, preset validation, and config-writing `.ovpn` export without expanding the identity contract. Start: `base.py`. Files: `opnsense.py`.
- **API-based adapters** — one module per target. Start: `gitlab.py`. Files: `gitea.py`, `nextcloud.py`, `nexus.py`, `jenkins.py`, `immich.py`, `opnsense.py`, `npm.py`.
- **SSH adapter** — pinned-host key management over asyncssh. Start: `ssh.py`.

## Child DOX Index

- (none)
