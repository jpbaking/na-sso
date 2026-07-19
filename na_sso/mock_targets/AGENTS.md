# na_sso/mock_targets

## Purpose

Stateful protocol mocks for experiencing and testing NA-SSO without OPNsense, Nexus Repository, Nextcloud, Jenkins, GitLab, Gitea, Immich, or Nginx Proxy Manager installations.

## Ownership

Owns the optional mock-target FastAPI service and its in-memory target state. Production connector behavior remains in `../connectors/`; application persistence and orchestration remain in the parent package.

## Local Contracts

- Implement only the target API shapes exercised by production connectors or explicitly staged for connector integration; this is not a general emulator for any target product.
- Keep every target type's state isolated and in memory. Restarting the service intentionally resets remote demo state.
- Default credentials are public demo values and must never be presented as production-safe secrets.
- The Compose demo publishes the mock service only on host loopback port 9000 for its control page; never bind it to a non-loopback host interface.
- `/__mock__/reset` and one-shot `/__mock__/fail/{target}` controls are deterministic test/demo aids for the private Compose network and loopback tests.
- Each target has one in-memory availability switch that consistently succeeds or fails every connector-facing request for that target.
- Protocol changes must receive direct API coverage and remain aligned with `../connectors/`; add real-HTTP connector coverage when the matching connector exists.

## Verification

- Run `.venv/bin/pytest -q tests/test_mock_targets.py` from the repository root.
- Validate and smoke-test the demo through the compose-helper commands documented in the root README.

## Feature Map

- **Stateful target API emulation** — Implements the connector-facing OPNsense Auth User, Nexus Security User, Nextcloud OCS Provisioning, Jenkins local-realm, GitLab Users/moderation, Gitea administrator Users, Immich administrator Users, and Nginx Proxy Manager v2.15.1 token/user routes, including role/group membership payloads where supported. Start: `app.py`.
- **Demo controls and readiness** — Provides a lazyway.io browser page with independent whole-target availability switches plus deterministic health/reset and one-shot failures. Start: `app.py`.

## Child DOX Index

- (none)
