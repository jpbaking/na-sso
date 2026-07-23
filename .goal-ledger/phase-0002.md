# phase-0002 — P1.5 fix: beside-field server-error association

- Status: done
- Depends on: none
- Goal: Make server-side password rejection (and any sibling server-side field errors on the user form) programmatically associated with the field — visible beside-field error plus aria-describedby/aria-invalid — and tighten the browser assertion to enforce it.
- Done when: a rejected create/update renders a field-adjacent error associated to the input via aria-describedby (and aria-invalid="true") while keeping the focused error summary; the browser form-preservation test asserts the association; unit + browser suites pass.

## Sub-tasks
1. [done] Server-authored field error with ARIA association (delegate: codex) — done when: template/route render the associated beside-field error on rejection.
2. [done] Tighten the browser assertion — done when: tests/browser/test_safety.py asserts aria-describedby/aria-invalid and the visible field error text.
3. [done] Regression: summary focus and field preservation unchanged — done when: existing assertions still pass.

## Log
- (append-only, one line per event)
- codex delivered a presentation-only parallel field_errors mapping in users.py (_form_error + _identity_error_field); user_form.html and user_action.html (restore had the same gap) render .field-error + aria-invalid + appended aria-describedby, preserving password-checks; #error-summary untouched; no feedback.py or CSS change (house classes existed)
- unit tests tightened at existing route tests (justified); browser assertions extended for create AND restore
- orchestrator verification: browser 19 passed twice; full unit 294 passed, 19 deselected
