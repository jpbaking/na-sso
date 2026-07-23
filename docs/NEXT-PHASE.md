# NA-SSO roadmap: delivery record and deferred work

> **Delivery status (2026-07-16): implemented; release verification passed.**
> The original audit and recommendations remain below as the decision record.
> The traceability matrix near the end of this document records the delivered
> outcome for every confirmed issue and prioritised expansion.
>
> **Later deliveries (2026-07-18 to 2026-07-21):** five additional connectors
> behind the same contract — Gitea, GitLab, Immich, Jenkins, and Nginx Proxy
> Manager (see `docs/CONNECTORS.md`); a console dashboard home for non-managed
> users plus console navigation polish; and bulk-onboarding follow-ups (CSV
> template, target-ID picker, configurable import limits).
>
> **Later delivery (2026-07-23):** OpenVPN client-config self-service for
> OPNsense targets — admin-enabled per target, user self-download of an `.ovpn`
> with an OPNsense-issued client certificate for certificate-plus-password
> servers, and CRL-authoritative revocation on offboarding. Proven end to end
> against a real OPNsense 26.7 firewall. See `docs/CONNECTORS.md` and the
> production notes in `docs/PRODUCTION.md`.
>
> **Later delivery (2026-07-23, Connector Contract 1.1):** lifecycle-operation
> support declarations now let sync, dry-run, reconciliation, operator warning
> surfaces, and the targets API report unsupported ensure, disable, or delete
> operations before remote mutation.
>
> This document also absorbs the former `FUTURE-WORK.md`; its final deferred
> item is now recorded as delivered in the traceability table below.

## Recommendation

Make the next phase **operator confidence and lifecycle correctness**, then add
scale features. NA-SSO already has a coherent visual system and a valuable core
idea: it is a control plane for local accounts, not another authentication
dependency. The next release should make every long-running operation truthful,
recoverable, and easy to understand before broadening the connector or workflow
surface.

The recommended order is:

1. Fix state-machine and recovery defects.
2. Make every action visibly confirm success, failure, and next steps.
3. Redesign the admin information architecture for more users and targets.
4. Add reconciliation, bulk onboarding, lifecycle policy, and delegated
   administration.

Do not expand into login proxying, federation, token issuance, or application
session brokering. Those would turn NA-SSO into the SSO dependency it is intended
to avoid.

## Audit basis

This review was performed on 2026-07-15 against the clean disposable demo stack
at desktop (1440×1000) and mobile (390×844) sizes using Playwright with Chromium.
It exercised the real application and connectors against all six demo targets:

- root-admin sign-in, failed sign-in, account access, and password change entry;
- invalid and valid credentials for OPNsense, Nexus, Nextcloud, SSH/password,
  and SSH/private-key targets;
- manual and generated user passwords, validation failures, and target selection;
- user creation, editing, target assignment, disable/re-enable, reset, delete,
  restore, purge eligibility, and a delete during CHPW;
- temporary-password choice, normal password change, admin reset, password
  expiry, and **Keep current password**;
- browser-generated and server-fallback SSH key enrollment;
- forced Nexus failure, automatic retry state, and manual retry;
- audit history and responsive behavior.

The review also traced the observed behavior through templates, routes,
synchronization code, and existing tests. The current suite passes: 88 tests,
with one dependency deprecation warning. Passing tests do not cover several
state combinations found by the browser audit.

## What is already working

- The product has a clear identity and consistent visual language. Page
  hierarchy, typography, color, and primary actions generally feel deliberate.
- First-login CHPW explains why the temporary password cannot yet be propagated.
- Per-target status, independent retries, and an audit record are the right core
  primitives for partial failure.
- Target assignment is explicit, and the UI warns that unassignment disables
  rather than deletes a remote account.
- Target credentials are write-only and probed immediately.
- Both SSH key paths correctly emphasize one-time private-key handling, and the
  server fallback response is marked `no-store`.
- Password creation, normal change, reset, expiry, and history are already one
  coherent model rather than unrelated features.

## Confirmed issues

Priority labels describe recommended sequencing, not production incident
severity; the product is still pre-production.

### P0 — lifecycle and state truth

#### 1. Delete can become permanently stuck during CHPW

