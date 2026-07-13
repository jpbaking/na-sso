# DOX framework

DOX v2.0.0 — framework source: https://github.com/jpbaking/dox

DOX is a hierarchy of AGENTS.md files that keeps a project understandable. Each folder's AGENTS.md is the local contract for everything beneath it; together they form a tree from the repository root down to each work area. Follow DOX across every edit in this project.

## Core Contract

- Each AGENTS.md is the binding contract for its subtree.
- Any work product, source material, instruction, record, asset, or durable doc must stay understandable from the nearest AGENTS.md plus every AGENTS.md above it.
- No child doc may weaken a rule set by a parent doc.

## Hierarchy

- The root AGENTS.md holds project-wide instructions, global preferences, durable workflow rules, its own Feature Map, and the top-level Child DOX Index. The root's Feature Map and Child DOX Index follow the same rules as any child doc's.
- Each child AGENTS.md owns the instructions for its own folder and lists its own children.
- The closer a doc is to the work, the more specific and practical it is. Broad rules live in parents; concrete details live in children.
- Each parent explains what its direct children cover and what the parent keeps for itself.

## Where a doc goes: boundaries

A **boundary** is a folder that earns its own AGENTS.md. Apply this test to every folder, at the root and at every level below it, all the way down. Depth does not matter — a deeply nested folder gets a doc on exactly the same terms as a top-level one.

**Create an AGENTS.md for a folder when ANY of these is true:**

- It is a submodule, subproject, or nested repository (git submodule, SVN external, Perforce stream or mapped depot path, workspace/monorepo package, or a project you maintain inside this one).
- It is separately built, run, tested, or deployed — it has its own `package.json`, `pyproject.toml`, `go.mod`, `Cargo.toml`, `Makefile`, `Dockerfile`, or similar.
- It has its own purpose or audience that differs from its parent.
- It carries its own contracts, interfaces, or rules that differ from the parent's.

**Do NOT create one when ALL of these are true:**

- It is just a grouping folder (for example `utils/`, `helpers/`, `assets/`) with no distinct contract.
- Everything in it already follows the parent's rules.
- A doc here would only repeat the parent.

When unsure, do not create one — the nearest parent covers it. But **never skip a real submodule or subproject**; those always get a doc.

**Go as deep as the structure goes.** A boundary inside a boundary gets its own child AGENTS.md. A submodule that itself contains submodules or subprojects gets a full DOX subtree beneath it. There is no depth limit and no preference for a flat tree — the doc tree should mirror the project's real structure.

**Sub-roots.** When a boundary is a self-contained submodule or subproject, treat its AGENTS.md as a *sub-root*: write it like a root (full local contract, plus its own workflow or verification rules where they differ from the parent) and build a normal DOX tree beneath it. A sub-root still may not weaken any rule from the docs above it.

**Nested roots.** A sub-root that is also the root of its own independently versioned project is a *nested root* — a git submodule or nested git repo, an SVN external, a Perforce stream or mapped depot path, or any folder synced from another repository. The marker is the doc, not the version control system: **any folder whose AGENTS.md carries the full DOX rules is a nested root.** It plays two roles at once — root of its own project, sub-root inside this tree — so the same doc works whichever folder an engineer roots their workspace at. Follow these rules exactly:

1. **Leave its doc as it is.** A nested root keeps its full copy of the DOX rules and its root shape. Never rewrite it into Child Doc Shape and never strip its rules — that breaks the project when it is developed standalone.
2. **Read it as a local root.** When walking the doc chain, treat the nested root's AGENTS.md as the root for everything beneath it; the parent chain still applies above it.
3. **Never edit it to resolve a conflict.** If a nested root's rule conflicts with a parent rule, do not change the nested root — it is owned by another project. Report the conflict to the user and let them decide.
4. **The parent speaks in its own doc.** Anything the parent expects from the nested project goes in the parent's AGENTS.md (its Child DOX Index line and, if needed, a Local Contract) — never into the nested root's doc.
5. **Respect the repository boundary.** Any file change inside a nested root — its AGENTS.md included — belongs to that project's own version history: commit or submit it there, following that system's convention (for a git submodule, the parent repo then updates its submodule pointer). State this in your report whenever you touch files under a nested root.

## Child Doc Shape

A child AGENTS.md uses these sections, in this order. Omit a section if it would be empty — except keep the Child DOX Index and mark it `(none)` at a leaf, so the tree stays explicit.

