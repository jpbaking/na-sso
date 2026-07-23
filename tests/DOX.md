# tests

## Purpose

Behavioral pytest suite covering configuration, security, connectors, lifecycle, API, and demo flows, plus a headless Playwright browser suite under `browser/`.

## Ownership

Owns all tests and fixtures here. Application contracts live in `na_sso/DOX.md` and below.

## Local Contracts

- Shared fixtures live in `conftest.py`; the `client` fixture builds an isolated app with a tmp SQLite DB and bootstrap admin env vars — use it rather than constructing apps ad hoc.
- Test files map one-to-one to application areas (`test_<module>.py`); add new coverage in the matching file.
- Connector tests use the mock target app and `respx`; no real network access.
- Browser tests live in `browser/`, carry the registered `browser` marker (excluded from the default run), and use `browser/conftest.py`'s session-scoped loopback live-server fixture (real app + in-process mock targets); they stay deterministic and offline. Run instructions: `docs/DEVELOPER.md`.

## Verification

- `.venv/bin/pytest -q`
- `.venv/bin/pytest -m browser tests/browser/` (requires `playwright install chromium`)

## Child DOX Index

- (none)