A user restored with a temporary password returns to CHPW. If an administrator
deletes that account before the user finishes the password decision, every
assigned target is excluded from delete synchronization because its state is
`chpw`. The account remains **deleting**, `deleted_at` is never set, and **Purge**
never appears.

Recommendation:

- Define deletion as an overriding operation that is valid from every prior
  state, including `chpw`, `awaiting_credentials`, failed, retired, and
  unassigned.
- Add a transition table and tests for every lifecycle-state × action pair.
- Make deletion completion depend on explicit terminal results per target, not
  on assumptions inherited from ensure/update behavior.

#### 2. Live updates misrepresent server state

The initial HTML can correctly render **not assigned** or **unassigned;
disabled**, but the event-stream renderer reduces most non-OK states to generic
**pending**. This was reproduced after unassigning Firewall B: the server HTML
said `unassigned; disabled`, then the live page changed it to `pending`.
Unselected targets on a newly created user also appeared pending after the first
event.

Recommendation:

- Use one shared state-to-label/view model for server rendering and live updates.
- Preserve `unassigned`, `awaiting_credentials`, `retired`, `expired_disabled`,
  `pending_disable`, deletion, and retry semantics.
- Include an accessible text explanation and timestamp, not just a colored badge.
- Add parity tests asserting that static HTML and event updates render the same
  state identically.

#### 3. Restore is available while deletion is still running

The row shows a restore password field as soon as delete is requested, even
while targets are still being removed. A restore can therefore race an active
delete. During the audit, restore was accepted while one target was still
pending, producing confusing follow-up states and connector failures.

Recommendation:

- Treat delete and restore as mutually exclusive jobs with durable operation
  IDs and terminal states.
- Show **Cancel deletion** only if cancellation can be guaranteed; otherwise
  enable **Restore** only after deletion finishes.
- Present progress such as `4 of 6 targets deleted` and name the target still
  blocking completion.

### P1 — trust, safety, and task completion

#### 4. Protected root controls are false affordances

The Users table presents the root account as a username link and shows a red
**Delete** button. Both are server-side no-ops. Conversely, the root account can
change its password at `/account`, but the admin navigation has no Account link.

Recommendation:

- Render root as a protected system account with no edit/delete controls.
- Add an admin account menu with **My account**, **Change password**, and
  **Sign out**.
- Require current-password reauthentication for root security changes.

#### 5. Form errors discard work or fail silently

- A rejected new-user password clears username, display name, email, and all
  target selections.
- An invalid restore password redirects back to Users with no visible error.
- The restore password input has no accessible label and is compressed into the
  final cell of an already wide table.
- Most successful mutations return to a list or login page without confirmation.

Recommendation:

- Preserve every non-secret field and selection on validation errors.
- Put field-specific errors beside fields, add an error summary, and focus it.
- Move Restore into a dedicated dialog/page with password policy feedback and
  confirmation.
- Add durable success/failure notices: `User created`, `Password changed—sign in
  again`, `Retry started`, and `Target credentials verified`.

#### 6. Generated-secret handoff can leave the operator without the secret

The generated-password dialog displays only a partial preview. The full value is
available only through Clipboard API. If clipboard access fails, the operator
has no reveal/select fallback, but can still close the dialog and create the
account with a password nobody retained.

Browser SSH enrollment similarly triggers a private-key download and immediately
submits the public key. It does not confirm that the download completed or that
the user stored the private key before replacing the enrolled key.

Recommendation:

- Show the complete generated password in a selectable, revealable field.
- Disable account creation until the operator explicitly confirms it was copied,
  downloaded, or replaced with a manual password.
- Split browser SSH enrollment into **Generate → Save and confirm → Enroll**.
- Show the new key fingerprint before replacement and preserve the previous key
  until confirmation succeeds.

#### 7. Target credential failures are too opaque

Invalid credentials return to the collapsed Targets list. The visible result is
only **AUTH FAILED**; the actionable HTTP detail exists in a hover `title`.
There is no visible last-probed time, credential revision, manual probe action,
or distinction between credential readiness and current target availability.

Recommendation:

- Keep the failed target expanded and show an inline, sanitized error with likely
  causes and a retry action.
- Separate **Credentials verified** from **Target currently reachable**.
- Show last checked, last successful check, credential update time, and current
  retry/backoff state.
- Expose the existing probe route as **Test connection**.

