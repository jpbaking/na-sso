# phase-0004 — Require every CSV field except target_ids

- Status: done
- Depends on: phase-0003
- Goal: Align bulk CSV validation with the Add User page — username, action, display_name and email are required; target_ids may be empty.
- Done when: a CSV missing any of the five columns is rejected outright, an onboard row with a blank display_name or email is marked invalid with a clear reason, offboard rows may leave display_name and email blank, the hint and modal copy match, and the suite passes.

## Sub-tasks
1. [done] Require all five column headers in `_parse_csv` — done when: a CSV lacking any of them is rejected with a message naming the missing columns.
2. [done] Enforce non-empty display_name and email on onboard rows in `preview_bulk_workflow` — done when: such rows are invalid with a specific detail and offboard rows are unaffected.
3. [done] Update the page hint and modal copy to state the new contract — done when: the hint no longer calls display_name/email optional.
4. [done] Update the CSV template so its onboard rows satisfy the stricter rules — done when: the template still passes preview validation.
5. [done] Tests for the header requirement and the onboard field requirement — done when: new tests pass and the full suite is green.

## Log
- user chose: display_name/email required on onboard rows only; offboard rows may leave them blank
- CSV_COLUMNS constant added; _parse_csv now names the missing columns in its rejection message
- onboard rows without a display name or email are invalid with a specific detail; offboard rows unaffected
- hint and modal copy restated; the CSV template already satisfied the stricter onboard rules
- 2 tests added; full suite 243 passed
- demo re-verified: template round-trips (303), a username/action-only CSV is rejected with "missing required column(s): display_name, email, target_ids"
- DOX unchanged: the feature entry already covers bulk.py and templates/bulk_import.html; this was a validation-rule change, not a new feature
