# phase-0005 — Docs, DOX, full verification; post-merge live CI check

- Status: done
- Depends on: phase-0001, phase-0002, phase-0003, phase-0004
- Goal: Document the email channel across the example config and product docs, run the DOX pass, verify everything locally, mark the self-service-requests precondition satisfied, and confirm the first live CI run after acceptance merge+push.
- Done when: the example config documents email_channel; docs (DEVELOPER/PRODUCTION/NEXT-PHASE) describe the email channel, the Mailpit demo, and the SMS deferral; affected DOX docs are current; orchestrator-run unit + browser suites pass; the roadmap records email end-user notification delivered and the self-service-requests precondition satisfied; after acceptance merge+push, the first GitHub Actions run on main is confirmed green and recorded in the ledger.

## Sub-tasks
1. [done] Example config + product docs (delegate: codex) — done when: na-sso.yaml.example documents a commented email_channel; DEVELOPER/PRODUCTION/NEXT-PHASE cover the channel, the Mailpit demo, and the recorded SMS deferral; the self-service precondition is marked satisfied with evidence.
2. [done] DOX pass — done when: affected DOX docs (root feature map, na_sso, tests) are current or confirmed unaffected.
3. [done] Full local verification (orchestrator) — done when: orchestrator-run unit and browser suites are green.
4. [skipped — reason: cannot run before the acceptance merge+push; deferred to the goal completion procedure, where the first live run on main must be confirmed green and recorded in GOAL.md before the goal is marked completed] Post-merge live CI confirmation (orchestrator) — done when: after the user accepts and main is pushed, the first workflow run on main is green (gh run watch/list) and recorded in GOAL.md before completion.

## Log
- (append-only, one line per event)
- codex (docs only): docs/PRODUCTION.md gains an 'Email notifications' subsection (email_channel fields, end-user recipient resolution, skip auditing, write-only password, Channel-aware Notifications visibility + retry parity, reused queue/retry/audit, webhook-vs-email payload guarantees); docs/NEXT-PHASE.md adds the 'SMTP end-user email' delivered-capability row, rewrites the self-service sequencing paragraph (email delivered 2026-07-24; SMS optional/deferred) and the decision note (email satisfies end-user-reachability precondition, demo-verified via Mailpit; SMS deferred — no local mock; self-service no longer blocked on reachability); docs/DEVELOPER.md updates persistence + notification architecture and adds the in-process aiosmtpd + Mailpit test note; na-sso.yaml.example already correct (unchanged)
- orchestrator DOX pass: na_sso/DOX.md Notifications entry already current (end-user email + email_delivery.py); root DOX.md Demo entry already names the Mailpit inbox (phase-0004); notifications were never a root-map top-level entry (webhooks aren't either — detail lives in na_sso/DOX.md), so no root-map email row added; tests/DOX.md is convention-based (test_<module>.py one-to-one) and the new tests/test_email_delivery.py conforms — unaffected; all DOX current
- orchestrator final local verification: full unit 302 passed, 19 deselected; browser 19 passed; validator (with git) clean
- sub-task 4 deferred to the completion procedure with user-visible reason (the first live run on main can only start after the acceptance push); the check is deferred, not dropped
- sub-task 4 SATISFIED 2026-07-24: post-acceptance CI run 30047834479 on main GREEN (Unit 3m51s, Browser 1m29s) — https://github.com/jpbaking/na-sso/actions/runs/30047834479
