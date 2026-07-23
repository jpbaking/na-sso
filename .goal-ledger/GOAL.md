# GOAL — SMTP email notification channel (end-user delivery)

## Goal
- Goal ID: 20260724-email-notification-channel
- Outcome: NA-SSO delivers human-readable email notifications to managed users for their own lifecycle/password events, reusing the existing notification queue's retry/audit machinery, demoable live via a self-contained Mailpit inbox and tested in-process with aiosmtpd — no external service dependency.
- Done when: an `email` channel (SMTP host/port/from/TLS/optional creds + event allowlist) validates in config; enqueued end-user events for a user with an email address are delivered to an aiosmtpd sink with correct recipient/subject/body, retried on failure, and audited (email.delivered/email.failed); users without an email are skipped and audited; the /notifications admin page shows email deliveries with retry parity; the demo compose profile brings up Mailpit (SMTP 1025, web UI 8025) and a demo reset produces a message visible in its inbox; docs + DOX + example config updated; full unit + browser suites pass and the first live CI run after merge stays green.
- Goal status: completed
- Goal status meaning: drafting | approved | executing | blocked-on-human | awaiting-acceptance | completed | abandoned
- Last completed phase: phase-0005

## Git
- Repository: yes
- Strategy: isolated-branch
- Starting branch: main
- Work branch: goal/20260724-email-notification-channel
- Baseline commit: 85134b0c6adc52af6151d87d551cfea1475d386a
- Starting upstream at start: origin/main@85134b0c6adc52af6151d87d551cfea1475d386a
- Work upstream at start: none

## Phases
- [done] phase-0001 — SMTP send primitive + email channel config
- [done] phase-0002 — Delivery queue + worker integration for end-user email
- [done] phase-0003 — Admin visibility + safety for email deliveries
- [done] phase-0004 — Mailpit demo profile (self-contained live inbox)
- [done] phase-0005 — Docs, DOX, full verification; post-merge live CI check

## Handoff
- Current position: completed — user accepted 2026-07-24; 11 goal commits squashed to one snapshot, merged to main, branch deleted, pushed
- Next action: none (the first live CI run's result is recorded in the Log by a follow-up ledger commit — the run can only start after this snapshot is pushed)
- Last verified evidence: orchestrator-run full unit 302 passed / 19 deselected; browser 19 passed; phase-0004 live Mailpit inbox capture (total=1, To mailpit-demo@demo.local, Subject 'Your NA-SSO account is ready'); validator clean
- Blockers: none

## Log
- created ledger with 5 phases
- supersedes completed goal 20260723-ci-and-findings-polish (retained in Git history)
- scope decision: SMS deferred — no local-mock equivalent exists and any live SMS demo needs an external provider, conflicting with the no-external-dependencies constraint; the channel abstraction is built so SMS can drop in later; email satisfies the end-user-reachability precondition for self-service access requests
- delegation directive (standing): codex strong-tier implements slices; orchestrator keeps ledger bookkeeping, diff review, gate runs, commits; live CI confirmation is orchestrator-owned
- design intent: generalize the existing webhook_deliveries queue/worker with a channel + recipient discriminator (additive column migration per db.py pattern) rather than building a parallel email queue; recipients derive from ManagedUser.email
- Gate A + B + C approved by user 2026-07-24 ("I approve, proceed as you see fit"; "Good on delegation")
- all 5 phases delivered via codex strong-tier slices with independent orchestrator gate runs; phase-0004 live-verified the end-to-end path by capturing a real end-user message in the self-contained Mailpit demo inbox
- user accepted 2026-07-24; squash preconditions verified (11/11 Goal-ID, no merges, branch unpublished); squashed to one snapshot, ff-merged to main, branch deleted, pushed; .goal-ledger retained; the deferred phase-0005 live-CI check is recorded below by a follow-up commit once the run finishes (it cannot precede the push that triggers it)
- deferred phase-0005 check SATISFIED 2026-07-24: live CI run 30047834479 on main GREEN — Unit 3m51s, Browser 1m29s (https://github.com/jpbaking/na-sso/actions/runs/30047834479); email-channel + Mailpit-demo changes verified in CI
