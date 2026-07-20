# phase-0002 — Report the configured limits in the UI, errors and docs

- Status: done
- Depends on: phase-0001
- Goal: Every place that states a limit must state the configured one.
- Done when: the upload hint, the CSV and API rejection messages, and `docs/PRODUCTION.md` all reflect the configured values rather than fixed numbers.

## Sub-tasks
1. [done] Pass the configured row and byte caps into the bulk import template — done when: the hint renders both from context.
2. [done] Make rejection messages quote the configured caps — done when: an oversized CSV names the actual limit.
3. [done] Update `docs/PRODUCTION.md` to describe the setting and its derivation — done when: the stated contract matches the code.

## Log
- added upload_size_label(); the page context now carries max_rows and max_upload, and the hint renders both (thousands-separated rows)
- the oversized-upload rejection quotes the configured cap through the same label helper
- docs/PRODUCTION.md now documents bulk_import_policy, the derivation, and the sequential-execution cost of raising max_rows
- docs/DEVELOPER.md notes both caps are read per request, never captured at import
