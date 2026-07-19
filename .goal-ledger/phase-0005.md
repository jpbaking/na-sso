# phase-0005 — Tests and demo verification

- Status: done
- Depends on: phase-0004
- Goal: Full-suite green plus visual verification of the dashboard in the running demo; update DOX Feature Map for the new feature.
- Done when: `.venv/bin/pytest -q` passes, a headless-browser screenshot of `/dashboard` in the demo shows the agreed charts, and `na_sso/DOX.md` lists the dashboard feature.

## Sub-tasks
1. [done] Route/permission tests for /dashboard incl. role redirects — done when: tests pass
2. [done] Rebuild demo and capture screenshot for user review — done when: screenshot delivered
3. [done] Update `na_sso/DOX.md` Feature Map — done when: dashboard entry present

## Log
- (append-only, one line per event)
- fixed bug caught by full suite: _target_ids() serialized Connector objects; now uses connector.target_id
- updated stale redirect expectations in test_mfa.py / test_security.py (console home is /dashboard)
- full suite: 237 passed (.venv/bin/pytest -q)
- demo rebuilt; headless login as admin lands on /dashboard; screenshot dashboard-final.png delivered
- na_sso/DOX.md Feature Map: added Console dashboard entry
- acceptance feedback round 1: (a) charts too tall on wide viewports -> capped .chart svg to max-height 280px and render after layout settles; (b) redesigned More insights as a centred hairline divider disclosure (.insights); (c) sidebar footer missing on /dashboard -> route now passes admin username to the template
- re-verified in demo at 1900px: footer present, chart height 260px, divider renders; tests/test_dashboard.py 6 passed
