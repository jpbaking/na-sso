# phase-0001 — Contract 1.1: per-operation support flags, Jenkins declaration, conformance tests

- Status: done
- Depends on: none
- Goal: Extend `ConnectorContract` with per-operation support flags (supported by default), bump the contract version to 1.1, have Jenkins declare disable unsupported, and update conformance tests.
- Done when: `na_sso/connectors/base.py` publishes contract 1.1 with per-operation support flags; the Jenkins connector declares disable unsupported while retaining its execution-time refusal as defense in depth; `tests/test_connector_contract.py` asserts the flags for all nine connectors; the connector-contract test module passes.

## Sub-tasks
1. [done] Inventory every consumer of `ConnectorContract` / `contract_metadata()` (delegate: agy, read-only) — done when: a report lists each consumer file:line so later phases know the full serializer/UI surface.
2. [done] Extend `ConnectorContract` with per-operation support flags and bump the version to 1.1 (delegate: codex) — done when: base.py exposes the flags with supported-by-default semantics and `contract_metadata()` serializes them.
3. [done] Jenkins declares disable unsupported — done when: the Jenkins contract carries the flag and the existing execution-time failure message remains.
4. [done] Update conformance tests for 1.1 — done when: `pytest tests/test_connector_contract.py` passes with per-connector flag assertions.

## Log
- agy inventory delivered and orchestrator-verified by independent grep: sole production consumer is na_sso/api.py:291 (asdict into /api/v1/targets response); report at scratchpad/agy-contract-consumers.md
- codex (session 019f8b0c-4b3f-7362-a973-17ea5a6e33ce) implemented flags as class-attribute booleans (ensure_supported/disable_supported/delete_supported, default True on Connector), version 1.1, Jenkins override, strengthened conformance tests keyed by connector_type with asdict round-trip
- orchestrator gate run caught a miss outside codex's gates: tests/test_api.py:208 hardcoded contract version "1.0"; finding sent back to the same codex session, fixed by asserting CONNECTOR_CONTRACT_VERSION
- note: `codex exec resume` rejects -C; resume without it (session keeps its cwd)
- orchestrator-verified gates: test_connector_contract + test_sync + test_api = 23 passed
- phase check: full suite 289 passed (230s); repo-wide grep confirms no remaining "1.0" contract-version hardcodes
