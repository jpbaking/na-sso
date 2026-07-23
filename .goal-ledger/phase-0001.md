# phase-0001 — CI workflow: two-job GitHub Actions pipeline, locally validated

- Status: done
- Depends on: none
- Goal: Add a GitHub Actions workflow with separate unit and browser jobs whose commands exactly mirror docs/DEVELOPER.md, validated locally (the live run is confirmed post-merge in phase-0005).
- Done when: `.github/workflows/*.yml` defines a unit job (`pip install -e '.[dev]'`, `pytest -q`) and a browser job (adds `playwright install chromium`, runs `pytest -m browser tests/browser/`) on push/PR to main with Python 3.12+; the workflow passes local validation (actionlint if available, else yamllint/python-yaml parse + orchestrator review); commands are character-identical to the documented ones.

## Sub-tasks
1. [done] Author the two-job workflow (delegate: codex) — done when: the YAML exists with unit + browser jobs, pip caching, and push/PR triggers on main.
2. [done] Local validation — done when: actionlint or an equivalent local check passes and the orchestrator has reviewed the YAML line by line.
3. [done] Command parity check — done when: workflow commands match docs/DEVELOPER.md exactly (no drift between docs and CI).

## Log
- (append-only, one line per event)
- codex authored .github/workflows/ci.yml: unit (15m) + browser (20m) jobs, ubuntu-latest, Python 3.12, pip cache keyed on pyproject.toml, contents:read permissions, concurrency cancellation, checkout@v7 + setup-python@v6; playwright install-deps chromium split from playwright install chromium to keep documented commands literally unchanged
- orchestrator validation: PyYAML parse + structural assertions run personally; run-command parity with docs/DEVELOPER.md verified by grep (identical)
- noted live-run risk: pinned action majors are proven only by the post-merge run (phase-0005 sub-task 4)
