# phase-0001 — Demo firewall CA + OpenVPN server, and captured API ground truth

- Status: done
- Depends on: none
- Goal: Give the demo firewall a real CA and OpenVPN server, and capture the real API request/response transcripts that every later phase is built against.
- Done when: `openvpn/export/providers` on the demo firewall returns a server whose `caref` is the new CA, and JSON transcripts for providers, templates, accounts, cert-add, cert-export and certless-export are saved under `tests/fixtures/opnsense_openvpn/`.

**Cross-repo note:** provisioning scripts live in `na-sso-live-demo/opnsense/`, owned by the PARENT repo `na-sso-project`. Commit those files there, separately; they are outside this ledger's branch and squash range. Only the captured fixtures under `tests/` belong to this repo.

## Sub-tasks
1. [done] Bring the demo firewall up and confirm API reachability — done when: `curl -sk -u "$key:$secret" https://localhost:10443/api/trust/cert/search` returns HTTP 200 with a JSON body.
2. [done] Add `provision-openvpn.sh` under `na-sso-live-demo/opnsense/` that creates a CA (`trust/ca/add`) and a server certificate (`trust/cert/add`, `cert_type: server_cert`) via the API, not the serial console — done when: rerunning the script on an already-provisioned firewall exits 0 without creating duplicates.
3. [done] Extend that script to create an OpenVPN server Instance with `authmode` set and `verify_client_cert` requiring a cert — done when: `GET /api/openvpn/export/providers` lists the instance and its `caref` equals the CA from sub-task 2.
4. [done] Capture transcripts for `export/providers`, `export/templates`, `export/accounts/$vpnid`, and `instances/get/$uuid` — done when: four JSON files exist under `tests/fixtures/opnsense_openvpn/`.
5. [done] Manually issue one client cert with `commonname` equal to an existing demo username, then capture both `export/download/$vpnid/$certref` and `export/download/$vpnid` (no certref) — done when: two more fixtures exist, the cert-bearing one base64-decodes to a config containing an inline `<key>` block, and the certless one contains `auth-user-pass` and no `<cert>` block.
6. [done] Confirm the read-only-key failure mode — done when: the exact HTTP status and body returned by `export/download` for a key lacking write privilege is recorded in this phase Log, so phase-0003 can map it to a clear message.

## Log
- sub-task 1 done: `compose-helper.sh up -d opnsense` (service-scoped pass-through, not the full demo stack); API answered HTTP 200 after ~20s
- confirmed live: OPNsense 26.7; `export/templates` returns exactly ArchiveOpenVPN / PlainOpenVPN / ViscosityVisz as predicted from source
- baseline firewall state: zero CAs, one cert (the Web GUI TLS server cert), zero OpenVPN servers
- sub-tasks 2+3 done: `na-sso-live-demo/opnsense/provision-openvpn.sh` (PARENT repo) creates CA + server cert + Instance via the REST API, not the serial console. Rerun exits 0 and reuses all three.
- demo handles: caref `6a5fdc1533f7f`, server certref `6a5fdc35d15a1`, export vpnid `1c030500-62d0-4b62-b3d2-d6a953bad087`
- posture confirmed live: `verify_client_cert=require` + `authmode=Local Database`, and `export/providers` reports `mode: server_tls_user`
- GOTCHA: the Instance model has its OWN required numeric `vpnid` (here `1`), which is NOT the export handle. For Instances, `export/providers` keys entries by the config NODE UUID. phase-0003 must use the UUID, never the numeric field.
- GOTCHA: `instances/get` returns OptionField values as selection dicts (`{"require": {"value":..., "selected":1}}`), while `export/providers` returns flat strings. The connector needs to normalise both shapes.
- GOTCHA: never send `Content-Type: application/json` on a GET — OPNsense parses the empty body and answers 400 "Invalid JSON syntax".
- GOTCHA: `trust/ca/search` and `trust/cert/search` return `prv`/`prv_payload` — full PRIVATE KEYS in plaintext. Captured fixtures MUST have key material replaced with placeholders before being committed; git history is forever even for demo-only keys.
- NEW REQUIREMENT for phase-0004: the instance has no `interface`, so `export/providers` reports `hostname: null` and an exported config would have no usable `remote`. The export preset `hostname` must therefore be an admin-settable field in the UI. Amend phase-0004 accordingly before starting it.
- GOTCHA for phases 0002/0003: `export/providers` returns `[]` (a JSON *array*) when no server exists, but an *object* keyed by vpnid when populated. The connector and mock must both tolerate the array-when-empty shape.
- sub-task 4 done: 4 sanitized fixtures captured; all `crt`/`prv`/`crt_payload`/`prv_payload` replaced with `<REDACTED>` before writing
- FINDING (design-validating, security-relevant): `export/accounts` lists EVERY cert sharing the server's caref — including the SERVER certificate — with `users: []` for non-matching ones. Worse, passing the server cert's refid to `export/download` SUCCEEDS and inlines the server's private key into a client config; the source's `cert_type` guard does not fire in practice on 26.7. Therefore na-sso must NEVER accept a caller-supplied certref: the connector may only use a refid it minted itself with CN == username. Phase-0003 sub-task 4 must enforce this.
- sub-task 5 done: with certref -> 4619 bytes containing `<cert>`,`<key>`,`<ca>` AND `auth-user-pass` (the intended 2FA bundle), filename `na_sso_demo_VPN_j_baking.ovpn`; without certref -> 1512 bytes with `<ca>` + `auth-user-pass` only
- CORRECTION to GOAL.md invariant 3: an EMPTY trailing certref segment (`/download/{uuid}/`) does NOT error — routing collapses it to null, so it behaves exactly like omitting it. Only a non-empty INVALID certref raises. Invariant 3 as originally drafted was wrong; the safe rule is simply "omit the segment for password-only mode".
- error shapes for phase-0003 mapping: invalid certref -> HTTP 500 `{"errorMessage":"Client certificate not found","errorTitle":"OpenVPN export"}`; missing `openvpn_export` body -> HTTP 200 `{"result":"failed"}` with no content
- sub-task 6 done: an API key whose user holds no privileges gets a uniform HTTP 403 `{"status":403,"message":"Forbidden"}` on `export/providers`, `export/download` AND `trust/cert/add`. Test user `vpn-readonly` was deleted afterwards; firewall users are back to root + j-baking.
- NOTE for phase-0004: because discovery itself 403s on an under-privileged key, `export/providers` is a sufficient and side-effect-free privilege check for the common misconfiguration. phase-0004 sub-task 5 should reconsider whether a real config-writing download is still warranted, or whether discovery + `validate_presets` suffices.
- NOTE for phase-0005: docker-compose publishes only 8006 and 10443. A real client connection test needs `1194:1194/udp` added to the opnsense service in `na-sso-live-demo/docker-compose.yaml` (PARENT repo).

