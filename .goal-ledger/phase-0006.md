# phase-0006 — Certificate revocation on offboarding

- Status: done
- Depends on: phase-0005
- Goal: Ensure a client certificate issued by na-sso stops being usable when the user is disabled or deleted, so the feature does not open an offboarding gap.
- Done when: disabling and deleting a user each remove or revoke their OPNsense client certificate, and the full suite passes.

## Sub-tasks
1. [done] Extend provision-openvpn.sh to create a CA CRL and attach it to the instance — done when: instances/get reports a non-empty crl and a rerun is idempotent.
2. [done] Implement `revoke_client_certificate(username)` doing CRL-add best-effort THEN `trust/cert/del` authoritative — done when: it finds the cert by CN+caref, attempts `trust/crl/set/$caref` (merged revoked list per the OPNsense controller contract; failure is logged, not fatal), then deletes via `trust/cert/del/$uuid`; idempotent, returning success when no certificate exists; verified against the mock (which implements a working CRL).
3. [done] Wire it into `disable_user`/`disable_user_for_assignment` and `delete_user` in `na_sso/connectors/opnsense.py`, only when the target has OpenVPN enabled — done when: identity-only OPNsense targets make no `trust/*` calls at all.
4. [done] Ensure a revocation failure does not silently succeed the offboarding — done when: a test shows a failing DELETE surfaces as a failed `SyncResult` naming the certificate; a best-effort CRL failure alone (delete OK) does NOT fail offboarding but is logged.
5. [done] Audit revocation as `openvpn.certificate_revoked` — done when: a test asserts the event on user disable.
6. [done] Run the full suite and a DOX pass over `na_sso/connectors/DOX.md` and `docs/CONNECTORS.md` — done when: `pytest` is green and both docs describe the OpenVPN capability, its config-write side effect, and the CN-equals-username invariant.

## Log
- (append-only, one line per event)
- BLOCKER (live research, needs user decision): `POST /api/trust/crl/set/{caref}` returns HTTP 500 "Unexpected error" on this demo firewall for ANY CA — empty CRL or with revocations, JSON or form-encoded. Ruled out the serial-0 theory (a fresh CA created with serial=10 also 500s). certs.inc loads and cert SIGNING works (cert/add succeeds), but CRL signing specifically fails; the accessible guest logs (lighttpd error, configd, php-fpm, system) show no matching exception.
- What DOES work live on the demo: CA/cert creation, cert deletion (trust/cert/del), export, discovery, validate — everything phases 1-5 rely on.
- CONFLICT: the user chose CRL revocation specifically because deletion does not invalidate an already-distributed .ovpn (OpenVPN validates against the CA). But CRL creation is broken on this demo firewall, so the chosen mechanism cannot be live-verified here (and cannot be verified on any real firewall from this environment — only this demo is reachable).
- Paused for user decision on how to proceed with revocation. Demo state left clean: all crl/set attempts 500'd (no state change); throwaway serial-test CA was deleted; the j-baking client cert remains valid.
- USER DECISION 2026-07-22: revocation does BOTH — trust/cert/del (authoritative, live-verified on demo) plus best-effort CRL write per the OPNsense CrlController contract (production true-invalidation, unit-tested against a mock). Order: CRL-add first (needs the cert to still exist by refid), then delete. Delete failure fails offboarding; CRL-only failure is logged, not fatal. Sub-task 1 (demo CRL provisioning) skipped — crl/set 500s on the demo.
- CRL contract (verified from source + live GET on the demo): POST /api/trust/crl/set/{caref} JSON body {"crl":{"crlmethod":"internal","descr":...,"revoked_reason_<code>":"<comma-separated refids>"}}. It REBUILDS the revoked list from the payload, so revoke = GET /api/trust/crl/get/{caref}, collect currently-selected refids across reason codes, add this cert's refid, POST the merged set. On the demo the POST 500s (best-effort catch); the mock will implement a working crl/set so the code path is unit-tested.
- executed by Codex under delegation; revoke_client_certificate + _offboard_openvpn gate in opnsense.py, mock crl/get+crl/set, tests, DOX + docs/CONNECTORS.md
- ORCHESTRATOR VERIFICATION: full suite 288 passed (incl. the two identity-only tests that failed mid-run, now green); ruff clean; config.py + test_connector_contract.py unchanged
- gate confirmed: `_offboard_openvpn` returns the identity result immediately when `_configured_openvpn_vpnid()` is None (no enabled+verified TargetOpenvpnConfig) — an identity-only OPNsense target makes ZERO discovery/trust/crl calls. delete authoritative, CRL best-effort, identity mutation runs before revocation.
- LIVE DELETE VERIFICATION (orchestrator, real OPNsense 26.7): issued a throwaway cert via the connector, then revoke_client_certificate -> ok=True, detail "deleted; ...CRL update failed with HTTP 500" (best-effort CRL 500 caught, authoritative delete succeeded); cert confirmed gone; second revoke idempotent ("already absent"); j-baking cert untouched; no key material in detail.
- CRL SUCCESS path is NOT live-verifiable on this demo (crl/set 500s); covered by the working mock crl endpoints in the suite. This is the documented consequence of the user's delete+CRL decision.
- audit openvpn.certificate_revoked at opnsense.py:409; DOX + docs/CONNECTORS.md updated per sub-task 6
- no parent-repo change this phase (sub-task 1 skipped: demo CRL provisioning impossible)
- ROOT CAUSE of the crl/set 500 FOUND (user asked to dig): NOT broken. I ran OPNsense's exact phpseclib CRL-signing sequence in-guest with the demo CA (loadX509/loadPrivateKey/withPadding/setPrivateKey/loadCA/signCRL/saveCRL/loadCRL) — ALL OK, valid 626-byte CRL. The 500 is because CrlController::setAction does `$crl->lifetime = (string)$payload['lifetime']` UNCONDITIONALLY, and OPNsense MVC promotes the undefined-key warning to an exception -> opaque "Unexpected error". The GUI always sends lifetime; my requests (and the connector's revoke code) omitted it.
- CONFIRMED LIVE: POST crl/set with {"crlmethod":"internal","descr":...,"lifetime":"9999"[,"serial":...]} returns {"status":"saved"}; revoking the client cert shows reason_0=[refid], serial increments. serial is optional (guarded by !empty); lifetime is the required missing field.
- IMPACT (latent bug in delivered code): Codex's revoke_client_certificate builds the crl/set payload WITHOUT lifetime, so its best-effort CRL would ALWAYS fail — on every real firewall, not just the demo. Must fix: include lifetime (and read/pass serial) in the payload. This turns CRL from "unverifiable best-effort" into a live-verifiable authoritative-capable path.
- FIX PLAN: (1) connector revoke sends lifetime (from crl/get, default 9999); (2) mock crl/set requires lifetime and 400/fails without it, so tests lock the regression; (3) live-verify full CRL revocation on the demo; (4) revisit sub-task 1 (attach CRL to instance) now that CRL works, for true end-to-end invalidation.
- demo state restored after investigation: test CRL deleted, j-baking not revoked, serial 0.

