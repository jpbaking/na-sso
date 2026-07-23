# phase-0005 — Docs, DOX pass, full verification; post-merge live CI check

- Status: done
- Depends on: phase-0001, phase-0004
- Goal: Update the roadmap observations to resolved, document CI in docs/DEVELOPER.md, run the DOX pass, verify everything locally, and confirm the first live CI run after acceptance merge+push.
- Done when: docs/NEXT-PHASE.md marks the four observations resolved with evidence; docs/DEVELOPER.md CI note points at the actual workflow; affected DOX docs are current; orchestrator-run unit + browser suites pass; after the user accepts and main is pushed, the first GitHub Actions run is confirmed green (recorded in the ledger log before goal completion).

## Sub-tasks
1. [done] Roadmap + developer docs updates (delegate: codex) — done when: observations marked resolved; CI documented against the real workflow file.
2. [done] DOX pass — done when: affected DOX docs current or confirmed unaffected.
3. [done] Full local verification — done when: orchestrator-run unit and browser suites are green.
4. [skipped — reason: cannot run before the acceptance merge+push; deferred to the goal completion procedure, where the first live run on main must be confirmed green and recorded in GOAL.md before the goal is marked completed] Post-merge live CI confirmation (orchestrator) — done when: the first workflow run on main is green (gh run watch/list) after acceptance push.

## Log
- (append-only, one line per event)
- codex updated NEXT-PHASE.md (observations → dated resolved record incl. the CORRECTED overflow root cause; CI-added note; P2.13 evidence now cites the in-tree responsive suite instead of the stale not-kept-in-tree caveat) and DEVELOPER.md (CI paragraph points at the real two-job workflow)
- orchestrator DOX pass: root DOX.md feature map gains a Continuous integration entry (.github/workflows/ci.yml); tests/DOX.md already current; others unaffected
- orchestrator final local runs: browser 19 passed; unit 294 passed, 19 deselected
- sub-task 4 re-scoped to the completion procedure with user-visible reason (see status line); the check is deferred, not dropped
