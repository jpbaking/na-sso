# Administrator guide

This guide is for operators who run the NA-SSO console day to day: registering
targets, creating and maintaining managed users, reconciling drift, adopting
unmanaged accounts, running onboarding and offboarding jobs, reviewing access,
and reading the audit trail.

- To **install, configure, and deploy** NA-SSO, start with the
  [build & deployment guide](PRODUCTION.md).
- To hand an **end user** their sign-in, password, SSH-key, and OpenVPN
  instructions, point them at the [user guide](USER.md).
- To **evaluate** NA-SSO against mock targets, see the [demo guide](DEMO.md).

Every action below is enforced server-side by your role and recorded in the
audit trail. The sidebar shows only the sections your role can use.

## Roles and what they can do

The protected root account assigns one scoped role per operator account.

| Role | What it can do |
| --- | --- |
| **User operator** | Create and manage ordinary users, assignments, and lifecycle actions. Cannot change operator accounts or roles. |
| **Target operator** | Configure write-only target credentials, test connections, and inspect target health. |
| **Auditor** | Filter, inspect, and export audit events. No user or target mutations. |
| **Root security administrator** | Everything above, plus role assignment and protected local recovery. |

Role changes are audited and immediately end the changed account's existing
sessions. Keep at least one tested root recovery credential: the root identity
cannot be delegated, disabled, or removed. Operators manage their own password
and MFA from **My account** in the account menu — see the [user guide](USER.md).

## The core model in one minute

NA-SSO never puts itself in a target's login path. It holds the *desired* set of
local accounts and fans each change out to every assigned target through that
target's native API, tracking each target independently.

The safety mechanism you will see constantly is **CHPW** ("change password"):

- Initial, administrator-reset, and restore passwords authenticate **only to
  NA-SSO**. They are never sent to a target.
- While a user still holds a temporary password, their assigned target accounts
  stay **uncreated or disabled** and show **CHPW**.
- The moment the user signs in and chooses their own password, NA-SSO creates or
  enables the real target accounts and propagates the new password.

So a freshly created user is not "live" anywhere until they take over their own
credentials. This is by design — temporary credentials never reach a target.

## First: register and verify targets

