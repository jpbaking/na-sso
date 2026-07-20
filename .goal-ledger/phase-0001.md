# phase-0001 — Template CSV endpoint built from configured targets

- Status: done
- Depends on: none
- Goal: Serve a downloadable example CSV whose target_ids column uses real configured target IDs.
- Done when: `GET /users/bulk/import/template.csv` returns a CSV attachment with the documented columns and real target IDs, and its rows parse through the existing bulk CSV parser without unknown-target validation errors.

## Sub-tasks
1. [done] Read `na_sso/bulk.py` parsing/validation rules and `get_connectors()` shape — done when: exact column names, separator, and action vocabulary are confirmed.
2. [done] Add a template-row builder drawing target IDs from enabled connectors, with a safe fallback when none are configured — done when: helper returns deterministic example rows.
3. [done] Add the `template.csv` route reusing `_csv_response` and the page's auth guard — done when: route is registered so the `{workflow_id}` catch-all does not shadow it.
4. [done] Exercise the endpoint — done when: a request returns 200 `text/csv` with a Content-Disposition filename.

## Log
- columns confirmed: username, action, display_name, email, target_ids; separator `|`; actions onboard/offboard
- added available_targets() and bulk_template_rows() in na_sso/bulk.py; falls back to `example-target` when no connectors are configured
- route GET /users/bulk/import/template.csv registered before the {workflow_id} catch-all so it is not shadowed; reuses _csv_response and the MANAGE_USERS guard
- verified: 200 text/csv, filename na-sso-bulk-import-template.csv, target_ids column carries the real configured ID
