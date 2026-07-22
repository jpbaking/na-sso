# GOAL — Connector Contract 1.1: capability-declared unsupported operations

## Goal
- Goal ID: 20260723-contract-declared-unsupported-ops
- Outcome: Connectors declare per-operation support up front in Connector Contract 1.1, so unsupported operations (today: Jenkins disable) are surfaced in the UI, dry-run plans, and reconciliation previews before execution, and planning records an explicit unsupported outcome instead of a failed attempt.
- Done when: contract version is 1.1 with per-operation support flags; Jenkins declares disable unsupported; unassignment/offboarding planning skips the doomed disable and records `unsupported` without a failed operation attempt; assignment/unassignment UI warns beforehand; dry-run and reconciliation previews include the limitation; the API serializer, `docs/CONNECTORS.md`, and the roadmap traceability are updated; the full test suite passes.
- Goal status: completed
- Goal status meaning: drafting | approved | executing | blocked-on-human | awaiting-acceptance | completed | abandoned
- Last completed phase: phase-0004

## Git
- Repository: yes
- Strategy: isolated-branch
- Starting branch: main
- Work branch: goal/20260723-contract-declared-unsupported-ops
- Baseline commit: 622d16ddc014ff60006dd9c1c5535a949675615b
- Starting upstream at start: origin/main@622d16ddc014ff60006dd9c1c5535a949675615b
- Work upstream at start: none

## Phases
- [done] phase-0001 — Contract 1.1: per-operation support flags, Jenkins declaration, conformance tests
- [done] phase-0002 — Planning/sync honors declared-unsupported operations
- [done] phase-0003 — Operator surfacing: UI warnings and API exposure
- [done] phase-0004 — Docs, DOX pass, and full verification

## Handoff
- Current position: completed — user accepted 2026-07-23; 9 goal commits squashed into one snapshot and merged to main; goal branch deleted per user instruction
- Next action: none
- Last verified evidence: full suite 294 passed (orchestrator run) with docs + DOX updates in place; contract 1.1 flags live end to end (contract → sync/dry-run/reconciliation → UI warnings → /api/v1/targets)
- Blockers: none

## Log
- created ledger with 4 phases
- supersedes completed goal 20260722-opnsense-openvpn-self-service (retained in Git history)
- Gate A approved by user 2026-07-23 ("approved, create the branch, and go" — covers Gates A–C); priorities delegated to the agent, which chose the sole deferred roadmap item (NEXT-PHASE.md "Deferred future work")
- delegation directive from user: use cross-CLI delegates (codex strong-tier for code-heavy phase work, agy fast-tier for bounded read/summarize tasks); orchestrator keeps ledger bookkeeping, diff review, test runs, and commits
- execution record: agy delivered the consumer inventory (verified by grep); codex session 019f8b0c-4b3f-7362-a973-17ea5a6e33ce implemented all four slices; one delegate miss (stale 1.0 hardcode in test_api.py) caught by orchestrator gates and fixed by the same session; every diff orchestrator-reviewed; full suite green at every phase close (289→292→294→294)
- user accepted 2026-07-23; all squash preconditions verified (9/9 commits carry Goal-ID, no merges, branch unpublished); squashed to one snapshot on the baseline, merged to main, branch deleted per user instruction; .goal-ledger retained
