# phase-0003 — Operator surfacing: UI warnings and API exposure

- Status: done
- Depends on: phase-0002
- Goal: Warn operators before they trigger a declared-unsupported operation (assignment/unassignment/offboarding UI) and expose per-operation support through the API contract metadata.
- Done when: the assignment/unassignment/offboarding UI shows a text warning for targets whose connector declares an operation unsupported ("this target cannot disable accounts — delete instead"); the API exposes the per-operation flags in contract/target metadata; tests assert both.

## Sub-tasks
1. [done] Assignment/unassignment/offboarding UI warns before the operation (delegate: codex) — done when: templates render the warning for a declared-unsupported target and a test asserts it.
2. [done] API serializer exposes per-operation support in contract metadata (delegate: codex, same task) — done when: the API response includes the flags and a test asserts it.
3. [done] Warning presentation is text-equivalent, not color-only, matching house accessibility style — done when: the warning carries explicit text reviewed against existing status patterns.

## Log
- codex (same session) chose the three genuine decision surfaces: user create/edit form (ensure warnings beside target choices; disable warnings on edit), delete confirmation (delete limitations for assigned targets), bulk preview (operation-appropriate warnings; unassign correctly maps to the disable warning)
- one canonical OPERATION_WARNING_TEXT table + view-model helpers in users.py; templates receive bounded warning strings — no parallel rendering path; existing .field-hint/.alert alert-info/.data-list patterns, plain text (not color-only)
- api_contract.py inspected, unchanged (generic envelopes; no field enumeration); asdict path already exposes flags — tests/test_api.py now contractually asserts ensure/disable/delete_supported in /api/v1/targets
- orchestrator reviewed full diff; gate run: test_users + test_api + test_sync + test_inventory = 62 passed
- phase check: full suite 294 passed (orchestrator run)
