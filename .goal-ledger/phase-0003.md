# phase-0003 — Connector capability: discovery, cert issuance, config export

- Status: done
- Depends on: phase-0002
- Goal: Add OpenVPN export to the OPNsense connector as a capability distinct from the identity `Connector` ABC, so identity sync stays unchanged.
- Done when: `pytest tests/test_connectors.py tests/test_connector_contract.py` passes, including a test proving `ensure_user`/`inspect_user` behaviour is byte-for-byte unchanged.

## Sub-tasks
1. [done] Define an `OpenVpnExport` capability protocol (discovery, issue-cert, export-config, revoke-cert) separate from `Connector` in `na_sso/connectors/base.py`, and have `OPNsenseConnector` implement it — done when: `test_connector_contract.py` still passes untouched, confirming the identity contract did not change.
2. [done] Implement discovery: list servers from `export/providers`, list templates from `export/templates`, and resolve the true auth posture (legacy `mode`; Instances via `instances/get`) — done when: a unit test against the mock returns the seeded server with a posture of "certificate required".
3. [done] Implement `ensure_client_certificate(username)`: look for an existing cert in `export/accounts` whose CN equals the username and whose `caref` matches the server, otherwise `trust/cert/add` with `commonname == username` and `private_key_location: firewall` — done when: calling it twice yields the same `certref` and creates exactly one certificate.
4. [done] Implement `export_config(username, mode)` posting `openvpn_export` with the chosen template, appending `certref` only in certificate mode and omitting the path segment entirely otherwise — done when: both modes return decoded bytes and the password-only path never sends an empty `certref`.
5. [done] Map failures to `ConnectorErrorKind` — read-only key to `AUTHENTICATION` with a message naming the missing "VPN: OpenVPN: Client Export" privilege, and CA mismatch to `VALIDATION` — done when: two tests assert the error kind and that no key material appears in `detail`.
6. [done] Add a non-mutating `validate_export(vpnid)` wrapping `export/validate_presets` — done when: a test confirms it reports success without the mock recording a preset write.

## Log
- (append-only, one line per event)
- executed by Codex (session 019f86f6) under delegation; capability added to base.py (OpenVpnExport Protocol) + opnsense.py, tests in test_connectors.py, DOX updated
- ORCHESTRATOR VERIFICATION: mock+contract 50 passed; full suite 269 passed; ruff clean; test_connector_contract.py UNCHANGED; identity methods byte-for-byte unchanged
- LIVE SMOKE TEST (orchestrator-only, against real OPNsense 26.7): discovery resolved posture=cert_and_password + caref; ensure_client_certificate idempotent (ref1==ref2, reused existing cert, no dup); both export modes correct (4619B cert bundle / 1512B password-only); validate_presets returned ok against the real API
- INVARIANT 7 VERIFIED LIVE: passing the real server cert's refid to export_config is rejected with a VALIDATION SyncResult BEFORE any HTTP call; no key material in the detail. The connector's per-instance allowlist (refid->username, populated only by ensure_client_certificate) is the guard.
- BUG CAUGHT BY LIVE TEST, not by mocks: Codex's first version called `/trust/cert/search?carefs=..&user=..`, which returns HTTP 500 on real 26.7 (the mock ignored the params). Sent back to Codex's session; fixed to a plain search + client-side filter; idempotency test strengthened to assert an empty query string. This validates the decision to keep a live smoke test at this phase.
- design note for phase-0005: export_config(certref=...) only succeeds on the SAME connector instance that minted the cert via ensure_client_certificate (the allowlist is instance-local). phase-0005's request flow must call both on one connector instance. This is a safety feature (no cross-instance refid injection), not a limitation to work around.
- gap for phase-0004: the mock has NO /api/openvpn/export/validate_presets endpoint (Codex tested validate via respx). phase-0004 delegation MUST add it to the mock, since admin verification uses discovery + validate_presets.

