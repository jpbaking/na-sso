# phase-0001 — Agree dashboard spec (charts, layout, roles)

- Status: done
- Depends on: none
- Goal: Turn the proposed chart list into an agreed spec: which charts, which data windows, tile order, and which roles see the dashboard.
- Done when: The chosen chart set with data sources and layout is recorded in this phase file's log and the user has confirmed it.

## Sub-tasks
1. [done] Present the candidate chart list with data sources — done when: user has picked/adjusted the set
2. [done] Record the agreed spec (charts, windows, order, roles) in this file — done when: spec section appended below

## Agreed spec (user-confirmed 2026-07-20)

Roles: admin, operator, auditor (console accounts). Managed-user self-service home unchanged.

Eager section (rendered on page load):
- Tile 1: Managed users — total + active/disabled split, 30-day sparkline
- Tile 2: Targets healthy — reachable/total from probe state
- Tile 3: Open findings — reconciliation drift + unmanaged findings pending disposition
- Tile 4: Operations (24h) — completed vs failed
- Chart A: Per-target sync health — stacked bar (in-sync / pending / error per target)
- Chart B: Lifecycle operations over time — line, 14 days (succeeded / failed / retried)
- Chart C: Expiry horizon — bar (passwords, SSH keys, service-account creds, target creds × ≤7/≤30/≤60 days)
- Chart D: Reconciliation findings by class — donut (last run)

"More insights" section at the bottom — collapsed by default; datasets are lazy-loaded
(fetched from a JSON endpoint on first expand, then charts rendered):
- Chart E: User lifecycle distribution — donut (active / disabled / pending-delete / protected)
- Chart F: Audit activity — line, 14 days (events/day)
- Chart G: Webhook delivery success rate — recent deliveries
- Chart H: Access review progress — completed vs pending attestations for open cycle

## Log
- user selected lean set; charts 7,10,11,12 moved to a collapsed, lazy-loaded "More insights" section
- phase check passed: spec section present above, user confirmed lean set + lazy insights
