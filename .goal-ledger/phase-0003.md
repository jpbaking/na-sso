# phase-0003 — Boundary tests, full suite, DOX pass

- Status: done
- Depends on: phase-0002
- Goal: Lock the configurable behaviour with tests and close the docs.
- Done when: tests cover the default and a custom cap at the N/N+1 boundary for both CSV and API paths, the full suite passes, and the owning DOX docs are current.

## Sub-tasks
1. [done] Test the default cap and a custom `max_rows` for the CSV path at N and N+1 — done when: tests pass.
2. [done] Test the API path rejects above the configured cap — done when: test passes.
3. [done] Test the derived byte cap rejects an oversized upload — done when: test passes.
4. [done] Run the full suite — done when: `.venv/bin/pytest -q` is green.
5. [done] DOX pass for `na_sso/DOX.md` — done when: the bulk entry reflects the configurable limits.

## Log
- added a capped_client fixture that loads a real config file (max_rows 5, row_byte_allowance 512), so the tests exercise the config wiring rather than a monkeypatched accessor
- 4 tests: derived cap arithmetic, CSV path at N and N+1, API path above cap returns 422, oversized upload rejected with the configured label
- full suite 247 passed
- na_sso/DOX.md: bulk entry now names bulk_import_policy and the derived byte cap
