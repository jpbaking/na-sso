# GOAL — OPNsense OpenVPN client-config self-service

## Goal
- Goal ID: 20260722-opnsense-openvpn-self-service
- Outcome: An admin can enable OpenVPN client-config self-service on an OPNsense target through the na-sso web UI, and an assigned user can download their own `.ovpn` — with an OPNsense-issued client certificate when the server requires one — with the certificate revoked automatically on offboarding.
- Done when: against the live demo firewall, an admin configures OpenVPN on `opnsense_demo` entirely through the UI (no YAML change), a managed user downloads a working `.ovpn` from their account page and can download it again, disabling that user places the certificate on the CA's CRL so an already-distributed config stops working, and the full test suite passes.
- Goal status: completed
- Goal status meaning: drafting | approved | executing | blocked-on-human | awaiting-acceptance | completed | abandoned
- Last completed phase: phase-0006

## Git
- Repository: yes
- Strategy: isolated-branch
- Starting branch: main
- Work branch: goal/20260722-opnsense-openvpn-self-service
- Baseline commit: f258fa2aa9055b5094e362f8c106edf1158a48d0
- Starting upstream at start: origin/main@f258fa2aa9055b5094e362f8c106edf1158a48d0
- Work upstream at start: none

## Phases
- [done] phase-0001 — Demo firewall CA + OpenVPN server, and captured API ground truth
- [done] phase-0002 — Mock target endpoints mirroring the captured transcripts
- [done] phase-0003 — Connector capability: discovery, cert issuance, config export
- [done] phase-0004 — Persisted per-target OpenVPN settings and admin UI
- [done] phase-0005 — User-facing self-service `.ovpn` download
- [done] phase-0006 — Certificate revocation on offboarding

## Handoff
- Current position: ALL phases terminal AND all sub-tasks done (the phase-0005 tunnel test was upgraded skipped->done via a container OpenVPN client — real tunnel established). Goal awaiting user acceptance.
- Next action: none
- Last verified evidence: live OPNsense 26.7 provisioned with CA 6a5fdc1533f7f, server cert 6a5fdc35d15a1, instance 1c030500-62d0-4b62-b3d2-d6a953bad087; both export modes produced correct configs (4619 B with cert bundle, 1512 B password-only); 8 sanitized fixtures captured
- Blockers: none. User chose delete+CRL: deletion is authoritative and live-verified on the demo; CRL is best-effort for production and unit-tested against a mock (crl/set 500s on the demo, cannot be live-verified there).

## Log
- created ledger with 6 phases
- supersedes completed goal 20260721-configurable-bulk-limits (retained in Git history)
- Gate A approved by user 2026-07-22; plan accepted as drafted, no changes requested
- Gate B: isolated branch goal/20260722-opnsense-openvpn-self-service created from clean main at f258fa2
- Gate C approved by user; execution started
- phase-0001 done: demo firewall provisioned via API and ground truth captured; two drafted assumptions corrected against the live API (invariant 3, and the caller-supplied-certref hazard now recorded as invariant 7)
- user design decisions 2026-07-22 (recorded before phase-0002):
  (a) offboarding revokes via CRL and attaches the CRL to the OpenVPN instance — deleting a cert does NOT invalidate an already-distributed config, since OpenVPN validates against the CA. phase-0006 sub-task 1 now covers the CRL wiring, which phase-0001's provisioning script must be extended to create.
  (b) re-download is allowed and returns the same certificate; the Goal's Done-when was amended from "exactly once" accordingly. Rationale: OPNsense holds the key, not na-sso, so show-once buys little and costs an admin ticket per device wipe.
  (c) phase-0004 verification uses discovery + validate_presets only, writing nothing to the firewall.
- cross-repo note: phase-0001 also edits `na-sso-live-demo/opnsense/`, which belongs to the PARENT repo `na-sso-project`, not to this submodule. Those files are committed separately in the parent repo and are deliberately outside this ledger's branch and squash range. User approved this split during drafting.

