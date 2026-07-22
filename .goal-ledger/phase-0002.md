# phase-0002 — Mock target endpoints mirroring the captured transcripts

- Status: done
- Depends on: phase-0001
- Goal: Extend the OPNsense mock target so the whole feature can be exercised in tests and the containerised demo without a KVM guest.
- Done when: `pytest tests/test_mock_targets.py` passes with new cases covering cert issuance, both export modes, and the certless/`certref`-empty distinction.

## Sub-tasks
1. [done] Add `trust`/`openvpn` state to `MockState` in `na_sso/mock_targets/app.py` (CAs, certs, servers) and clear it in `reset()` — done when: `reset()` empties the new stores and existing mock tests still pass.
2. [done] Implement `GET /api/openvpn/export/providers`, `/templates`, `/accounts/{vpnid}` and `GET /api/openvpn/instances/get/{uuid}` shaped exactly like the phase-0001 fixtures — done when: each mock response validates against its captured fixture's key set.
3. [done] Implement `POST /api/trust/cert/add`, `POST /api/trust/cert/del/{uuid}`, `GET /api/trust/cert/search` and `GET /api/trust/ca/ca_list`, generating a real self-signed keypair via `cryptography` — done when: an added cert appears in `export/accounts` for the matching `caref` only when its `commonname` matches a mock user.
4. [done] Implement `POST /api/openvpn/export/download/{vpnid}[/{certref}]` returning `{result, filename, filetype, content}` with base64 content — done when: omitting `certref` yields a config with `auth-user-pass` and no `<cert>`, passing a valid one inlines `<cert>`/`<key>`, and passing an empty string errors like the real API.
5. [done] Reproduce the write-privilege and missing-`openvpn_export` failure modes recorded in phase-0001 — done when: a POST without an `openvpn_export` body returns `{"result": "failed"}` and no content.
6. [done] Seed the mock with one CA and one server so the demo works out of the box — done when: `tests/test_mock_targets.py` asserts a provider is listed on a freshly reset mock.

## Log
- (append-only, one line per event)
- delegated to Codex (strong tier) per user instruction; ledger bookkeeping, commits and verification retained by the orchestrator
- delegation attempt failed before starting: harness permission classifier denied `codex exec`; tree still clean, no phase work performed
- executed by Codex (gpt-5.6-sol) under delegation; 728 lines added to app.py, 246 to tests, DOX feature map updated
- ORCHESTRATOR VERIFICATION (independent, not delegate's claim): ran `pytest tests/test_mock_targets.py` -> 41 passed; full `pytest` -> 262 passed in 3:42; `ruff check` clean. Read the download/accounts/cert-add logic directly.
- fixture-equality tests are genuine: direct == against all four captured GET fixtures, plus a real cert-matches-key crypto assertion and the trailing-empty==no-cert equivalence
- FIDELITY DIVERGENCE (accepted, logged for phase-0003): the mock is STRICTER than the real 26.7 API. Passing the server cert's refid as a client certref is REJECTED by the mock ("Certificate does not belong to server CA"), whereas the live API accepted it and leaked the server key (invariant 7). The mock therefore cannot reproduce that specific hazard. This is fine: the connector guard is "only ever use a self-minted refid with CN==username", which is testable without the mock reproducing the bug. But phase-0003's live smoke test remains the only place invariant 7 is exercised against reality.
- MINOR for phase-0004: `_opnsense_openvpn_download` falls back to `str(server["hostname"])`, which yields the literal "None" if neither the request nor the seeded server supplies a hostname. Harmless now (tests pass a hostname; the real seed value is set), but phase-0004 makes hostname a required admin field, which closes it properly.
- mock conventions phase-0003 will consume: unprivileged key is `forbidden-key`/`forbidden-secret` (persistent 403); one-shot 403 via POST `/__mock__/fail/opnsense-forbidden`; `cert/del` takes the cert UUID (not refid), so the connector must map refid->uuid for deletion

