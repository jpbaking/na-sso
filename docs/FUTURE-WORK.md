# Future work

Deliberately deferred improvements. Each entry records why it was deferred so a
later phase can pick it up without rediscovering the context.

## Capability-declared unsupported operations

**Context.** Some connectors cannot perform every lifecycle operation: Jenkins'
built-in local security realm has no realm-independent disable, so the Jenkins
connector fails disable explicitly. Today the sync layer learns this only *after*
attempting the operation: a connector `VALIDATION` failure persists as the
terminal `unsupported` sync state, is presented truthfully, and is never
auto-retried.

**Deferred improvement.** Declare unsupported operations up front in the
connector contract instead of discovering them at execution time:

- Extend `ConnectorContract` / `IdentityCapabilities` (`na_sso/connectors/base.py`)
  with per-operation support flags (e.g. `disable_supported`), bumping the
  contract version.
- Surface the limitation *before* the operation runs: warn in the assignment and
  unassignment/offboarding UI ("this target cannot disable accounts — delete
  instead?"), and include it in dry-run plans and reconciliation previews.
- Let unassignment planning skip the doomed disable and record an explicit
  unsupported outcome without a failed operation attempt.

**Why deferred.** It is a contract version bump with UI, docs, API-serializer,
and test surface across assignment, sync, reconciliation, and dry-run planning —
a feature in its own right. Worth doing once more connectors with partial
operation support exist; with only Jenkins affected, the execution-time
`unsupported` state carries the same truth at a fraction of the surface.
