# Connector contract

NA-SSO connector contract **1.0** defines the minimum behavior for built-in and
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
account-discovery, and dry-run support; membership and SSH key-last-use
semantics; bounded connection/operation timeouts; and all stable error kinds.
Do not claim inferred features. The built-in SSH connector reports key last use
as unsupported because its management channel does not receive login logs.

## Optional read paths

`inspect_user` returns sanitised observations for reconciliation.
`discover_accounts` returns bounded `RemoteAccount` metadata without mutation.
Never include password hashes, tokens, private/public key material, raw response
bodies, or secret-bearing URLs. `plan_user` uses inspection only and returns
bounded field actions/blockers; it must never invoke ensure, disable, or delete.

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
passes. Document every unsupported optional capability explicitly.