#### 8. Password-expiry acknowledgement needs an explicit policy

The expiry flow is understandable and **Keep current password** works, but
keeping it resets the password-age clock to a fresh 90 days. That may be desired
as a recurring acknowledgement, but the interface describes the password as
expired rather than explaining a 90-day risk extension.

Recommendation:

- Decide whether keep means a full renewal, a shorter grace period, or a one-time
  exception.
- State the resulting date before confirmation and record the policy decision in
  the audit detail.
- Allow operators to disable keep, limit its frequency, or require approval.

### P2 — usability at realistic scale

#### 9. The Users matrix does not scale with targets

Six targets already make the table wider than the desktop content area and
1,282 px wider than its mobile container. Important actions sit beyond the
initial viewport. Root and user lifecycle concerns are mixed with a target per
column, and there is no search, sort, filter, pagination, or bulk action.

Recommendation:

- Make the primary table about people: **User**, **lifecycle**, **target
  coverage**, **issues**, **password**, and **actions**.
- Summarize targets as `5/6 healthy` or `1 needs attention`; open a details drawer
  or user page for the target matrix.
- Add search, lifecycle/target/error filters, sorting, pagination, row selection,
  and bulk disable/retry/assignment.
- On mobile, use user cards or a compact list rather than a clipped matrix.

#### 10. Managed users cannot see their actual access

The Account page shows profile, expiry, and whether some SSH key exists, but not
assigned targets, propagation state, failure/retry status, access mode, or key
fingerprint. A user cannot tell where the account is usable.

Recommendation:

- Add **My access** with one row per assigned target and plain-language states.
- Explain CHPW, waiting for credentials, disabled, and retrying in user terms.
- Add SSH key fingerprint, algorithm, enrollment date, replace, revoke, and
  download-safety guidance.
- Provide an operator-configurable support/contact path for failures the user
  cannot fix.

#### 11. Audit is a raw event dump, not an investigation tool

The page exposes up to 500 newest rows with raw action names, microsecond
timestamps, target IDs, and full connector error strings. It has no filtering,
pagination, export, correlation, or event detail view. On mobile, the table is
472 px wider than its container and the long page is difficult to scan.

Recommendation:

- Add time, actor, user, target, action, and outcome filters.
- Use friendly summaries with a detail drawer for raw technical data.
- Group all target attempts under one operation/correlation ID.
- Add paginated CSV/JSON export and configurable retention.
- Display timezone explicitly and offer localized timestamps.

#### 12. Admin roles exist in the backend but not in the UI

Create/update routes accept `user` and `admin`, but the form has no role control.
The product therefore has an undiscoverable authorization feature without a
safe administrative workflow.

Recommendation:

- Either remove the unsupported role until it is designed, or ship role
  assignment with clear capability descriptions, confirmation, audit detail,
  and protection against removing the last root/admin path.
- Prefer scoped roles such as **User operator**, **Target operator**, **Auditor**,
  and **Root security administrator** over one broad admin bit.

#### 13. Responsive navigation and accessibility need a focused pass

At 390 px the brand wraps, nav items crowd the header, **Sign out** splits across
lines, and wide tables hide content behind horizontal scrolling. The restore
password is unlabeled. Status/error meanings often depend on color, terse codes,
or hover-only text.

Recommendation:

- Add a tested mobile navigation pattern and visible horizontal-scroll cues only
  where tables remain necessary.
- Give every input a persistent label and every status an equivalent text
  description.
- Run keyboard-only and screen-reader passes plus automated accessibility checks
  in CI.
- Expand `CHPW` on first use to **Password change required**; reserve the code as
  supporting terminology.

## Recommended delivery plan

### Phase 1 — make lifecycle operations correct

- Create a typed lifecycle/synchronization state model and transition table.
- Make delete override CHPW and every nonterminal ensure state.
- Serialize or reject conflicting delete/restore/update jobs.
- Unify static and streamed status rendering.
- Add operation IDs, start/end timestamps, progress, and terminal outcomes.
- Add regression coverage for the complete transition matrix.

Exit criteria:

- No action can leave a user indefinitely stuck without a named target, reason,
  and available recovery action.
- Static and live state labels match for every state.
- Delete from active, disabled, failed, CHPW, awaiting-credentials, unassigned,
  and restored states reaches a documented terminal result.

