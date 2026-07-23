# phase-0003 — Admin visibility + safety for email deliveries

- Status: done
- Depends on: phase-0002
- Goal: Surface email deliveries in the /notifications admin page alongside webhooks with retry parity, and prove no secret or PII leakage in payloads, audits, or the admin view.
- Done when: /notifications lists email deliveries with channel, recipient, status, attempts, and last error; the existing retry action works for failed/disabled email deliveries; SMTP credentials never render and are never stored in the delivery payload; recipient display is present but no message body/secret is exposed in audit detail; a browser or route test asserts the email delivery row renders; unit + browser suites pass.

## Sub-tasks
1. [done] Admin page email rows + retry parity (delegate: codex) — done when: notifications.html/route render email deliveries with channel + recipient + status and the retry endpoint accepts email deliveries the same way as webhooks.
2. [done] Leakage review + assertions (delegate: codex) — done when: a test asserts SMTP password never appears in payload/audit/rendered page and that audit detail carries no message body; orchestrator reviews the rendered fields line by line.
3. [done] Phase verification (orchestrator) — done when: orchestrator-run full unit suite green and browser suite passes twice; validator (with git) clean.

## Log
- (append-only, one line per event)
- codex: notifications.html gains a read-only Email channel card (from_address, tls_mode, enabled badge, events; NEVER username/password) in the Destinations section; deliveries desktop table + mobile cards gain a Channel column and show delivery.recipient for email rows (endpoint_id for webhooks); th scope=col added; no-destinations alert now covers email too
- codex: notification_delivery_retry intercepts channel=='email' before the webhook endpoint lookup — requires email_channel enabled, resets to pending/attempt_count=0/next_attempt_at=now, audits email.retry_requested; webhook retry path byte-for-byte unchanged
- codex tests: sentinel SMTP username/password asserted ABSENT from rendered /notifications HTML; email.delivered/email.failed audit detail asserted free of message body; email card + recipients + '>email<' channel asserted present; email AND webhook failed rows both retryable via the endpoint (reset to pending with correct audits)
- orchestrator diff review: retry branch cleanly isolated; template renders no SMTP creds; Channel column lives in the scrolling table-wrap (no overflow); tests are genuine
- orchestrator gates (independent): full unit 302 passed, 19 deselected (was 300; +2); browser 19 passed TWICE (independence); ruff clean; validator (with git) clean
- housekeeping: removed 21M untracked report/automated-tests trace dir codex emitted during its browser run (ephemeral evidence, not a deliverable)
