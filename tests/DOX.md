# tests

## Purpose

Behavioral pytest suite covering configuration, security, connectors, lifecycle, API, and demo flows.

## Ownership

Owns all tests and fixtures here. Application contracts live in `na_sso/DOX.md` and below.

## Local Contracts

- Shared fixtures live in `conftest.py`; the `client` fixture builds an isolated app with a tmp SQLite DB and bootstrap admin env vars — use it rather than constructing apps ad hoc.
- Test files map one-to-one to application areas (`test_<module>.py`); add new coverage in the matching file.
- Connector tests use the mock target app and `respx`; no real network access.

## Verification

- `.venv/bin/pytest -q`

## Child DOX Index

- (none)
