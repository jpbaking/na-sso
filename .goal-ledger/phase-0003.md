# phase-0003 — Notice and naming fixes: admin-reset handoff wording; legacy-mode display names

- Status: done
- Depends on: none
- Goal: Make the admin-reset completion notice state the temporary-password/CHPW handoff, and make My-access target headings resolve connector display names in legacy env-connector mode; tighten the matching browser assertions.
- Done when: after an admin reset with a generated password, the post-save notice names the handoff (temporary password set; user must change it at next sign-in); My access shows human display names (not raw IDs) for legacy env-configured connectors; browser tests assert both; unit + browser suites pass.

## Sub-tasks
1. [done] Admin-reset notice wording (delegate: codex) — done when: the feedback notice names the temporary-password/CHPW handoff only when a credential reset actually occurred.
2. [done] Legacy-mode display-name resolution — done when: My access headings use connector display names regardless of config mode.
3. [done] Tighten browser assertions — done when: test_passwords.py asserts the new notice wording and display-name headings.

## Log
- (append-only, one line per event)
- codex: edit success notice conditional on bool(password.strip()) — reset wording names the temporary password + change-at-next-sign-in; blank-password edits keep the old message byte-for-byte; restore already had correct wording; no feedback.py/template change
- auth.py account_page(): resolution order YAML definition → connector display_name/target_type → raw ID; modern-mode behavior untouched; legacy harness now renders OPNsense/Nexus Repository headings, asserted by the tightened My-access journey
- unit CRUD route test tightened to prove both notice variants (suite count unchanged, justified)
- orchestrator verification: browser 19 passed twice; full unit 294 passed, 19 deselected