- user accepted 2026-07-23; feature docs (README, DEMO, PRODUCTION) updated; 20 goal commits squashed into one snapshot with the Goal-ID trailer and merged to main; .goal-ledger retained

## Design reference (verified against OPNsense core source, 26.7 era)

Endpoints this goal relies on, all core — no plugin required:

- `GET /api/openvpn/export/providers` — servers keyed by `vpnid`; covers legacy `openvpn-server` entries and new Instances (there `vpnid` is the instance UUID). Carries `caref`, `mode`, hostname.
- `GET /api/openvpn/export/templates` — export provider keys. Shipped: `PlainOpenVPN`, `ArchiveOpenVPN`, `ViscosityVisz`.
- `GET /api/openvpn/export/accounts/$vpnid` — certs whose `caref` matches the server CA, each with matching usernames.
- `POST /api/openvpn/export/validate_presets/$vpnid` — validates without saving. No `throwReadOnly()`, no `serializeToConfig()`.
- `POST /api/openvpn/export/download/$vpnid[/$certref]` — returns `{result, filename, filetype, content}` where `content` is base64.
- `POST /api/trust/cert/add`, `POST /api/trust/cert/del/$uuid`, `GET /api/trust/cert/search`, `GET /api/trust/ca/ca_list`.
- `GET /api/openvpn/instances/get/$uuid` — exposes `authmode` and `verify_client_cert` for Instances.

Invariants that constrain the implementation:

1. **CN is the user link.** The modern `Auth/User` model has no `cert` field. `accountsAction` matches `cert.commonname` against system usernames. Certificates MUST be issued with `commonname == username`.
2. **Key must stay on the firewall.** `trust/cert/add` with `private_key_location: firewall` (the default). `local` does not persist the key and export cannot inline it.
3. **Password-only means omitting the `certref` path segment.** `downloadAction` proceeds without client cert data when `$certref` is null. (Corrected in phase-0001 against the live 26.7 API: a *trailing empty* segment collapses to null and behaves identically, so it is harmless — but only a non-empty valid refid produces a certificate bundle, and a non-empty invalid one raises HTTP 500 "Client certificate not found".)
4. **`download` writes config.** It calls `storePresetsAction`, which persists export presets and calls `throwReadOnly()`. The API key needs write privilege on VPN: OpenVPN: Client Export, and every download mutates `config.xml`.
5. **`download` requires a POST body** containing `openvpn_export` with at least `template`; without it the response stays `{"result":"failed"}` and no content is generated.
6. **Auth posture is server-side, not per-user.** na-sso reads it (`mode` for legacy, `authmode`/`verify_client_cert` via `instances/get` for Instances) rather than letting an admin assert it.
7. **Never pass a caller-supplied certref.** Verified in phase-0001: `export/download` will happily export the SERVER certificate's private key as a client config; the source's `cert_type` guard does not fire on 26.7. The connector may only use a refid it minted itself with CN == username.

Cert issuance payload shape:

```json
{"cert": {"action": "internal", "caref": "<server caref>", "cert_type": "usr_cert",
          "commonname": "<username>", "descr": "na-sso <username> <target_id>",
          "key_type": "2048", "digest": "sha256", "lifetime": 397,
          "private_key_location": "firewall", "country": "NL"}}
```

Existing code this goal builds on:

- `na_sso/connectors/opnsense.py` — connector, basic auth via `_client()`.
- `na_sso/status.py:196` `configure_target` — save-then-probe admin flow to mirror.
- `na_sso/models.py:128` `TargetCredential` — the YAML-declares-shape / DB-holds-runtime-state split.
- `na_sso/auth.py:367` — show-once secret delivery with `Cache-Control: no-store`, template `private_key_once.html`.
- `na_sso/mock_targets/app.py:369` — existing OPNsense mock (`/api/auth/user/*` only today).
- `na-sso-live-demo/opnsense/` — real OPNsense 26.7 nano KVM guest; API on `https://localhost:10443`, credentials in `apikey.json`.