- **Purpose** — what this folder is for.
- **Ownership** — what this doc governs and what it leaves to parent or child docs.
- **Local Contracts** — rules, interfaces, or constraints specific to this folder. A sub-root puts its build/run/test commands and its own workflow rules here.
- **Work Guidance** — current standards or user instructions for work here. Leave empty if none exist yet.
- **Verification** — how to check work here (tests, lint, build). Leave empty if no such check exists yet.
- **Feature Map** — features implemented in this subtree, each pointing to its entry file and supporting files. Omit if none yet. See the Feature Map section below for the format and rules.
- **Child DOX Index** — one line per direct child AGENTS.md, naming what it covers. Mark `(none)` at a leaf.

Example child AGENTS.md (a leaf):

```markdown
# services/auth

## Purpose
Authentication service: login, sessions, and token issuance.

## Ownership
Owns code under services/auth/. Database schema is owned by ../db.

## Local Contracts
- All endpoints return the shared Error shape from ../shared/errors.
- Never log raw tokens.

## Verification
- `npm test` in this folder must pass before any commit.

## Feature Map
- **Login** — email/password sign-in; issues a session. Start: `login.ts`. Files: `session.ts`, `password.ts`.
- **Token issuance** — signs and refreshes access tokens. Start: `tokens.ts`. Files: `keys.ts`.

## Child DOX Index
- (none)
```

Example sub-root (a standalone subproject with its own children):

```markdown
# packages/payments

Sub-root: standalone subproject with its own build and deploy.

## Purpose
Payments service: charges, refunds, and provider integrations.

## Ownership
Owns everything under packages/payments/. Inherits all root DOX rules and adds the local rules below. Does not weaken any parent rule.

## Local Contracts
- Build, test, and run from this folder: `make test`, `make run`.
- All money values use integer minor units. Never use floats.

## Verification
- `make test` must pass before any commit that touches this subtree.

## Feature Map
- **Charge a card** — authorizes and captures a payment. Start: `charge.ts`. Files: `providers/`, `ledger/post.ts`. Detail in ./providers and ./ledger.
- **Refund** — reverses a charge and updates the ledger. Start: `refund.ts`. Files: `ledger/post.ts`.

## Child DOX Index
- providers/ — adapters for each external payment provider.
- ledger/ — double-entry ledger and reconciliation.
```

## Feature Map

The Feature Map answers "what does this part of the system do, and which files do I open to work on it?" It lets an agent start a feature with minimal code traversal, and lets the whole tree be aggregated into an architecture overview.

It is separate from the Child DOX Index on purpose. The Child DOX Index maps **docs to child docs** (how to navigate the doc tree). The Feature Map maps **features to source files** (how to navigate the code). They are different axes — keep them in different sections.

**Format.** Each AGENTS.md lists the features whose code lives mostly within its own subtree. One bullet per feature:

```
- **<Feature name>** — <one line: what it does>. Start: `<entry file>`. Files: `<other files or folders>`.
```

- **Start** is the single file to open first — the entry point, or the clearest place to begin reading.
- **Files** are the other source files or folders that implement it. Name a folder, not every file inside it, when the whole folder belongs to the feature.

**Where a feature goes (locality).**

- Put a feature in the AGENTS.md closest to its code.
- If a feature's code spans several folders, put it in the lowest folder whose subtree contains all of it. Name the feature there and point into the child docs that hold each slice (`Detail in ./child`); each child may list its own slice.
- The root AGENTS.md keeps its own Feature Map under exactly the same rules: it owns the features whose lowest common subtree is the whole project, plus the project's primary, system-wide features. When the detail lives deeper, a root entry points to the owning doc instead of listing files.

**Keep it current.** When a change adds, removes, renames, or relocates a feature — or moves the files behind one — update the Feature Map in the owning doc in the same pass. A stale map is worse than none.

**Architecture overview.** To produce one, walk the DOX tree from the root and collect every Feature Map. Grouped by feature, the result is a feature-to-files map of the whole system — with no separate source of truth to maintain.

## Initialization

Run this when asked to initialize or index the project. Work top-down, then recurse into every boundary.