### Phase 2 — make actions safe and self-explanatory

- Fix root affordances and add the admin account menu.
- Preserve forms and implement inline validation plus success notices.
- Redesign generated-password, SSH enrollment, delete, restore, and purge
  confirmations.
- Turn Targets into actionable operational status with test/retry and timestamps.
- Add the managed-user **My access** view.
- Complete keyboard, screen-reader, contrast, and responsive remediation.

Exit criteria:

- Every mutation visibly confirms what happened and what happens next.
- No generated credential can be committed without a recoverable handoff.
- All core admin and user tasks work at 390 px without clipped navigation or
  undiscoverable actions.

### Phase 3 — support real inventories

- Replace the target-per-column Users matrix with a summarized list and user
  detail page.
- Add search, filters, sorting, pagination, and bulk operations.
- Add useful audit filtering, correlation, export, and retention.
- Design and expose delegated admin roles.
- Add notification preferences and outbound webhooks for persistent failures,
  expiring passwords, and lifecycle completion.

Exit criteria:

- Operators can locate and act on one account among thousands without scanning
  the full inventory.
- A partial failure can be investigated from user action through every target
  attempt using one correlation ID.

### Phase 4 — expand the product without becoming SSO

Prioritize these feature additions:

1. **Reconciliation and drift detection** — scheduled comparison of desired
   versus actual target state, with dry-run, report, and approved repair.
2. **Bulk onboarding and offboarding** — CSV/API import, validation preview,
   assignment profiles, idempotency keys, and downloadable results.
3. **Lifecycle policy** — account owner, reason, start/end dates, temporary
   access, inactivity review, and scheduled disable/delete.
4. **Assignment profiles** — reusable bundles of targets, groups, and roles with
   per-user exceptions shown clearly.
5. **Access reviews** — attest who should retain which local accounts and record
   reviewer decisions.
6. **Delegated administration** — scoped roles, target ownership, separation of
   duties, approval for destructive/broad actions, and mandatory MFA for admins.
7. **SSH key lifecycle** — multiple named keys, fingerprints, expiry, rotation,
   revoke, last-used metadata where targets support it, and emergency removal.
8. **Automation surface** — documented API/CLI, service accounts, webhooks, and
   connector health endpoints using the same operation model as the UI.
9. **Unmanaged-account discovery** — report local target accounts outside
   NA-SSO, with explicit adopt, ignore, or remove workflows; never auto-delete
   discovered accounts.
10. **Connector SDK and capability contract** — versioned connector interface,
    conformance tests, capability discovery, and a safe dry-run mode.

Consider self-service access requests only after delegated administration and
approval exist. End-user SMTP email delivery was delivered on 2026-07-24, so a
future request/approval workflow can tell requesters and approvers what happened
without requiring them to poll the console. SMS remains an optional deferred
channel. Keep final provisioning authority with operators and continue to leave
authentication on each target.

## Verification backlog

Add Playwright coverage for these end-to-end contracts:

- root never exposes nonfunctional edit/delete controls and can reach My account;
- failed create/update/restore preserves safe fields and shows focused errors;
- every server state renders identically before and after an event update;
- invalid target credentials remain expanded with an inline recovery path;
- forced outage shows retry timing, manual retry, recovery, and one correlated
  operation;
- delete during CHPW completes, restore cannot race deletion, and purge appears
  only after a terminal delete;
- generated password and SSH key cannot be committed before handoff confirmation;
- temporary, normal, reset, expired-change, and expired-keep password journeys
  state their outcome before redirecting;
- user Account shows assigned targets and accurate propagation state;
- admin and user tasks pass at 390, 768, and 1440 px;
- keyboard navigation, focus order, dialog focus trapping, accessible names,
  status announcements, and contrast pass automated and manual checks.

Keep connector unit tests, but add model-based state-transition tests. The
browser audit found defects that isolated happy-path connector tests could not
detect.

### Verification backlog delivery

Delivered 2026-07-23 as an in-tree, headless Playwright suite. The historical
contract list above remains the acceptance statement; its browser evidence is:

