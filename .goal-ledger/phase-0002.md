# phase-0002 — Dashboard aggregation queries (backend)

- Status: done
- Depends on: phase-0001
- Goal: Implement read-only aggregation functions that compute every agreed chart's dataset from the models (managed users, sync states, operations, audit events, reconciliation/unmanaged findings, expiries, webhooks).
- Done when: A `dashboard` module returns the eager dataset dict and, separately, the lazy "insights" dataset dict for the agreed spec, exercised by unit tests against a seeded session.

## Sub-tasks
1. [done] Add dashboard aggregation module: eager datasets (tiles 1-4, charts A-D) — done when: functions return correct shapes for seeded fixtures
2. [done] Add insights aggregations (charts E-H) as a separate lazily-computed group — done when: functions return correct shapes for seeded fixtures
3. [done] Unit tests covering each aggregation (empty DB and seeded DB) — done when: new tests pass with `.venv/bin/pytest -q`

## Log
- (append-only, one line per event)
- added na_sso/dashboard.py: eager_datasets (tiles users/targets/findings/ops24h, sync health, ops timeline 14d, expiry horizon, recon findings donut) and insights_datasets (lifecycle donut, audit 14d, webhooks 30d, access review progress)
- spec deviation: target credentials have no expiry column, so expiry horizon covers passwords / SSH keys / service credentials only
- tests/test_dashboard.py: 4 tests (empty + seeded) pass via .venv/bin/pytest -q tests/test_dashboard.py
