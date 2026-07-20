# phase-0003 — Tests, demo verification, DOX pass

- Status: done
- Depends on: phase-0002
- Goal: Lock the new behaviour with tests, confirm it in the running demo, and update the owning DOX docs.
- Done when: new tests cover the template endpoint and the modal markup, the full pytest suite passes, the demo page renders the modal and serves the template, and affected DOX Feature Maps are current.

## Sub-tasks
1. [done] Add tests in `tests/test_bulk_import.py` for the template CSV route (auth, content type, real target IDs, validator acceptance) — done when: tests pass.
2. [done] Add a test asserting the bulk import page renders the modal trigger and target rows — done when: test passes.
3. [done] Run the full suite — done when: `.venv/bin/pytest -q` is green.
4. [done] Verify in the demo environment via compose-helper — done when: the page shows the modal and the download returns a CSV.
5. [done] DOX closeout for `na_sso/DOX.md` — done when: Feature Map reflects the bulk template/modal work.

## Log
- 4 tests added to tests/test_bulk_import.py: template columns/target IDs, template rows accepted by the preview validator, auth required, page renders the modal trigger and target rows
- full suite green: 241 passed (.venv/bin/pytest -q)
- demo verified with the Playwright fieldkit: modal opens and lists firewall_a / nexus_demo / cloud_demo; live template.csv download carries those real IDs
- finding during demo: get_connectors() only returns targets with saved verified credentials, so a fresh demo showed an empty modal; empty-state copy corrected to point at Targets rather than the config file
- na_sso/DOX.md Feature Map: bulk entry now names the target picker, the CSV template, and templates/bulk_import.html
