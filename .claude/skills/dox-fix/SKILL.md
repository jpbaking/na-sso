---
name: dox-fix
description: Audit AND auto-repair this project's DOX tree (the hierarchy of AGENTS.md files). Edits docs only — never source code. Use when the user asks to fix, repair, heal, or clean up the DOX docs, AGENTS.md tree, stale indexes, or broken Feature Maps. For a report without any edits, use dox-audit instead.
---

# DOX Fix (auto-repair)

Run the DOX health audit, fix the safe mechanical problems, and leave judgment calls to the user. Edit docs only — NEVER change source code.

## Step 0 — Precondition

Open the project root `AGENTS.md`. If it is missing or does not contain the heading `# DOX framework`, STOP and suggest `/dox-init`. Otherwise read it fully before changing anything.

If the user named a specific folder, scope every step below to that folder only.

## Step 1 — Audit first

Run the full audit from `/dox-audit` (Steps 1–2 there): boundary coverage at every depth, Child DOX Index, Feature Map file references, Child Doc Shape, and parent/child contract conflicts. Keep the findings list — it drives the fixes.

## Step 2 — NESTED ROOTS ARE OFF-LIMITS

Never edit any file inside a folder that carries its own root AGENTS.md with the full DOX rules (a git submodule, SVN external, Perforce mapped path, or other independently versioned subproject). Do not rewrite its doc into Child Doc Shape and do not strip its DOX rules — that root shape is correct there. List its problems for the user instead; fixes there must be committed in that project's own repository.

## Step 3 — Fix the safe, mechanical problems

- Create a missing AGENTS.md at every uncovered boundary using Child Doc Shape (write a submodule or subproject as a sub-root).
- Repair every Child DOX Index: add missing children, drop entries for docs that no longer exist, mark leaves `(none)`, and replace any "Not yet indexed" placeholder.
- Fix Feature Map entries: correct paths to moved files, remove entries whose files are gone, add obvious missing features with their Start file, and replace any "Not yet mapped" placeholder.
- Fix Child Doc Shape: restore missing sections and their order; convert a submodule/subproject doc into a sub-root where it should be one (but never a nested root — Step 2).
- Delete text describing files or behavior that no longer exist.

## Step 4 — Do NOT guess on judgment calls

For contract conflicts (a child weakening a parent), ambiguous ownership, or a rule you cannot tell is intentional, leave it unchanged and list it for the user instead.

## Step 5 — Close out

Follow the **Closeout** procedure in the root AGENTS.md to finish.

## Step 6 — Report

List every file you created or changed (one line each). Then, separately, list the judgment calls and nested-root fixes you left for the user to decide.
