# phase-0004 — Make /dashboard the console home (routing + nav)

- Status: done
- Depends on: phase-0003
- Goal: Console roles (admin, operator, auditor) default to `/dashboard`: `default_home()` in `auth.py`, sidebar brand link, and a new Dashboard sidebar entry at the top of `_admin_nav.html`.
- Done when: Login as each console role lands on `/dashboard`, the sidebar shows Dashboard as active there, and managed-user self-service home is unchanged.

## Sub-tasks
1. [done] Update `default_home()` and `home_url` usage for console roles — done when: post-login redirect goes to /dashboard
2. [done] Add Dashboard nav entry + active state — done when: nav renders and highlights correctly
3. [done] Confirm managed-user (self-service) home unaffected — done when: managed-user login flow test passes

## Log
- (append-only, one line per event)
- default_home(): all console roles -> /dashboard; managed user stays /account (permissions.py)
- Dashboard entry added at top of _admin_nav.html, gated to console permissions; brand default home -> /dashboard in base.html
- updated stale expectations: test_permissions role homes -> /dashboard; test_users nav order + account-menu-in-sidebar-footer (stale since the pre-goal UI commit)
- tests: test_users + test_permissions + test_dashboard = 49 passed