1. **Map the folder.** List its directory tree. Skip vendored, build, and version-control dirs (`node_modules`, `dist`, `build`, `target`, `.git`, `.svn`, and similar). Write down each folder and a one-line note on its purpose.
2. **Mark the boundaries.** Apply the boundary test (above) to every folder you listed. Mark each one "doc" or "no doc." Always mark submodules and subprojects "doc."
3. **Recurse.** For each folder you marked "doc," repeat steps 1–2 *inside* that folder. Keep going until you reach folders that contain no further boundaries. Do not stop at the top level — go as deep as the structure goes. If any folder already carries its own root AGENTS.md with the full DOX rules, it is a nested root: keep its whole doc tree as it is, index it as a child, and do not rebuild anything inside it.
4. **Write the docs.** Keep the DOX rules in the root AGENTS.md only — a nested root keeps its own copy; leave it unchanged. Write every other AGENTS.md using Child Doc Shape above; write a submodule or subproject as a sub-root. Leave Work Guidance and Verification empty where no standard or check exists yet.
5. **Wire the indexes.** In every doc that has children, fill the Child DOX Index — one line per direct child, naming what it covers. Mark a leaf `(none)`.
6. **Map the features.** In every doc — the root AGENTS.md included — fill the Feature Map with the features you can identify from the code, one bullet per feature, each with its Start file and supporting files. Put each feature in the doc closest to its code (see Feature Map locality); the root's map holds the project-spanning and primary features.
7. **Report.** Print the full tree you created and name any folder you deliberately left without a doc.

Done when: every boundary at every depth has an AGENTS.md, every Child DOX Index is filled (or `(none)` at a leaf), each doc's Feature Map — the root's included — lists the features identifiable at its level, and no index still reads "Not yet indexed" or "Not yet mapped."

## Read Before Editing

Before editing any file:

1. Read the root AGENTS.md.
2. List the files and folders you expect to touch.
3. For each target, walk from the root down to it, reading every AGENTS.md along the way — including any sub-root or nested root in the path.
4. Treat the nearest AGENTS.md as the local contract and the parents as repo-wide rules.
5. If two docs conflict, the closer one controls local details — but no child or sub-root may weaken DOX itself.

Re-read the applicable chain in the current session. Do not rely on memory.

## Update After Editing

Every meaningful change requires a DOX pass before the task is done. Update the closest owning AGENTS.md when a change affects:

- purpose, scope, ownership, or responsibilities;
- durable structure, contracts, workflows, or operating rules;
- required inputs, outputs, permissions, constraints, side effects, or artifacts;
- the set of features in this subtree, or the files that implement a feature (update the Feature Map in the owning doc);
- user preferences about behavior, communication, process, organization, or quality;
- any AGENTS.md creation, deletion, move, rename, or index change.

Update a parent when parent-level structure, ownership, workflow, or its child index changes. Update a child when a parent change alters its local rules. Remove stale or contradictory text immediately. A change that alters no behavior or contract may leave docs unchanged — but still do the pass to confirm that.

When the owning doc lies inside a nested root, make the update there — but say in your report that the change belongs to that project: commit or submit it in that project's own repository (for a git submodule, the parent repo then updates its pointer).

## Style

- Keep docs concise, current, and operational. Document stable contracts, not history.
- Prefer direct bullets with explicit names.
- Match the doc tree to the real structure: go deep where the project is deep, and do not flatten genuine boundaries to save docs.
- Do not duplicate a rule across files unless each scope needs its own version.
- Delete stale notes instead of explaining how things used to be.
- Trim obvious statements, repeated rules, misplaced detail, and warnings for risks that no longer exist.

## Closeout

1. Re-check changed paths against the DOX chain.
2. Update the nearest owning docs and any affected parents or children.
3. Refresh every affected Child DOX Index.
4. Refresh every affected Feature Map.
5. Remove stale or contradictory text.
6. Run existing verification when relevant.
7. If any changed file — doc or source — lies inside a nested root, say so: it must be committed or submitted in that project's own repository (for a git submodule, the parent repo also updates its pointer).
8. Report any docs you intentionally left unchanged and why.

## User Preferences

When the user requests a durable behavior change, record it here or in the relevant child AGENTS.md.

## Feature Map

- **Containerized application** — Builds and runs One Auth with persistent SQLite storage through the compose-helper workflow. Start: `docker-compose.yaml`. Files: `Dockerfile`, `compose-helper.sh`, `compose-helper.env`, `.config/.env.example`.
- **Python package and service** — FastAPI application implementing local administration and cross-target credential propagation. Start: `oneauth/main.py`. Files: `oneauth/`. Detail in `./oneauth`.
- **Operator and developer runbook** — Documents configuration, target prerequisites, operation, and verification. Start: `README.md`. Files: `pyproject.toml`.

## Child DOX Index

- `oneauth/` — FastAPI application, persistence, UI, connectors, and sync orchestration.
- `tests/` — pytest fixtures and behavioral coverage for users, connectors, and synchronization.

## Project rules (imported)

### Agent rules (lazyway-io boilerplate)

Read and follow every markdown file in `.agents/rules/` — they are always-on
rules for this project. On-demand skills live in `.agents/skills/` (Agent
Skills standard). Ignore other harnesses' config trees (`.cline/`,
`.clinerules/`, `.claude/`, `CLAUDE.md`) — they carry these same rules,
retuned for other agents.
