# phase-0005 — Responsive and accessibility journeys

- Status: done
- Depends on: phase-0002, phase-0003, phase-0004
- Goal: Cover the responsive contract (core admin and user tasks pass at 390, 768, and 1440 px) and the accessibility contract (keyboard navigation, focus order, dialog focus trapping, accessible names, status announcements, automated checks).
- Done when: representative admin and user journeys run at all three viewports without clipped navigation or undiscoverable actions; keyboard-only traversal and dialog focus trapping are asserted; an automated accessibility scan (axe-core via Playwright) passes on the core pages with any suppressions justified in-code; orchestrator independently re-runs and inspects the accessibility results.

## Sub-tasks
1. [done] Three-viewport journeys (delegate: codex) — done when: sign-in, user create, target status, and account pages pass at 390/768/1440 px.
2. [done] Keyboard and focus contracts — done when: tab order, dialog focus trap, and accessible-name assertions pass on the core flows.
3. [done] Automated accessibility scan — done when: axe-core (or equivalent, offline/vendored) passes on core pages; each suppression carries an in-code justification.
4. [done] Orchestrator verification of accessibility claims — done when: the orchestrator re-runs the scan and reviews suppressions personally.

## Log
- (append-only, one line per event)
- codex (same session) delivered 5 tests in test_responsive_a11y.py: parameterized 390/768/1440 journeys (body scrollWidth <= innerWidth with designated .table-wrap/.prose pre internal overflow; primary actions' bounding boxes within viewport; off-canvas nav proven at 390/768); keyboard-only sign-in + generated-password modal focus trap (Tab wrap both directions, Escape restores trigger focus) + ARIA-snapshot control names; six-page a11y scan
- axe-core NOT available offline (npm cache, pip, system paths all searched; no network permitted) — task's documented fallback used: six deterministic rules (single main landmark, heading order, img alt, form labels, button names, html lang), serious impact, fail-on-violation, ZERO suppressions; one scanner false positive (closed-disclosure buttons) fixed via textContent, not suppressed
- orchestrator sub-task 4: read the scanner JS line by line (rules faithful, visibility-filtered, no suppression paths); re-ran a11y test (5 passed) and full browser suite twice (19 passed both) personally
- product finding (5): 390px mobile drawer over the data-populated Dashboard produced document scrollWidth 429px (body overflow hidden while open, so not user-visible; Dashboard not a core-surface requirement) — recorded for future review
- phase check: full unit suite 294 passed, 19 deselected (orchestrator run)
