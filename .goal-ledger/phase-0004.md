# phase-0004 — Dashboard drawer overflow fix at 390px

- Status: done
- Depends on: phase-0002, phase-0003
- Goal: Eliminate the document scrollWidth overflow produced by the open mobile drawer over the data-populated dashboard at 390px, and add the dashboard to the responsive journey's asserted surfaces so the fix is enforced.
- Done when: opening the drawer on a data-populated dashboard at 390px leaves document scrollWidth <= innerWidth; tests/browser/test_responsive_a11y.py asserts the dashboard surface (drawer open and closed) at all three viewports; unit + browser suites pass.

## Sub-tasks
1. [done] CSS/layout fix (delegate: codex) — done when: the drawer no longer widens the document over the dashboard at 390px.
2. [done] Extend the responsive journey — done when: dashboard (with seeded data, drawer open + closed) is asserted at 390/768/1440.
3. [done] Regression: existing viewport assertions unchanged — done when: prior surfaces still pass.

## Log
- (append-only, one line per event)
- ROOT CAUSE CORRECTED (vs the recorded finding): the drawer was never causal — charts.js's accessible fallback `<table class=sr-only>` (Expiry horizon, 3 series) overrode the 1px sr-only box via native intrinsic column widths, measuring 385px wide; the 39px overflow existed with the drawer CLOSED and body overflow:hidden while open merely masked it. Failing measurement (scrollWidth 429 vs innerWidth 390) reproduced before the fix
- fix scoped in app.css only: .chart table.sr-only { max-width:1px; table-layout:fixed } + cell overflow hidden — table stays in the accessibility tree; design-system bundle untouched; no drawer/sidebar/body rule changed
- responsive journey now seeds a deterministic populated dashboard (incl. 80-day-old credential to force the Expiry horizon chart) and asserts document width at 390/768/1440 with drawer closed, and open at 390/768, with bounded diagnostic geometry on failure
- phase-0005 must record the corrected root cause in the roadmap (the original observation blamed the drawer)
- orchestrator verification: browser 19 passed twice; full unit 294 passed, 19 deselected