| Contract (in list order) | Browser evidence |
| --- | --- |
| 1. Protected root affordances and My account access | `tests/browser/test_safety.py` |
| 2. Preserved form values and focused errors | `tests/browser/test_safety.py` |
| 3. Server-rendered and SSE-updated state parity | `tests/browser/test_lifecycle.py` |
| 4. Invalid target-credential recovery | `tests/browser/test_safety.py` |
| 5. Forced outage, retry, recovery, and operation correlation | `tests/browser/test_lifecycle.py` |
| 6. CHPW deletion, restore gating, and terminal-only purge | `tests/browser/test_lifecycle.py` |
| 7. Generated-secret and SSH-key handoff confirmation | `tests/browser/test_safety.py` |
| 8. Temporary, normal, reset, expired-change, and expired-keep password journeys | `tests/browser/test_passwords.py` |
| 9. Managed-user account access truth | `tests/browser/test_passwords.py` |
| 10. Core tasks at 390, 768, and 1440 px | `tests/browser/test_responsive_a11y.py` |
| 11. Keyboard, focus, accessible-name, and automated accessibility checks | `tests/browser/test_responsive_a11y.py` |

`tests/browser/test_smoke.py` supplies the harness baseline by proving that the
bootstrapped root administrator can sign in and reach the admin landing page.
The parity journey also forced a product fix: markers `data-sync-cell`,
`data-user-id`, and `data-target` were reattached to the state wrapper in
`na_sso/templates/user_detail.html`, restoring the production SSE update path
lost in regression commit `639c37d`.

CI was added 2026-07-23 in `.github/workflows/ci.yml`, with separate unit and
headless Chromium browser jobs for pushes and pull requests to `main`.

Resolved product review record (2026-07-23):

- P1.5 field-specific server errors now render beside the user-form and restore
  inputs with `aria-invalid`/`aria-describedby`, while retaining the focused
  summary (`na_sso/templates/user_form.html`,
  `na_sso/templates/user_action.html`, `tests/browser/test_safety.py`,
  `tests/test_users.py`).
- Administrator edits that reset a password now state the temporary-password
  and next-sign-in CHPW handoff; edits without a reset retain the generic notice
  (`na_sso/users.py`, `tests/browser/test_passwords.py`,
  `tests/test_users.py`).
- Legacy environment-connector My access headings now fall back to each
  connector's `display_name` instead of its raw target ID (`na_sso/auth.py`,
  `tests/browser/test_passwords.py`).
- The 390 px Dashboard overflow came from the `charts.js` `.sr-only` fallback
  table's intrinsic width and was present with the drawer closed; the drawer
  merely masked it through `body.sidebar-open { overflow: hidden; }`, so the
  chart table is now constrained in `na_sso/static/app.css` and the seeded
  Dashboard is enforced with the drawer closed and open in
  `tests/browser/test_responsive_a11y.py`.

## Delivery traceability

All confirmed P0–P2 findings have shipped. The implementation preserves the
product boundary stated in this document: NA-SSO remains an operator-controlled
local-account control plane and does not proxy login, issue application tokens,
federate identities, or broker target sessions.

### Confirmed issues

