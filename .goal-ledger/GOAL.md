# GOAL — Example CSV template and target_ids modal for bulk onboarding

## Goal
- Goal ID: 20260721-bulk-csv-template-targets
- Outcome: The bulk onboarding/offboarding page offers a downloadable example CSV pre-filled with real configured target IDs, and the `target_ids` token in the column hint opens a modal listing available targets with their IDs.
- Done when: `/users/bulk/import` renders a clickable `target_ids` link that opens a modal table of enabled connectors (target_id, display name, type); a template download returns a CSV whose `target_ids` values are real configured IDs and that the existing bulk preview validator accepts; the test suite passes.
- Goal status: completed
- Goal status meaning: drafting | approved | executing | blocked-on-human | awaiting-acceptance | completed | abandoned
- Last completed phase: phase-0005

## Git
- Repository: yes
- Strategy: isolated-branch
- Starting branch: main
- Work branch: goal/20260721-bulk-csv-template-targets
- Baseline commit: f66a053b11569943afd1b7e30b9be5091c6dde14
- Starting upstream at start: origin/main@f66a053b11569943afd1b7e30b9be5091c6dde14
- Work upstream at start: none

## Phases
- [done] phase-0001 — Template CSV endpoint built from configured targets
- [done] phase-0002 — Bulk import page: target_ids modal and template download
- [done] phase-0003 — Tests, demo verification, DOX pass
- [done] phase-0004 — Require every CSV field except target_ids
- [done] phase-0005 — Declutter the CSV column instructions

## Handoff
- Current position: completed
- Next action: none
- Last verified evidence: 243 tests pass; demo shows the column table, modal opens, template round-trips
- Blockers: none

## Log
- created ledger with 3 phases
- Gate A/B/C approved by user ("start"); isolated branch created from f66a053
- phase-0001 done: template CSV endpoint live
- phase-0002 done: target_ids modal trigger, targets table and CSV download on the page
- phase-0003 done: tests, demo verification and DOX pass complete; goal awaiting acceptance
- user added scope after review: require every CSV field except target_ids (Add User parity); phase-0004 appended
- phase-0004 done: all CSV fields required except target_ids; goal awaiting acceptance
- user feedback: the required-columns hint is too crowded; phase-0005 appended
- phase-0005 done: upload card instructions restructured as a column table; goal awaiting acceptance
- user accepted 2026-07-21; goal commits squashed into one snapshot commit and merged to main
