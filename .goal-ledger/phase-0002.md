# phase-0002 — Lifecycle and state-truth journeys

- Status: done
- Depends on: phase-0001
- Goal: Cover the state-truth contracts: static/SSE render parity for every server state, forced-outage retry/recovery under one correlated operation, and the delete/restore/purge race rules.
- Done when: browser tests prove (a) every server state renders identically before and after an event update; (b) a forced target outage shows retry timing, manual retry, recovery, and one correlated operation; (c) delete during CHPW completes, restore cannot race deletion, and purge appears only after a terminal delete.

## Sub-tasks
1. [done] State render parity journey (delegate: codex) — done when: for a representative matrix of sync states, the label/description after an SSE update equals the server-rendered HTML.
2. [done] Forced outage/retry/recovery journey — done when: mock-target failure toggling shows retry state, manual retry works, recovery lands, and the audit trail shows one operation correlation.
3. [done] Delete/restore/purge contract journey — done when: delete during CHPW reaches terminal, restore is unavailable mid-delete, purge appears only after terminal delete.

## Sub-tasks (amended 2026-07-23, user-approved)
4. [done] Product fix: restore the orphaned SSE per-target live-update path — done when: user_detail.html carries the data-sync-cell markers the base.html renderer targets, the parity test asserts real product DOM without test-time marker injection, and gates pass.

## Log
- (append-only, one line per event)
- PRODUCT FINDING (via the new parity journey): commit 639c37d (2026-07-16 inventory redesign) removed the only data-sync-cell emitter; base.html's SSE sync-state renderer has been orphaned since — no page live-updates per-target state. 294 unit tests never caught it. User decision 2026-07-23: fix in this goal (option a) — re-attach markers on user_detail.html's canonical State badge and drop the test's marker-injection workaround.
- codex delivered three journeys (SSE parity across 6-state matrix; nexus outage via /__mock__/availability with manual retry correlated under one operation; CHPW delete → restore hidden until terminal → purge) + fixture refactor (BrowserServers, mock_server_url, autouse mock reset, tests/browser/__init__.py to avoid module-name collision)
- fix round (same session): user_detail.html data-value wrapper now carries data-sync-cell/data-user-id/data-target (matching the pre-639c37d contract); parity test asserts real product DOM, aborting /events/sync only on the fresh-render comparison page
- orchestrator verification: browser suite 4 passed twice consecutively (19.1s/18.5s); test_users + test_sync 52 passed
- phase check: full unit suite 294 passed, 4 deselected (orchestrator run)
