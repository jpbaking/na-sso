# phase-0001 — BulkImportPolicy config with a derived byte cap

- Status: done
- Depends on: none
- Goal: Make the row cap configurable and derive the upload byte cap from it, removing both hardcoded constants.
- Done when: `bulk_import_policy.max_rows` is read at request time, the byte cap equals `max_rows x 2 KiB`, and nothing captures either limit at import time.

## Sub-tasks
1. [done] Add `BulkImportPolicy` to `na_sso/config.py` and wire it into `FileConfig` — done when: the model validates bounds and defaults to 1000 rows.
2. [done] Add accessors in `na_sso/bulk.py` for the configured row and byte caps — done when: both derive from settings on each call.
3. [done] Replace the import-time `max_length=MAX_BULK_ROWS` on `BulkApiPreviewRequest` with a runtime check — done when: the API rejects an oversized batch against the configured cap, not a baked-in one.
4. [done] Replace remaining `MAX_BULK_ROWS` / `MAX_CSV_BYTES` uses, including the upload read guard — done when: no module-level limit constant remains.

## Log
- BulkImportPolicy added to config.py with max_rows (1..100000, default 1000) and row_byte_allowance (512..65536, default 2048); max_upload_bytes is a derived property
- wired into FileConfig as bulk_import_policy; settings.file falls back to defaults when no config file is set, so accessors are safe either way
- bulk.py gained max_bulk_rows() / max_csv_bytes(), read per request; both module constants removed
- BulkApiPreviewRequest no longer bakes max_length at import; the oversized-batch check now runs in preview_bulk_workflow, which the API route already maps to 422, so the API status is unchanged and only the message improves
- verified: defaults 1000 rows / 1.95 MiB; max_rows=50 derives 100 KiB; max_rows=0 rejected by bounds
