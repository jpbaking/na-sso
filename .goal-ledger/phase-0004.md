# phase-0004 — Docs, DOX pass, and full verification

- Status: done
- Depends on: phase-0003
- Goal: Update `docs/CONNECTORS.md` for Contract 1.1, move the roadmap's deferred entry into delivered traceability, run the DOX update pass, and verify the whole suite.
- Done when: `docs/CONNECTORS.md` documents the 1.1 flags and third-party guidance; `docs/NEXT-PHASE.md` records the delivery in traceability and drops/annotates the deferred entry; affected DOX.md feature maps are current; the full pytest suite passes.

## Sub-tasks
1. [done] Update `docs/CONNECTORS.md` for Contract 1.1 — done when: capability table and extension guidance cover the per-operation flags.
2. [done] Update `docs/NEXT-PHASE.md` traceability — done when: the deferred entry is recorded as delivered with evidence pointers.
3. [done] DOX pass over changed subtrees — done when: `na_sso/DOX.md`, `na_sso/connectors/DOX.md` (if present), and the root feature map reflect any feature/file changes, or are confirmed unaffected.
4. [done] Full verification — done when: the complete pytest suite passes and all delegate output has been reviewed by the orchestrator.

## Log
- codex (same session) updated CONNECTORS.md (1.1 heading, Lifecycle operation support section with Jenkins example, consumer list, third-party guidance) and NEXT-PHASE.md (dated header note, contract row 1.0→1.1, new delivered traceability row, deferred section replaced by delivery pointer); orchestrator fact-checked every claim against the reviewed phase 1–3 diffs
- orchestrator DOX pass (not delegated): root DOX.md feature map (1.0→1.1), na_sso/DOX.md child index (1.0→1.1), na_sso/connectors/DOX.md local contracts (version bump + lifecycle-operation declaration rule); tests/DOX.md and feature-map file lists confirmed unaffected (no feature/file relocations)
- phase check: full suite 294 passed (orchestrator run); every delegate diff across all four phases was orchestrator-reviewed and independently gate-tested
