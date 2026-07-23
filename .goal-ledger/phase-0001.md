# phase-0001 — SMTP send primitive + email channel config

- Status: done
- Depends on: none
- Goal: Add an async SMTP send primitive and an `email_channel` configuration block on NotificationPolicy, both fully unit-tested against an in-process aiosmtpd sink, with no wiring into the live enqueue/worker path yet.
- Done when: `aiosmtplib` is a declared dependency; NotificationPolicy carries a validated `email_channel` (enabled, host, port, from address, TLS mode, optional username/password SecretStr, event allowlist reusing NotificationEvent); a pure `send_email()` delivers a message that an in-process aiosmtpd controller captures with the expected From/To/Subject/body; config validation rejects malformed channels; unit + browser suites pass (browser unaffected).

## Sub-tasks
1. [done] Add aiosmtplib dependency + aiosmtpd dev dependency (delegate: codex) — done when: pyproject declares aiosmtplib (runtime) and aiosmtpd (dev/test); `pip install -e '.[dev]'` resolves; import smoke passes.
2. [done] email_channel config model (delegate: codex) — done when: NotificationPolicy has an optional `email_channel` with host/port/from/tls-mode/optional creds/enabled/events; a model_validator rejects an enabled channel missing host/from and enforces a valid TLS mode; example config documents it (commented).
3. [done] Pure send_email() primitive (delegate: codex) — done when: an async send function builds a correct MIME message and sends via aiosmtplib; a unit test stands up aiosmtpd in-process, calls send_email, and asserts captured From/To/Subject/body; TLS-mode selection is unit-covered without requiring a real cert.
4. [done] Phase verification (orchestrator) — done when: orchestrator-run full unit suite green and browser suite unaffected; validator (with git) clean.

## Log
- (append-only, one line per event)
- codex: aiosmtplib>=3.0 (runtime) + aiosmtpd>=1.4 (dev) in pyproject; EmailChannel(StrictModel) with enabled/host/port(25,1..65535)/from_address(email-regex)/tls_mode Literal[none,starttls,tls]/username/password SecretStr/events; model_validator requires host+from_address when enabled, rejects dup events + malformed from_address; NotificationPolicy.email_channel: EmailChannel|None = None (existing configs unaffected)
- codex: na_sso/email_delivery.py send_email(channel,*,to,subject,body) builds EmailMessage and aiosmtplib.send with use_tls=(tls=='tls'), start_tls=(tls=='starttls') — auto-STARTTLS disabled for 'none' so the plain aiosmtpd sink works; auth passed only when username/password set
- codex tests: tests/test_email_delivery.py (in-process aiosmtpd Controller captures From/To/Subject/body) + test_config.py (valid parse incl. password round-trip; 5 invalid cases: missing host, missing from, bad tls_mode, malformed from_address, dup events); na_sso/DOX.md notification entry notes the SMTP primitive + email_delivery.py
- orchestrator diff review: send primitive/config/tests idiomatic and correctly scoped; no web-surface change
- orchestrator gates (independent): full unit 296 passed, 19 deselected (was 294; +2); browser 19 passed; validator (with git) clean; ruff/git diff --check clean per codex
