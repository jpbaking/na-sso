# phase-0002 — Planning/sync honors declared-unsupported operations

- Status: done
- Depends on: phase-0001
- Goal: Make unassignment/offboarding planning consult the contract flags, skip declared-unsupported operations, and record an explicit terminal `unsupported` outcome without a failed operation attempt; include the limitation in dry-run plans and reconciliation previews.
- Done when: a unit test proves that a declared-unsupported disable is never attempted against the connector yet lands in the terminal `unsupported` sync state; dry-run plans and reconciliation previews label the operation unsupported before execution; the execution-time unsupported path still works for undeclared cases.

## Sub-tasks
1. [done] Sync/unassignment planning consults contract flags and records terminal `unsupported` without calling the connector (delegate: codex) — done when: a test asserts no connector call occurs and the sync state is `unsupported`.
2. [done] Dry-run plans include declared-unsupported outcomes — done when: dry-run plan output labels the operation unsupported before execution, with a test.
3. [done] Reconciliation previews include the limitation — done when: a reconciliation preview test shows the unsupported marker.
4. [done] Regression: execution-time unsupported handling still covers undeclared cases — done when: existing sync tests pass unchanged or with justified adaptations.

## Log
- codex (same session) implemented: shared Connector.lifecycle_operation_for()/supports_operation()/unsupported_operation_detail(); sync branches to terminal unsupported BEFORE start_target_attempt (no attempt row, no retry, audit action sync.<op>.unsupported); dry-run plan_user() prepends an unsupported blocker; reconcile.py selects the same operation (incl. pending-disable variants) and mark_unsupported_operation() converts repairable drift to DriftState.UNSUPPORTED so previews never offer a doomed repair
- execution branches now consume the same selected operation string, so selection and execution cannot diverge; execution-time VALIDATION fallback untouched (test_validation_failure_is_terminal_unsupported_without_retry still passes)
- operation summary distinguishes "N failed" from "N unsupported"; unsupported still counts toward failed_targets (requested outcome not achieved) — deliberate, documented in delegate report
- orchestrator reviewed full production+test diff; gate run: test_sync + test_reconciliation + test_connector_contract + test_lifecycle = 34 passed
- phase check: full suite 292 passed (orchestrator run, 231s)
