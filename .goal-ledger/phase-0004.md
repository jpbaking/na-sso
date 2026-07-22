# phase-0004 — Persisted per-target OpenVPN settings and admin UI

- Status: done
- Depends on: phase-0003
- Goal: Let an admin enable and verify OpenVPN self-service entirely from the web UI, with no new YAML surface.
- Done when: `na_sso/config.py` is unchanged by this goal, and an admin can enable OpenVPN on the mock OPNsense target from `/status` using pickers populated by live API discovery.

## Sub-tasks
1. [done] Add a `TargetOpenvpnConfig` model in `na_sso/models.py` (target_id unique, enabled, vpnid, template, cert_lifetime_days, detected auth posture, verified_at, verify_detail) plus a migration — done when: `pytest tests/test_migrations.py` passes on a database created before this change.
2. [done] Keep it out of `TargetCredential.encrypted_payload` — done when: a test asserts rotating credentials leaves the OpenVPN row and its `revision` untouched, since these settings are not secret and have their own verification lifecycle.
3. [done] Add `GET /targets/{target_id}/openvpn/discover` returning servers and templates from the live target, guarded by `MANAGE_TARGETS` — done when: it returns 403 for a non-admin and the seeded server for an admin.
4. [done] Add `POST /targets/{target_id}/openvpn` following the save-then-verify shape of `configure_target` (`na_sso/status.py:196`) — done when: saving stores the row and immediately runs verification.
5. [done] Make verification call discovery plus `export/validate_presets` only, writing nothing to the firewall — done when: an under-privileged mock key leaves `verified_at` null with the phase-0003 privilege message, and the mock records zero preset writes during verification. User decision 2026-07-22: phase-0001 proved discovery itself 403s on an under-privileged key, so this catches the realistic misconfiguration with no config.xml write and no firewall audit noise; genuine write-privilege failure surfaces on the first real user download instead.
6. [done] Add an admin-settable export `hostname` (the client's `remote`), defaulting to the value discovery reports and required before enabling — done when: enabling a target whose `export/providers` reports `hostname: null` is rejected with a message naming the field. Added during phase-0001: the demo instance binds no interface, so the export would otherwise generate a config with no reachable `remote`.
7. [done] Render the section in `status.html` behind the existing target expander, with server and template as discovery-backed selects, the auth posture shown read-only, and copy warning that each download writes to the firewall's config and audit log — done when: the section only appears for `type == "opnsense"` targets whose credentials are already verified, and the warning is visible next to the enable control.

## Log
- (append-only, one line per event)
- executed by Codex under delegation; 8 files: models.py (+TargetOpenvpnConfig), db migration via create_all, mock validate_presets endpoint, status.py (2 routes + view), status.html, and 3 test files
- ORCHESTRATOR VERIFICATION: focused gate 77 passed; full suite 274 passed; ruff clean
- GATES CONFIRMED INDEPENDENTLY: config.py UNCHANGED (git diff empty); .goal-ledger/tests-fixtures/contract-test untouched; verification calls NEITHER export_config NOR ensure_client_certificate (grep-verified) — non-mutating as decided; the phase-0003 mock-gap (validate_presets) is now filled with a non-mutation test
- route review: discover (MANAGE_TARGETS, opnsense-only, 409 before verified creds, 403->auth) and configure (save-then-verify; requires hostname+server+template; cross-checks the selected vpnid/template against discovery before validate; audits every outcome). Template gated on `p.type=='opnsense' and p.verified`; the /status view populates p.openvpn + p.verified per target.
- LIVE TEST DEFERRED (deliberate): phase-0004's only firewall-facing calls are discover_openvpn + validate_openvpn_export, both proven live in phase-0003 via the same connector methods; the credential plumbing (build_unverified_connector) is the same path the existing opnsense probe already uses. Full end-to-end live confirmation happens in phase-0005.