| Finding | Delivered outcome | Primary evidence |
| --- | --- | --- |
| P0.1 Delete stuck during CHPW | Typed lifecycle transitions make delete override every prior state and finish only from explicit per-target terminal results. | `na_sso/lifecycle.py`, `na_sso/operations.py`, `tests/test_lifecycle.py`, `tests/test_sync.py` |
| P0.2 Live state misrepresentation | Static HTML and SSE consume the same canonical presentation payload, including unassigned, credential-waiting, expiry, deletion, retry, timestamps, and accessible descriptions. | `na_sso/lifecycle.py`, `na_sso/status.py`, `na_sso/templates/base.html`, `tests/test_sync.py` |
| P0.3 Restore races deletion | Durable operation IDs serialize mutations; restore is unavailable until delete is terminal, while progress and blocking targets remain visible. | `na_sso/operations.py`, `na_sso/users.py`, `na_sso/templates/users.html`, `tests/test_lifecycle.py`, `tests/test_users.py` |
| P1.4 False root controls | Root renders as protected `SUPERADMIN` without target/edit/delete controls; the account-only menu opens a My Account security hub for password, MFA, and current-password reauthentication paths. | `na_sso/templates/users.html`, `na_sso/templates/base.html`, `na_sso/templates/account.html`, `tests/test_security.py` |
| P1.5 Lost or silent form outcomes | Safe fields/selections survive validation; focused summaries, inline errors, dedicated action pages, and signed one-time success/failure notices explain every mutation. | `na_sso/feedback.py`, `na_sso/templates/user_form.html`, `na_sso/templates/user_action.html`, `tests/test_users.py` |
| P1.6 Unsafe generated-secret handoff | Complete selectable/revealable secrets require explicit saved confirmation; browser SSH enrollment is Generate → inspect/save/confirm → Enrol and rotation is add-before-revoke. | `na_sso/templates/users.html`, `na_sso/templates/account.html`, `na_sso/ssh_keys.py`, `tests/test_security.py`, `tests/test_ssh_keys.py` |
| P1.7 Opaque target failures | Target rows retain inline sanitized recovery detail and separately expose credential proof, reachability, revision/check history, retry state, and manual Test connection. | `na_sso/target_credentials.py`, `na_sso/templates/status.html`, `tests/test_target_credentials.py` |
| P1.8 Ambiguous expiry acknowledgement | Bounded policy supports disabled, full-renewal, or shorter one-time grace modes; UI previews the resulting date and audit retains the decision without rewriting password-age evidence. | `na_sso/config.py`, `na_sso/auth.py`, `tests/test_security.py`, `.config/na-sso.yaml.example` |
| P2.9 Users matrix scale | People-first server-paginated inventory adds stable search/filter/sort, summaries, mobile cards, detail pages, selection, and previewed bulk assignment/disable/retry. | `na_sso/inventory.py`, `na_sso/templates/users.html`, `tests/test_inventory.py` |
| P2.10 Missing My access | Managed users see assigned targets, plain propagation/retry/mode guidance, configurable support, and complete named SSH-key lifecycle metadata/actions. | `na_sso/auth.py`, `na_sso/templates/account.html`, `tests/test_security.py`, `tests/test_ssh_keys.py` |
| P2.11 Raw audit dump | Bounded investigation adds actor/subject/target/action/outcome/time/operation filters, friendly summaries, technical detail, correlation drill-down, retention, and CSV/JSON export with explicit timezone. | `na_sso/audit_query.py`, `na_sso/templates/audit.html`, `tests/test_audit.py` |
| P2.12 Hidden broad roles | Central capabilities expose User operator, Target operator, Auditor, and protected Root roles with root-only assignment, descriptions, audit, and last-recovery-path protection. Target ownership is enforced at the target-operator capability boundary; record-level target grants are intentionally not invented for the current single-control-plane model. | `na_sso/permissions.py`, `na_sso/users.py`, `tests/test_permissions.py` |
| P2.13 Responsive/accessibility gaps | A distinctly iconed, capability-aware collapsible desktop and off-canvas mobile sidebar keeps destinations usable in every state; workflow ordering, the account-only header menu, cards, bounded table scrolling, persistent labels, text-equivalent states, focused errors, responsive layouts, and expanded password-change wording are covered at 390/768/1440 px. | `na_sso/static/app.css`, `na_sso/templates/base.html`, `na_sso/templates/_admin_nav.html`, `tests/browser/test_responsive_a11y.py`, `tests/test_users.py`, `tests/test_security.py` |

### Prioritised expansion

