# phase-0006 — Docs, DOX pass, and full verification

- Status: done
- Depends on: phase-0005
- Goal: Document how to install browsers and run the suite, record the backlog delivery in the roadmap, run the DOX pass, and verify everything.
- Done when: docs cover install/run (including CI notes) for the browser suite; docs/NEXT-PHASE.md records the Verification backlog as delivered with evidence; tests/DOX.md and any affected DOX docs are current; the default unit suite and `pytest -m browser` both pass in orchestrator runs.

## Sub-tasks
1. [done] Run/install documentation (delegate: codex) — done when: a developer can go from clean checkout to green browser suite following the docs.
2. [done] Roadmap backlog delivery record — done when: NEXT-PHASE.md maps the 11 contracts to their test files.
3. [done] DOX pass over changed subtrees — done when: tests/DOX.md (verification + feature map) and any parent docs reflect the browser suite, or are confirmed unaffected.
4. [done] Full verification — done when: orchestrator-run unit suite and browser suite are both green and all delegate output has been reviewed.

## Log
- (append-only, one line per event)
- codex (same session) documented the suite in docs/DEVELOPER.md (idiomatic home — README routes there; local-setup commands now include playwright install chromium; 11 contracts enumerated; architecture, a11y-fallback, and CI notes) and added the dated delivery table to docs/NEXT-PHASE.md mapping all 11 contracts to test files, plus the SSE-fix note and the four future-review observations
- orchestrator DOX pass (not delegated): tests/DOX.md purpose/local-contracts/verification updated for the browser suite; root DOX.md and na_sso/DOX.md confirmed unaffected (no feature/file relocations; tests/browser/ is a grouping dir, not a boundary)
- phase check (orchestrator runs): unit suite 294 passed, 19 deselected; browser suite 19 passed — all delegate output across six phases reviewed
