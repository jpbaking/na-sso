# docs

## Purpose

Documentation for evaluating, developing, deploying, securing, and maintaining NA-SSO.

## Ownership

Owns detailed operator guidance under `docs/`. Product positioning remains in the root `README.md`; executable configuration and behavior remain owned by the root Compose files and `na_sso/` source.

## Local Contracts

- Keep demo instructions isolated from production instructions and label all public demo credentials as unsafe for real use.
- Keep implementation architecture and engineering workflows in `DEVELOPER.md`; keep deployment and operator procedures in `PRODUCTION.md`.
- Commands must use `compose-helper.sh` so project names, Compose files, and environment files remain consistent.
- Production guidance must distinguish a demo-free Compose model from a fully hardened deployment and must not imply that placeholder configuration is safe.
- Keep ports, filenames, command behavior, credentials, target prerequisites, and backup requirements synchronized with implementation.

## Verification

- Validate both Compose models after command or configuration documentation changes.
- Confirm every relative link and referenced repository path exists.

## Feature Map

- **Demo guide** — Evaluates the complete application with isolated mock API and SSH targets. Start: `DEMO.md`.
- **Developer guide** — Explains code ownership, internal architecture, synchronization state, and engineering verification. Start: `DEVELOPER.md`.
- **Connector extension contract** — Defines the versioned adapter interface, machine-readable capabilities, read-only discovery/dry-run rules, error taxonomy, timeouts, security boundaries, and conformance workflow. Start: `CONNECTORS.md`.
- **Next-phase delivery record** — Preserves the admin/user UX audit and feature roadmap, and maps every confirmed issue and prioritised expansion to its delivered implementation and evidence. Start: `NEXT-PHASE.md`.
- **Future work** — Records deliberately deferred improvements with the context and reason for deferral, starting with capability-declared unsupported connector operations. Start: `FUTURE-WORK.md`.
- **Production guide** — Configures, secures, validates, operates, and backs up the normal runtime. Start: `PRODUCTION.md`.

## Child DOX Index

- (none)
