# phase-0003 — Safety and handoff journeys

- Status: done
- Depends on: phase-0001
- Goal: Cover the trust/safety contracts: root affordances, form preservation, target-credential recovery, and generated-secret/SSH handoff gating.
- Done when: browser tests prove (a) root never exposes nonfunctional edit/delete controls and can reach My account; (b) failed create/update/restore preserves safe fields and shows focused errors; (c) invalid target credentials remain expanded with an inline recovery path; (d) a generated password and a browser-enrolled SSH key cannot be committed before handoff confirmation.

## Sub-tasks
1. [done] Root affordances + My account journey (delegate: codex) — done when: assertions cover absent root controls and the account menu path.
2. [done] Form preservation and focused errors journey — done when: failed create/update/restore keeps safe fields/selections and focuses an error summary.
3. [done] Target-credential failure recovery journey — done when: an invalid-credential probe leaves the target expanded with sanitized inline error and working retry/Test connection.
4. [done] Generated-secret and SSH handoff gating journey — done when: commit controls stay disabled until explicit saved/confirmed handoff in both flows.

## Log
- codex (same session) delivered four journeys in tests/browser/test_safety.py; credential journey uses a NEW function-scoped modern_target_config fixture (temp YAML + NA_SSO_CONFIG_FILE swap with settings-cache clears both directions) because legacy env connectors bypass the credential UI; mock Nexus real Basic-auth (401 on bad secret) drives failure/recovery
- product findings reported, NOT fixed (per instruction): (1) P1.5 nuance — server password rejection lands in the focused #error-summary; the beside-field signal is the live policy checklist, with no server-authored .field-error/aria-invalid association; (2) browser SSH key download suggests extensionless filename na-sso_ed25519. Both queued for user at acceptance
- orchestrator verification: browser suite 8 passed twice consecutively (27.1s/27.5s); diff reviewed (fixture +30 lines, test_safety.py 248 lines, 4 tests)
- phase check: full unit suite 294 passed, 8 deselected (orchestrator run)
