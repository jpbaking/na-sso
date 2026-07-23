# phase-0004 — Password journeys and user access truth

- Status: done
- Depends on: phase-0001
- Goal: Cover the password-journey contracts (temporary, normal, reset, expired-change, expired-keep each state their outcome before redirecting) and the managed user's Account page truth (assigned targets and accurate propagation state).
- Done when: browser tests walk all five password journeys asserting the stated outcome before redirect, and a managed user's Account page shows assigned targets with propagation state matching the seeded backend state.

## Sub-tasks
1. [done] Five password journeys (delegate: codex) — done when: temporary, normal, reset, expired-change, and expired-keep each assert their outcome notice before redirect.
2. [done] User Account access-truth journey — done when: assigned targets and propagation/retry/mode states on the Account page match seeded reality, including a failing target.

## Log
- (append-only, one line per event)
- codex (same session) delivered six journeys in tests/browser/test_passwords.py; no fixture/config change needed (fixture PasswordPolicy already: 90d expiry, grace mode, 14d, limit 1); expired arrangements mirror expire_due() persisted state with 120d-old password and model-computed due assertion
- P1.8 keep journey verifies DB truth: hash age unchanged, password_keep_until equals the UI-promised date, acknowledgement 1 of 1, restriction cleared; My-access journey computes expected payload from committed rows via the shared presentation contract and asserts login mutated nothing
- product findings reported, NOT fixed: (3) admin-reset post-save notice is generic ("Changes saved") — does not state a temporary password was set/CHPW required; (4) legacy env-connector mode: My access headings fall back to raw target IDs (display names resolve only from YAML definitions). Queued for acceptance
- orchestrator verification: browser suite 14 passed twice consecutively (45.3s/44.8s); scope = one new test file only
- phase check: full unit suite 294 passed, 14 deselected (orchestrator run)
