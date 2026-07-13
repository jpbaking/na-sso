# oneauth/mock_targets

## Purpose

Stateful protocol mocks for experiencing and testing One Auth without OPNsense, Nexus Repository, or Nextcloud installations.

## Ownership

Owns the optional mock-target FastAPI service and its in-memory target state. Production connector behavior remains in `../connectors/`; application persistence and orchestration remain in the parent package.

## Local Contracts

- Implement only the target API shapes exercised by the production connectors; this is not a general emulator for any target product.
- Keep OPNsense, Nexus, and Nextcloud state isolated and in memory. Restarting the service intentionally resets remote demo state.
- Default credentials are public demo values and must never be presented as production-safe secrets.
- The Compose demo must not publish mock API or control endpoints to the host.
- `/__mock__/reset` and one-shot `/__mock__/fail/{target}` controls are deterministic test/demo aids for the private Compose network and loopback tests.
- Protocol changes must remain aligned with `../connectors/` and receive direct API plus real-HTTP connector coverage.

## Verification

- Run `.venv/bin/pytest -q tests/test_mock_targets.py` from the repository root.
- Validate and smoke-test the demo through the compose-helper commands documented in the root README.

## Feature Map

- **Stateful target API emulation** — Implements the connector-facing OPNsense Auth User, Nexus Security User, and Nextcloud OCS Provisioning routes. Start: `app.py`.
- **Demo controls and readiness** — Provides health, deterministic reset, and one-shot target failure behavior for repeatable workflows. Start: `app.py`.

## Child DOX Index

- (none)
