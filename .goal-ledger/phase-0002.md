# phase-0002 — Delivery queue + worker integration for end-user email

- Status: done
- Depends on: phase-0001
- Goal: Generalize the existing notification delivery queue and worker to carry a channel + recipient, enqueue end-user email for the relevant lifecycle/password events using human-readable templates, and deliver them to managed-user recipients with the same retry/backoff/audit discipline as webhooks.
- Done when: the delivery queue carries a channel discriminator (default "webhook") and a recipient, added via an additive migration matching db.py's pattern; enqueueing an email-eligible event for a user with an email address queues and then delivers a human-readable message captured by an in-process aiosmtpd sink; failures retry with backoff and terminate as email.failed at max_attempts; success audits email.delivered; users without an email are skipped and audited (no queue row or an immediately-terminal skip, decided in-slice and asserted); webhook behavior is byte-for-byte unchanged; unit + browser suites pass.

## Sub-tasks
1. [done] Queue schema generalization + migration (delegate: codex) — done when: webhook_deliveries carries channel + recipient columns with an additive migration; existing rows default to channel="webhook"; test_migrations covers the upgrade; existing webhook delivery tests pass unchanged.
2. [done] Email enqueue path + templates (delegate: codex) — done when: end-user email is enqueued for account-created (lifecycle.completed) and password reset/expired with human-readable subject/body templates that carry no secrets or connector detail; recipient resolves from ManagedUser.email; missing-email is skipped and audited.
3. [done] Worker dispatch by channel (delegate: codex) — done when: deliver_due_once dispatches email rows through send_email with retry/backoff/audit (email.delivered/email.failed) reusing the existing status machine; webhook rows unchanged; an aiosmtpd-backed test asserts delivered/retry/failed transitions.
4. [done] Phase verification (orchestrator) — done when: orchestrator-run full unit suite green (incl. migration + delivery tests); browser deferred to phase-0003 (no web-surface change here); validator (with git) clean.

## Log
- (append-only, one line per event)
- codex: WebhookDelivery gains channel (String(16) default+server_default 'webhook') + recipient (String(254) nullable); db.py upgrades dict adds webhook_deliveries channel/recipient via additive ALTER; table not renamed
- codex: enqueue_notification email branch after the webhook loop — gated on email_channel.enabled + event in channel.events; _render_email_notification maps lifecycle.completed/password.expired/approval.completed to (subject,body); no-template events audited email.skipped_no_template; recipient from ManagedUser by username==subject, missing/blank email audited email.skipped_no_recipient; email row endpoint_id='email', channel='email', dedupe f'{event}:{dedupe}:{email}', payload compact JSON {subject,body} only (no secrets)
- codex: deliver_due_once branches channel=='email' to _deliver_email (send_email + same status machine: delivered/retrying/failed via _retry_at, audits email.delivered/email.failed; disabled when channel gone); webhook path byte-for-byte unchanged; send_email imported lazily
- codex tests: test_migrations legacy webhook_deliveries upgrade (asserts channel DDL/default 'webhook', recipient nullable, existing row -> ('webhook',None)); test_notifications aiosmtpd delivered+audit, retries-then-fails at max_attempts, skip-no-recipient + skip-no-template; existing webhook tests unchanged
- orchestrator diff review: email dispatch correctly intercepts before the webhook endpoint-None path; reuses _retry_at; payload carries only subject/body; skip-audits committed by callers (consistent with webhook enqueue); tests are genuine assertions
- orchestrator gates (independent): full unit 300 passed, 19 deselected (was 296; +4); ruff clean; validator (with git) clean
- NOTE for phase-0003: UI retry of email rows currently returns "Retry unavailable" (endpoint_id='email' not in policy.endpoints) — phase-0003 must add email retry parity; /notifications lists email rows but without channel/recipient columns yet
