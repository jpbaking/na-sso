# phase-0005 — Declutter the CSV column instructions

- Status: done
- Depends on: phase-0004
- Goal: Replace the crowded prose hint with a scannable column specification that keeps the target_ids modal trigger.
- Done when: the upload card states the format in a short line plus a per-column `.data-list`, `target_ids` remains the modal trigger, no documented kit class is replaced by custom CSS, and the suite passes.

## Sub-tasks
1. [done] Restructure the upload card: one-line format hint plus a labelled column `.data-list` — done when: no run-on requirement sentence remains.
2. [done] Keep `target_ids` as the modal trigger inside the new layout — done when: the trigger still opens the dialog.
3. [done] Verify markup and existing assertions — done when: the bulk test file passes.
4. [done] Confirm in the demo and capture a screenshot — done when: the card reads clearly and the modal still opens.

## Log
- user feedback after phase-0004: the "Required columns" hint is too crowded
- prose hint reduced to one line; column contract moved into a Column/Requirement table.table
- first attempt used .data-list, but .data-key force-uppercases its content and misrendered the literal lowercase column names; switched to a table whose cells render verbatim
- added .csv-columns td:first-child { white-space: nowrap } to app.css so display_name no longer wraps mid-token; no documented kit class covers this
- target_ids remains the .copy-btn modal trigger, now in its own table cell
- full suite 243 passed; demo re-verified, modal still opens from the table cell
