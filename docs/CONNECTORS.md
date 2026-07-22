# Connector contract

NA-SSO connector contract **1.1** defines the minimum behavior for built-in and
third-party target adapters. The executable source of truth is
`na_sso/connectors/base.py`; this guide explains the extension boundary.

## Required interface

A connector subclasses `Connector`, exposes stable `target_id`, `target_type`,
and `display_name`, and implements async, idempotent `ensure_user`,
`disable_user`, `delete_user`, and `probe` methods. Mutations return
`SyncResult`; they do not leak client-library exceptions. Deleting an already
absent identity succeeds.

Identity validation runs before mutation. Passwords are transient arguments and
must never be stored, logged, added to command lines, or returned in detail.
Public keys may be persisted; private keys and management credentials may not.

## Machine-readable capabilities

`contract_metadata()` publishes contract version/type; read-only inspection,
account-discovery, and dry-run support; ensure, disable, and delete support;
membership and SSH key-last-use semantics; bounded connection/operation
timeouts; and all stable error kinds. Do not claim inferred features. The
built-in SSH connector reports key last use as unsupported because its
management channel does not receive login logs.

### Lifecycle operation support

`ensure_supported`, `disable_supported`, and `delete_supported` declare whether
the connector can perform each lifecycle mutation. All three default to `True`
on `Connector`, so adapters override only operations they genuinely cannot
perform:

```python
class JenkinsConnector(Connector):
    disable_supported = False
```

Jenkins' built-in local security realm has no realm-independent account disable.
Its connector therefore declares disable unsupported and retains the
execution-time refusal in `disable_user` as defence in depth.

NA-SSO consumes these declarations before mutation:

- synchronization selects ensure, disable, or delete, then records a terminal
  `unsupported` sync state without calling the connector or creating a target
  attempt when that operation is declared unsupported;
- `plan_user` adds the declaration detail as a dry-run blocker;
- reconciliation changes otherwise repairable drift to
  `DriftState.UNSUPPORTED`, preserving desired/actual evidence without offering
  the doomed repair;
- assignment/edit, delete-confirmation, and bulk-preview UI surfaces show a
  plain-text warning before the operator confirms the affected action; and
- `/api/v1/targets` exposes the three flags inside `connector_contract`.

A third-party Contract 1.1 connector must leave each supported operation at its
default or set it explicitly, and set the matching class attribute to `False`
for every unsupported operation. Keep the corresponding async method
idempotent and return a sanitised failed `SyncResult` with validation semantics
if it is called; do not remove the method or raise a client exception. The
runtime validation path remains for defence in depth and older connector
behavior, but it is not a substitute for an accurate declaration.

## Optional read paths

`inspect_user` returns sanitised observations for reconciliation.
`discover_accounts` returns bounded `RemoteAccount` metadata without mutation.
Never include password hashes, tokens, private/public key material, raw response
bodies, or secret-bearing URLs. `plan_user` uses inspection only and returns
bounded field actions/blockers; it must never invoke ensure, disable, or delete.

## Optional OpenVPN capability

`OpenVpnExport` is independent of the identity connector contract. OPNsense
implements server discovery, export-preset validation, client-certificate
issuance and revocation, and `.ovpn` export. A managed client certificate uses
the invariant `CN == username` and must match the selected server CA. Issuance
excludes every certificate currently selected under any reason code on that
CA's CRL, so re-onboarding creates a fresh certificate instead of reusing a
revoked one.

A profile download is not a read-only firewall operation: it can issue the
user's client certificate and write certificate/export state to OPNsense
configuration. NA-SSO streams the resulting profile without persisting it or
its key material.

For a verified, OpenVPN-enabled OPNsense target, user disable, assignment
disable, and delete also revoke the matching client certificate. Revocation
first GETs and merges all selected entries in the CA CRL, adds the certificate
under reason code 0, and POSTs the rebuilt CRL while the certificate still
exists, carrying forward the required CRL lifetime. A successful CRL update is
authoritative and the revoked certificate remains stored because OPNsense will
not delete a CRL-referenced certificate. If CRL update fails, the connector
logs the sanitized failure and falls back to certificate deletion by UUID;
deletion can remove firewall configuration but cannot invalidate an already
distributed profile. If both operations fail, offboarding fails after the
identity mutation has already run. Targets without enabled, verified OpenVPN
configuration remain identity-only and make no trust or CRL calls.

## Errors and timeouts

Failed results use authentication, validation, not found, conflict, rate
limited, unavailable, timeout, remote rejected, or internal. Only transient
classes are retryable. Calls use the shared 15-second connection and 20-second
operation bounds unless a tighter implementation limit is justified.

## Conformance workflow

Add mocked tests for payloads, reads, error mapping, idempotency, and timeouts:

```sh
.venv/bin/pytest -q tests/test_connector_contract.py tests/test_connectors.py
.venv/bin/pytest -q
```

Register typed configuration and a connector factory only after conformance
passes. Assert the published operation declarations in conformance tests and
document every unsupported capability explicitly.
