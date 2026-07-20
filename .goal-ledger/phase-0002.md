# phase-0002 — Bulk import page: target_ids modal and template download

- Status: done
- Depends on: phase-0001
- Goal: Make `target_ids` in the column hint a clickable trigger opening a target table modal, and surface the example CSV download.
- Done when: the page passes a target list into the template, the `target_ids` token is a modal trigger using the design system's `data-modal-open` dialog pattern, the modal shows a table of target_id/display name/type, and a download control links to the template CSV.

## Sub-tasks
1. [done] Extend the `/users/bulk/import` handler context with the enabled target list — done when: template receives id, display name and type per target.
2. [done] Replace the plain `<code>target_ids</code>` hint token with a modal trigger — done when: it is a real button honouring `data-modal-open` and keyboard focus.
3. [done] Add the `<dialog class="modal">` with the targets table and an empty state — done when: markup follows `na_sso/static/design/components.css` modal conventions.
4. [done] Add the "Download example CSV" action next to the upload field — done when: it points at the phase-0001 route.

## Log
- bulk_import_page context now carries available_targets() as `targets`
- hint token target_ids is a `.copy-btn` button with data-modal-open="#targets-modal" and aria-haspopup=dialog (documented kit classes; no new CSS)
- dialog.modal added with a .table-wrap table (Target ID / Name / Type), an alert-info empty state, and a modal-foot download link; /design/components.js loaded at page end
- upload form actions use .form-actions with a secondary "Download example CSV" link
- verified by rendering the page: trigger, dialog, target row (cloud / Cloud access / nextcloud), template link and components.js all present