Open **Targets**. Non-secret target definitions come from
`.config/na-sso.yaml` (see the [deployment guide](PRODUCTION.md#target-registry));
management **credentials** are entered here, encrypted in the database, and never
rendered back.

1. For each enabled target, save its management credential.
2. Probe it. **Until the current credential revision passes its probe, that
   target cannot be assigned, synchronized, or retried.**
3. Confirm health, and that any `default_groups` / `default_roles` already exist
   on the target — synchronization fails rather than provisioning an account
   without its intended memberships.

Per-connector permission requirements (OPNsense, Nexus, Nextcloud, GitLab,
Gitea, Immich, Nginx Proxy Manager, Jenkins, SSH) are in the
[deployment guide](PRODUCTION.md#target-registry). Note the honest limits:
Jenkins core has no realm-independent disable, so disable/unassign fails safely
there instead of deleting; Nginx Proxy Manager does not support exact role/group
or SSH-key management.

## Everyday user lifecycle

Work from **Users**. The inventory shows each account's per-target state and its
password-expiry date.

**Create.** Add the user, set profile fields (username, display name, email —
some targets require email), and assign only the targets they need. Retain the
one-time temporary password shown in the modal; it cannot be recovered after the
modal closes. The user is now in **CHPW** everywhere until they sign in.

**Assign / unassign.** Assigning a target either re-enables an existing remote
account or waits in `awaiting credentials` until a verified password action
supplies one. Unassignment **disables** the remote account rather than deleting
it.

**Reset password.** An administrator reset immediately returns the account to
**CHPW** and disables its assigned target accounts. The user's replacement
password re-enables or recreates them. It does not rotate anything the user
already controls elsewhere.

**Disable / enable.** Disable propagates a disabled state to assigned targets
(except where the target cannot, e.g. Jenkins).

**Delete.** Deletion is *soft* locally: NA-SSO deletes the assigned remote
accounts, retries failures, and keeps the completed local record for audit and
restoration. **Restore** requires a new temporary password and a completed CHPW
before target accounts are recreated.

**Purge.** Permanent removal of the local record requires an explicit purge, and
only after remote deletion has completed.

**When a target fails.** Open the per-target detail, read the failure, correct
the target, and retry that one target immediately. Automatic recovery also runs
with capped exponential backoff, so transient outages heal on their own.

## Reconciliation — detect and repair drift

**Reconciliation** compares your local desired state with a **read-only**
snapshot of each target before anything changes. A preview classifies every
account as **matching**, **drifted**, **unknown**, or **unsupported**.

- Repairs require a one-use approval token. **Destructive** repairs require a
  second explicit confirmation.
- Scheduled reconciliation produces **reports only** — it never repairs
  automatically. Timing and backoff come from `reconciliation_policy`.

## Unmanaged accounts — discover and adopt

**Unmanaged accounts** enumerates target-local accounts NA-SSO does not manage,
**without mutating them**. Enumeration is bounded and excludes protected names,
prefixes, and low Unix UIDs (`unmanaged_account_policy`). For each finding you
can:

- **Adopt** — link the remote account to a managed user without mutation;
  synchronization stays gated on a verified credential handoff.
- **Ignore** — persistently hide a known-good local account.
- **Remove** — available only if the deployment enabled `allow_removal`. Only
  Root can approve it, and execution still needs a second confirmation plus a
  one-use token.

## Bulk onboarding and offboarding

For larger jobs, use **Users → bulk** (CSV) or the automation API. Every bulk
run is preview-first and idempotent:

1. **Preview** validates every row with no mutation and reports per-row problems.
2. **Execute** fans each row out to its targets under one correlated operation,
   preserving per-row failures.

Limits: at most `bulk_import_policy.max_rows` rows (default 1,000), and at most
100 accounts per interactive bulk confirmation. Generated temporary passwords
are available through a single audited download and are then erased. CSV exports
are defended against spreadsheet-formula interpretation.

## Assignment profiles

**Assignment profiles** are immutable, versioned bundles of targets and
memberships. Publish a draft only after previewing it, then preview and confirm
each application to users. Applying a profile **preserves** a user's existing
direct assignments as explicit exceptions; per-user include/exclude exceptions
stay visible and override the profile.

## Governance: ownership, temporary access, and reviews

Each user's **Lifecycle policy** records an owner, a reason, effective dates,
temporary-access windows, inactivity handling, and an end action:

- Temporary access **must** have an end date; future access stays disabled until
  its start date.
- Scheduled deletion requires explicit confirmation.
- End-date and inactivity workers **disable** or open a review as configured —
  they never silently grant or retain access.

**Access reviews** start as drafts and open only on an explicit action, snapshotting
owner/reason. A reviewer's attestation can retain, disable, or stage deletion
(deletion needs a separate confirmation). Worker timing and reminders come from
`lifecycle_automation_policy`; subscribe a notification destination to
`access_review.reminder` to be nudged.

## Audit

**Audit** records every administrative action and connector result. Filter by
UTC date, actor, subject, target, action, operation ID, and outcome. CSV and
JSON exports download one bounded page at a time and are available only to
authorized administrators; technical detail is defensively redacted, but treat
exported files as sensitive.

Retention is `audit_policy.retention_days` (set in YAML); expired events are
pruned daily while correlated lifecycle operations and per-target attempts
remain.

## Automation: service accounts and `na-ssoctl`

Root creates **Service accounts** and grants only the capabilities that piece of
automation needs. Service accounts cannot hold Root capability and cannot sign
in to the browser.

Issue a labelled, time-bounded credential, copy its one-time `nas_…` value into
the client's secret manager, and send it as `Authorization: Bearer <token>`.
NA-SSO keeps only a keyed hash, prefix, expiry, issuer, revocation state, and a
coarsened last-used time. Rotate by issuing the replacement, verifying the
client, then revoking the old credential; revoking the account revokes every
credential at once.

The installed package ships `na-ssoctl` for scripted preview/apply/status/export
against `/api/v1`. Prefer a mode-`0600` token file so the token never enters
shell history:

```sh
na-ssoctl --base-url https://na-sso.example.lan --token-file /run/secrets/na-sso.token whoami
na-ssoctl ... bulk-preview accounts.csv --idempotency-key onboarding-2026-07-15
na-ssoctl ... bulk-apply <workflow-id> --idempotency-key onboarding-2026-07-15
na-ssoctl ... operation-status <operation-id>
na-ssoctl ... reconcile-preview --target-id cloud --idempotency-key cloud-check
na-ssoctl ... audit-export --output audit.json --operation <operation-id>
```

`reconcile-apply` requires the saved approval token, and destructive repair also
requires `--confirm-destructive`. The interactive OpenAPI document is at `/docs`.
Full API rules — idempotency keys, rate limits, pagination, and what payloads
never contain — are in the [deployment guide](PRODUCTION.md#versioned-automation-api).

## Notifications

When notifications are enabled (see the [deployment guide](PRODUCTION.md#signed-webhook-notifications)),
the **Notifications** page shows delivery state for both signed webhooks and the
end-user email channel. You can requeue exhausted deliveries while the channel
stays enabled, and Root can disable a destination immediately without revealing
its secret.

## A safe daily rhythm

1. **Targets** — confirm every enabled target is probing green.
2. **Users** — create/restore accounts, retain temporary passwords, assign only
   what's needed; hand the user their sign-in instructions.
3. Watch assigned targets leave **CHPW** as users take over their passwords.
4. Read per-target failures, correct the target, and retry.
5. **Reconciliation** on a schedule for drift; **Access reviews** for standing
   access.
6. **Audit** to confirm outcomes.
