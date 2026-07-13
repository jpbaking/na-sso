# Core working rules

(The small-model counterpart of this file is a full reasoning scaffold for weak
models; you don't need that. These are the workspace-specific disciplines.)

- This workspace may hold multiple unrelated projects. Establish which project
  a task belongs to before acting, and keep every edit, command, and verifier
  inside it — run builds/tests from that project's own folder with its own
  config, never from the workspace root or a sibling's.
- Verify names before use: APIs, functions, flags, paths. If something can't
  be verified, label it `ASSUMPTION:` instead of asserting it.
- Iterate only against external evidence (tests, compiler, linter — found via
  the project's package.json/Makefile/pyproject/CI config). Without a
  verifier, one careful self-review pass, then stop — don't loop on opinion.
- Change only what the task requires; prefer the diff that touches the fewest
  files and lines.
- Report honestly: failing tests, skipped steps, and unresolved issues are
  stated plainly, never smoothed over. Finding a bug in your own work is
  success.
