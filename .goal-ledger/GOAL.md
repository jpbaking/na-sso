# GOAL — Configurable bulk import limits

## Goal
- Goal ID: 20260721-configurable-bulk-limits
- Outcome: The bulk row cap is a per-deployment configuration value, and the CSV upload byte cap is derived from it instead of being an independent round number.
- Done when: `bulk_import_policy.max_rows` in the config file drives both the row limit and the derived upload byte limit (2 KiB per row), no import-time constant remains, the page hint and rejection messages report the configured values, `docs/PRODUCTION.md` matches, and the test suite passes.
- Goal status: completed
- Goal status meaning: drafting | approved | executing | blocked-on-human | awaiting-acceptance | completed | abandoned
- Last completed phase: phase-0003

## Git
- Repository: yes
- Strategy: isolated-branch
- Starting branch: main
- Work branch: goal/20260721-configurable-bulk-limits
- Baseline commit: 615d9301399c6ba95ba29a629d3281066a0b5ef6
- Starting upstream at start: origin/main@615d9301399c6ba95ba29a629d3281066a0b5ef6
- Work upstream at start: none

## Phases
- [done] phase-0001 — BulkImportPolicy config with a derived byte cap
- [done] phase-0002 — Report the configured limits in the UI, errors and docs
- [done] phase-0003 — Boundary tests, full suite, DOX pass

## Handoff
- Current position: completed
- Next action: none
- Last verified evidence: 247 tests pass, including four boundary tests against a real capped config file
- Blockers: none

## Log
- created ledger with 3 phases
- supersedes completed goal 20260721-bulk-csv-template-targets (retained in Git history)
- Gate A/B/C approved by user; per-row byte allowance chosen as 2 KiB (default upload cap becomes 2 MiB)
- phase-0001 done: limits now come from bulk_import_policy
- phase-0002 done: UI, errors and docs report the configured limits
- phase-0003 done: boundary tests, suite and DOX complete; goal awaiting acceptance
- user accepted 2026-07-21; goal commits squashed into one snapshot commit and merged to main
