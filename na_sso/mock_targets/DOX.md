# na_sso/mock_targets

## Purpose

In-process FastAPI mock implementations of the external targets, used by the Compose demo and the test suite.

## Ownership

Owns the mock target app only. Real adapters live in `../connectors/`; demo orchestration in the repo-root Compose files and `docs/DEMO.md`.

## Local Contracts

- Mocks emulate each target's user-management API surface closely enough for connectors to run unchanged; never add behavior the real target lacks.
- No real network calls or persistence beyond process state.

## Verification

- `.venv/bin/pytest -q tests/test_mock_targets.py`

## Feature Map

- **Mock target app** — all mock targets in one FastAPI app, including OPNsense user, trust/certificate, lifetime-required persistent CRL rebuild with CRL-referenced delete protection, and OpenVPN client-export APIs. Start: `app.py`.

## Child DOX Index

- (none)
