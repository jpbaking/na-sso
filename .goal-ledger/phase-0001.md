# phase-0001 — Playwright harness: dependency, live-server fixture, browser marker, smoke journey

- Status: done
- Depends on: none
- Goal: Add pytest-playwright with headless Chromium, a live uvicorn app-server fixture backed by the in-process mock targets, and a `browser` pytest marker that keeps the default unit run unchanged; prove it with one smoke journey.
- Done when: `pytest -m browser tests/browser/` runs a root sign-in smoke journey green against a live app on an ephemeral port with mock targets; `pytest -q` (default) collects zero browser tests and stays as fast as before; chromium installs reproducibly (documented command); the dependency is declared in pyproject.

## Sub-tasks
1. [done] Inventory existing fixtures/mock-target boot path relevant to a live-server fixture (delegate: agy, read-only) — done when: a report shows how conftest.py boots the app/mocks today and what a uvicorn-based fixture must reuse.
2. [done] Add pytest-playwright dependency + chromium install path (delegate: codex) — done when: pyproject declares it and the install command is verified locally.
3. [done] Live-server + mock-targets fixture and `browser` marker (delegate: codex, same task) — done when: browser tests get a base URL to a live app seeded like the unit fixtures; default runs deselect them.
4. [done] Root sign-in smoke journey — done when: `pytest -m browser` passes the smoke test headlessly.

## Log
- agy inventory verified by spot-check: per-test env/DB reset (tests/conftest.py:8-20), threaded-uvicorn precedent (tests/test_mock_targets.py:36-50), no custom markers before this phase; key tension identified: per-test tmp DBs vs long-lived server — later phases must seed/reset against the session server
- codex (session 019f8c3a-78a3-7772-b36b-b155677efbb5) delivered: playwright>=1.49 + pytest-playwright>=0.6 in dev extras; addopts "-m 'not browser'" + registered marker; _UvicornThread with pre-bound sockets; session-scoped live_server_url running real app + mock targets on loopback; root sign-in smoke with role/label selectors; install commands: pip install -e '.[dev]' && playwright install chromium
- fixture uses the LEGACY env-connector path (NA_SSO_OPNSENSE_ENABLED etc.), not YAML targets + UI credentials — fine for smoke; phases 0002/0003 need the modern target path (flagged for those prompts)
- orchestrator gates: smoke 1 passed (6.2s); collect-only exactly 294/295 (1 deselected)
- phase check: full default suite 294 passed, 1 deselected (233s, orchestrator run) — default run unaffected