## Final resolution (2026-07-23) — CRL works; full end-to-end proven

- REVERSED the earlier "CRL best-effort, unverifiable" stance. Root cause of the crl/set 500 was a missing `lifetime` field (OPNsense promotes the undefined-key warning to an exception). CRL works fine on the demo.
- USER DECISION: revocation is now CRL-AUTHORITATIVE with deletion only as a fallback (a cert on the CRL cannot be deleted — verified live — so delete-after-CRL was self-defeating). Also: ensure_client_certificate now EXCLUDES CRL-revoked certs so a re-onboarded user gets a fresh cert, never their revoked one.
- CONNECTOR (Codex, sessions 019f8a4c): revoke_client_certificate = CRL GET-merge-POST (with lifetime), authoritative on success ("revoked via CRL", no delete); on CRL failure falls back to trust/cert/del ("CRL unavailable; revoked by deletion" + distributed-profile warning); both fail => failed result. Mock models "cannot delete a CRL-referenced cert" (HTTP 500). 289 tests pass, ruff clean, config + contract unchanged.
- sub-task 1 DONE: provision-openvpn.sh (PARENT) now creates an internal CA CRL and attaches it to the instance's crl field (idempotent). Without this the server never consults revocations.
- FULL LIVE END-TO-END PROVEN (real OPNsense 26.7 + container OpenVPN client):
  1. user downloaded a .ovpn and CONNECTED — server log: `crl-e2e-user ... SENT CONTROL: PUSH_REPLY ... ifconfig 10.19.47.2` (tunnel IP assigned).
  2. na-sso revoked the cert via the connector — `revoked via CRL`, CRL lists the refid.
  3. same client reconnected and was REFUSED — server log: `CRL: loaded 1 CRLs` -> `VERIFY ERROR: depth=0, error=certificate revoked: CN=crl-e2e-user, serial=6` -> `Sent fatal SSL alert: certificate revoked`.
- FINDING: OPNsense AUTO-APPLIES the CRL — the connector's crl/set alone (no reconfigure) made the server regenerate its crl-verify file and enforce it on the next connection. So revocation takes effect immediately; the connector needs no extra apply step.
- demo state restored: crl-e2e-user + cert deleted, CRL revocations cleared (empty CRL still attached), users root + j-baking.

