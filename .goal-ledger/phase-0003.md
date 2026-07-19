# phase-0003 — Dashboard route, template, and chart wiring

- Status: done
- Depends on: phase-0002
- Goal: Add GET `/dashboard` (permission-gated like other console pages) rendering `dashboard.html` with stat tiles and charts drawn by the design-kit `lwCharts` (bar/line/donut/sparkline) from the aggregation data.
- Done when: `/dashboard` renders for an authenticated console account with all agreed charts populated from live data, following the design kit (one amber element, tokens, chart palette).

## Sub-tasks
1. [done] Route + permission gate + template skeleton with section head and tiles — done when: page renders 200 with placeholder data
2. [done] Wire lwCharts for eager charts (tiles + A-D) with server-embedded JSON — done when: charts render with real aggregates in the demo
3. [done] Collapsed "More insights" section: GET /dashboard/insights JSON endpoint, fetch on first expand, render E-H — done when: datasets are only fetched after expand and charts render
4. [done] Empty-state handling for zero-data charts (both sections) — done when: fresh install renders without JS errors

## Log
- (append-only, one line per event)
- routes live in na_sso/dashboard.py (router registered in main.py); console guard admits MANAGE_USERS/MANAGE_TARGETS/VIEW_AUDIT via permission_guard (keeps MFA step-up)
- templates/dashboard.html: grid-stats tiles + grid-2 charts, embedded JSON, /design/charts.js
- deviation: charts.js has no stacked-bar mode, sync health renders grouped bars
- verified in demo via headless chromium: 0 insights fetches before expand, 1 after; empty states render; no console errors
- route tests added; tests/test_dashboard.py 6 passed
