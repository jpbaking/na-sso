# GOAL — Console dashboard home for non-managed users

## Goal
- Goal ID: 20260720-admin-dashboard-home
- Outcome: Console accounts (admin, operator, auditor) land on a `/dashboard` home page showing the lean operational chart set, with extra charts in a collapsed lazy-loaded "More insights" section.
- Done when: Signing in as a console account redirects to `/dashboard`, eager tiles/charts render from live data via `lwCharts`, the "More insights" section fetches its datasets only on first expand, the sidebar links to it, and the test suite passes.
- Goal status: completed
- Goal status meaning: drafting | approved | executing | blocked-on-human | awaiting-acceptance | completed | abandoned
- Last completed phase: phase-0005

## Git
- Repository: yes
- Strategy: isolated-branch
- Starting branch: main
- Work branch: goal/20260720-admin-dashboard-home
- Baseline commit: 7db29dc8ba003f5e0a0d73b70d1b34d9f6b696d3
- Starting upstream at start: origin/main@3600e2e8a667783aba8336723de1f5d523c103f5
- Work upstream at start: none

## Phases
- [done] phase-0001 — Agree dashboard spec (charts, layout, roles)
- [done] phase-0002 — Dashboard aggregation queries (backend)
- [done] phase-0003 — Dashboard route, template, and chart wiring
- [done] phase-0004 — Make /dashboard the console home (routing + nav)
- [done] phase-0005 — Tests and demo verification

## Handoff
- Current position: completed
- Next action: none
- Last verified evidence: full suite 237 passed; demo login lands on /dashboard
- Blockers: none

## Log
- created ledger with 5 phases
- Gate A approved: lean chart set eager; charts 7,10-12 in collapsed lazy-loaded "More insights"; spec recorded in phase-0001
- Gate B/C: committed UI polish on main, branched goal/20260720-admin-dashboard-home from baseline 7db29dc
- phase-0001 done: dashboard spec agreed and recorded
- phase-0002 done: aggregation module + tests green
- phase-0003 done: /dashboard page + lazy /dashboard/insights working in demo
- phase-0004 done: /dashboard is console home with sidebar entry
- phase-0005 done: suite green, demo verified, DOX updated; goal awaiting acceptance
- user accepted 2026-07-20; goal commits squashed into one snapshot commit; merged to main
