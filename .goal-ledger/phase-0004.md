# phase-0004 — Mailpit demo profile (self-contained live inbox)

- Status: done
- Depends on: phase-0002
- Goal: Add a self-contained Mailpit service to the demo compose stack, point the demo's email channel at it, and document the live "trigger a reset → watch the inbox" flow — all via compose-helper, never raw docker compose.
- Done when: docker-compose-demo.yaml defines a Mailpit service (SMTP 1025, web UI 8025) with no external/network dependency; the demo na-sso config enables the email_channel pointed at mailpit:1025; bringing the demo up via compose-helper and triggering an end-user email event produces a message visible in Mailpit's web inbox at :8025; the demo doc records the exact steps; the stack is cleaned down via compose-helper afterward.

## Sub-tasks
1. [done] Mailpit compose service (delegate: codex) — done when: a mailpit service is added to docker-compose-demo.yaml (pinned image, SMTP + web UI ports, healthcheck), na-sso-demo depends on it and its email_channel targets mailpit:1025 with no auth/TLS for the local demo.
2. [done] Live demo verification (orchestrator) — done when: orchestrator brings the demo up via compose-helper, triggers an end-user email event, confirms the message appears in Mailpit's inbox (API or UI), then stops the stack via compose-helper (demo-stop, volumes preserved — NOT down).
3. [done] Demo documentation (delegate: codex) — done when: the demo doc describes enabling email, the Mailpit URL, and the trigger→inbox flow.
4. [done] Phase verification (orchestrator) — done when: compose config validates via compose-helper; validator (with git) clean; ledger records the observed inbox evidence.

## Log
- (append-only, one line per event)
- codex: docker-compose-demo.yaml adds mailpit service (axllent/mailpit:v1.30.5 pinned, restart unless-stopped, ONLY web UI published 127.0.0.1:8025:8025, SMTP 1025 internal); na-sso-demo depends_on mailpit condition service_started (healthcheck omitted — minimal image, brittle probe); demo-ssh.sh injects notification_policy{enabled:true, email_channel{enabled:true, host:mailpit, port:1025, from_address:na-sso@demo.local, tls_mode:none, events:[lifecycle.completed,password.expired,approval.completed]}} into the generated /demo/na-sso.yaml before targets:
- codex: docs/DEMO.md 'Inspect demo email' section (self-contained inbox, http://127.0.0.1:8025, demo-rebuild -> trigger -> watch inbox, stop via demo-stop not demo-down); DOX.md demo feature-map entry notes the Mailpit inbox; compose 'demo-compose config --quiet' exit 0; notification_policy YAML FileConfig-accepted; sh -n clean
- orchestrator LIVE verification via compose-helper: demo-rebuild rebuilt na-sso:local + started stack detached (mailpit pulled v1.30.5 OK); in-container trigger (create ManagedUser mailpit-demo with email + enqueue lifecycle.completed) => queued 1, deliver_due_once processed 1; container reported notif.enabled=True, email_channel=(True,'mailpit',1025,'none')
- orchestrator inbox EVIDENCE: Mailpit API http://127.0.0.1:8025/api/v1/messages total=1 — To mailpit-demo@demo.local, From na-sso@demo.local, Subject 'Your NA-SSO account is ready', body the human-readable template; full path config->enqueue->worker->send_email->mailpit:1025 proven end-to-end, self-contained (no external mail server)
- orchestrator: stack stopped with './compose-helper.sh demo-stop' (containers removed, na-sso-demo-data volume PRESERVED; demo-down deliberately NOT used); validator (with git) clean
