# GOAL — CI pipeline and browser-suite findings polish

## Goal
- Goal ID: 20260723-ci-and-findings-polish
- Outcome: The unit and browser suites run automatically in CI on every push/PR, and the four product findings recorded by the browser-verification goal are fixed with their browser-test assertions tightened so the suite enforces each fix.
- Done when: a GitHub Actions workflow runs two jobs (unit `pytest -q`; browser chromium install + `pytest -m browser tests/browser/`) and validates locally with commands mirroring docs/DEVELOPER.md; the four findings are fixed — (1) server password errors programmatically associated beside the field, (2) admin-reset notice names the temporary-password/CHPW handoff, (3) My-access headings show display names in legacy env-connector mode, (4) no document overflow from the 390px dashboard drawer — each verified by an extended browser assertion; roadmap observations updated to resolved; full unit + browser suites pass; first live CI run confirmed green after merge+push.
- Goal status: completed
- Goal status meaning: drafting | approved | executing | blocked-on-human | awaiting-acceptance | completed | abandoned
- Last completed phase: phase-0005

## Git
- Repository: yes
- Strategy: isolated-branch
- Starting branch: main
- Work branch: goal/20260723-ci-and-findings-polish
- Baseline commit: c233d6398db2c02039e03ec78f063bf82e68ba26
- Starting upstream at start: origin/main@c233d6398db2c02039e03ec78f063bf82e68ba26
- Work upstream at start: none

## Phases
- [done] phase-0001 — CI workflow: two-job GitHub Actions pipeline, locally validated
- [done] phase-0002 — P1.5 fix: beside-field server-error association
- [done] phase-0003 — Notice and naming fixes: admin-reset handoff wording; legacy-mode display names
- [done] phase-0004 — Dashboard drawer overflow fix at 390px
- [done] phase-0005 — Docs, DOX pass, full verification; post-merge live CI check

## Handoff
- Current position: completed — user accepted 2026-07-23; 11 goal commits squashed, merged to main, branch deleted, pushed
- Next action: none (the first live CI run's result is recorded in the Log by a follow-up ledger commit — the run can only start after this snapshot is pushed)
- Last verified evidence: none; repo has NO CI today (.github absent, verified); live CI green can only be confirmed after the final merge+push (goal branch stays unpublished to preserve squash preconditions)
- Blockers: none

## Log
- created ledger with 5 phases
- supersedes completed goal 20260723-in-tree-browser-verification (retained in Git history)
- Gate A approved by user 2026-07-23 ("approved, create the branch, and go" — covers Gates A–C) after choosing this over email/SMS notifications (queued as the next feature goal) and self-service requests (blocked behind email/SMS by recorded decision)
- delegation directive (standing): codex strong-tier implements slices; orchestrator keeps ledger bookkeeping, diff review, gate runs, commits; live CI confirmation is orchestrator-owned
- findings source: docs/NEXT-PHASE.md "Future product review observations" (recorded 2026-07-23 by the browser-verification goal)
- user accepted 2026-07-23; squash preconditions verified (11/11 Goal-ID, no merges, branch unpublished); squashed to one snapshot, ff-merged to main, branch deleted, pushed; .goal-ledger retained; the deferred phase-0005 live-CI check is recorded below by a follow-up commit once the run finishes (it cannot precede the push that triggers it)