| Capability | Delivered outcome | Primary evidence |
| --- | --- | --- |
| Reconciliation and drift detection | Scheduled and manual bounded read-only desired/actual comparison, saved dry-run, per-field evidence, explicit one-use approval, separate destructive confirmation, and correlated repair. | `na_sso/reconciliation.py`, `na_sso/reconcile.py`, `tests/test_reconciliation.py` |
| Bulk onboarding and offboarding | Bounded CSV/JSON import plus UI/API selection workflows provide no-change validation, idempotent execution, partial outcomes, assignment mapping, and one-time temporary-credential download. | `na_sso/bulk.py`, `tests/test_bulk_import.py`, `tests/test_inventory.py` |
| Lifecycle policy | Owner, reason, start/end, temporary access, inactivity timing, scheduled transitions, and review triggers reuse normal correlated lifecycle operations. | `na_sso/governance.py`, `tests/test_governance.py` |
| Assignment profiles | Immutable published target/group/role bundles, previewed application, and visible per-user assignment/membership exceptions feed synchronization and reconciliation. | `na_sso/assignments.py`, `tests/test_assignments.py` |
| Access reviews | Saved campaign preview/opening, reminders, owner/reason snapshots, idempotent attestations, and correlated retain/disable/delete decisions. | `na_sso/governance.py`, `tests/test_governance.py` |
| Delegated administration | Server-enforced separation among user, target, audit, and root-security duties; configurable mandatory administrator WebAuthn/TOTP with one-use recovery; previews/approvals protect broad and destructive workflows. | `na_sso/permissions.py`, `na_sso/mfa.py`, `tests/test_permissions.py`, `tests/test_mfa.py` |
| SSH key lifecycle | Multiple named keys expose fingerprint, algorithm, enrollment, expiry, history, truthful last-use support, zero-downtime replacement, individual/emergency/expiry revocation, and exact target key sets. | `na_sso/ssh_keys.py`, `tests/test_ssh_keys.py` |
| Automation surface | Versioned redacted API, thin CLI, least-privilege service accounts, expiring/rotatable one-time Bearer credentials, request IDs, rate limits, idempotency, operation status, target health, audit export, and signed webhooks. | `na_sso/api.py`, `na_sso/cli.py`, `na_sso/service_accounts.py`, `tests/test_api.py`, `tests/test_cli.py` |
| Unmanaged-account discovery | Built-in connectors enumerate safely without mutation; exclusions, persistent ignore, no-mutation adoption, and disabled-by-default two-step Root removal are explicit. | `na_sso/unmanaged.py`, `tests/test_unmanaged.py` |
| Connector contract | Contract 1.1 publishes machine-readable capabilities, typed retry-aware errors, bounded timeouts, inspection-only dry-run, discovery, per-operation support declarations, conformance tests, and third-party extension guidance. | `na_sso/connectors/base.py`, `docs/CONNECTORS.md`, `tests/test_connector_contract.py` |
| Capability-declared unsupported operations (deferred item) | Default-supported ensure, disable, and delete declarations are checked before mutation; unsupported work becomes a terminal outcome without a target attempt, while dry-run, reconciliation, operator decision surfaces, and the targets API expose the limitation in advance. | `na_sso/connectors/base.py`, `na_sso/sync.py`, `na_sso/reconciliation.py`, `na_sso/users.py`, `tests/test_connector_contract.py`, `tests/test_sync.py` |
| Notifications and webhooks (Phase 3 plan item) | Policy-driven outbound notifications with per-endpoint event allowlists (`sync.persistent_failure`, `password.expired`, `lifecycle.completed`, `approval.completed`, `access_review.reminder`), HMAC-signed deliveries with retry/disable states, and no connector detail or secrets in payloads. | `na_sso/notifications.py`, `tests/test_notifications.py`, `.config/na-sso.yaml.example` |
| SMTP end-user email | A second durable delivery channel resolves recipients from managed-user email addresses, renders allowlisted lifecycle/password/approval messages, shares webhook retry/backoff/audit state, exposes safe admin visibility and retry, and is demo-verified through the self-contained Mailpit inbox. SMS is deferred. | `na_sso/email_delivery.py`, `na_sso/config.py` (`EmailChannel`), `na_sso/notifications.py`, `tests/test_email_delivery.py`, `tests/test_notifications.py`, `tests/test_migrations.py`, `docker-compose-demo.yaml`, `docs/DEMO.md` |

Self-service access requests remain outside this delivered phase. This follows
the original sequencing recommendation rather than deferring a committed item:
operators retain final provisioning authority, and no end-user request workflow
was included in the prioritised capability list. Decision 2026-07-23, updated
2026-07-24: direct end-user reachability remains a precondition for any future
self-service request workflow. SMTP email delivery satisfies that precondition
and was demo-verified through the self-contained Mailpit inbox. SMS is explicitly
deferred because no local-mock equivalent exists and a live demonstration would
require an external provider, conflicting with the demo's no-external-dependency
constraint. Self-service access requests are therefore no longer blocked on
end-user reachability.

## Deferred future work

The sole deferred item, capability-declared unsupported operations, was
delivered on 2026-07-23 as Connector Contract 1.1. Its outcome and primary
evidence are recorded in **Prioritised expansion** above. SMS is separately
deferred as an optional additional notification channel; SMTP email now provides
the required end-user reachability without an external demo dependency.
