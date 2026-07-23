# GOAL — In-tree browser verification suite

## Goal
- Goal ID: 20260723-in-tree-browser-verification
- Outcome: The 11 end-to-end contracts in the roadmap's Verification backlog are covered by an in-tree, headless Playwright suite that runs the real app against the in-process mock targets, making the browser journeys that caught the original audit's defects durable regression tests.
- Done when: `pytest -m browser` runs a Playwright suite covering all 11 backlog contracts against a live app instance backed by mock targets; the suite is deterministic (no live demo stack or network needed); the fast unit suite stays unaffected by default; docs record how to run it; the roadmap's backlog section records delivery; the full suite (unit + browser) passes.
- Goal status: completed
- Goal status meaning: drafting | approved | executing | blocked-on-human | awaiting-acceptance | completed | abandoned
- Last completed phase: phase-0006

## Git
- Repository: yes
- Strategy: isolated-branch
- Starting branch: main
- Work branch: goal/20260723-in-tree-browser-verification
- Baseline commit: 09b57fec1dc94e5d218ff2b6807ad327caf933c5
- Starting upstream at start: origin/main@622d16ddc014ff60006dd9c1c5535a949675615b
- Work upstream at start: none

## Phases
- [done] phase-0001 — Playwright harness: dependency, live-server fixture, browser marker, smoke journey
- [done] phase-0002 — Lifecycle and state-truth journeys
- [done] phase-0003 — Safety and handoff journeys
- [done] phase-0004 — Password journeys and user access truth
- [done] phase-0005 — Responsive and accessibility journeys
- [done] phase-0006 — Docs, DOX pass, and full verification

## Handoff
- Current position: completed — user accepted 2026-07-23; 13 goal commits squashed into one snapshot, merged to main, branch deleted, pushed
- Next action: none
- Last verified evidence: final orchestrator runs — unit 294 passed 19 deselected, browser 19 passed; all 11 backlog contracts mapped to committed test files in docs/NEXT-PHASE.md
- Blockers: none

## Log
- created ledger with 6 phases
- supersedes completed goal 20260723-contract-declared-unsupported-ops (retained in Git history)
- Gate A approved by user 2026-07-23 ("approved, create the branch, and go" — covers Gates A–C) after user reviewed the disposition of the two smaller items (env-creds finding removal 09b57fe; email/SMS precondition recorded in NEXT-PHASE.md)
- delegation directive (standing, from previous goal): codex strong-tier implements phases 1–5 (Playwright is in its routing profile); agy fast-tier for bounded read-only inventories; orchestrator keeps ledger bookkeeping, diff review, gate runs, commits; phase-0005 accessibility claims get independent orchestrator verification
- scope source: docs/NEXT-PHASE.md "Verification backlog" — 11 contracts; baseline note: origin/main is behind local main (622d16d vs 09b57fe) because recent main commits are unpushed
- user accepted 2026-07-23 ("clean-up and down; commit and squash then merge into main (delete branch), then push"); demo compose stacks brought down per instruction; all squash preconditions verified (13/13 Goal-ID, no merges, branch unpublished); squashed, ff-merged to main, branch deleted, pushed; .goal-ledger retained
