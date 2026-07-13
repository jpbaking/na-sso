---
name: dox-upgrade
description: Upgrade this project's DOX framework rules (the root AGENTS.md) to the latest released version while preserving all project content — User Preferences, Feature Map, Child DOX Index, and imported project rules. Use when the user asks to upgrade or update DOX, refresh the framework, or when dox-audit reports the framework version is outdated. Not for repairing the doc tree — that is dox-fix.
---

# DOX Upgrade

Replace the framework rules in the root AGENTS.md with the latest version, keep every piece of project content, then reconcile the tree. Only the framework text changes — project knowledge is never lost.

## Step 0 — Preconditions

Open the project root `AGENTS.md`. If it is missing or does not contain the heading `# DOX framework`, STOP and suggest `/dox-init`.

## Step 1 — Fetch the latest framework

Download `https://raw.githubusercontent.com/jpbaking/dox/main/AGENTS.md` to a temporary file (do NOT overwrite the root AGENTS.md yet). If the download fails, STOP and tell the user — never upgrade from memory.

## Step 2 — Compare versions

The framework version is the line starting `DOX v` directly under the `# DOX framework` heading.

- Same version in both files: report "already on the latest version", run Step 6 (the packaged template may still be older), delete the temp file, STOP.
- The project file has no `DOX v` line: it predates versioning — continue.
- Different versions: continue.

## Step 3 — Merge (never lose project content)

1. In the CURRENT root AGENTS.md, collect the project's content: the bodies of `## User Preferences`, `## Feature Map`, and `## Child DOX Index`, plus every section that does not exist in the new framework text (for example `## Project rules (imported)`).
2. Start from the NEW framework text in full.
3. In the new text, replace the placeholder body of each live section (`## User Preferences`, `## Feature Map`, `## Child DOX Index`) with the project's existing body — but keep the new placeholder wherever the project's section was itself still a placeholder or missing.
4. Append the project-only sections from step 1 at the end, unchanged and in their original order.
5. Write the result as the root AGENTS.md. Keep the temp file — Step 6 still needs the pristine framework text; delete it only after Step 6.
6. If any piece of the old file does not clearly belong to either the framework or a project section, KEEP it and list it for the user — never silently drop text.

## Step 4 — Nested roots stay untouched

Never upgrade a nested root (a folder whose own AGENTS.md carries the full DOX rules) — its framework belongs to its own repository. If its `DOX v` line is older than the new version, list it and note the upgrade must be run inside that project.

## Step 5 — Reconcile the tree

New rules usually create new mechanical debt (new required sections, new placeholders). Run the `/dox-fix` procedure now to bring the doc tree up to the new rules.

## Step 6 — Refresh the packaged template

A `dox-init` skill may be installed for one or more agent harnesses, each packaging an offline copy of the framework. For each of `.cline/skills/`, `.claude/skills/`, and `.agents/skills/` that contains `dox-init/templates/AGENTS.md`, overwrite that file with the NEW framework text in full (the pristine framework from Step 1 — placeholders and all, never the merged project file). Skip silently whichever does not exist.

## Step 7 — Report

State: old version → new version, which live sections were carried over, which project-only sections were kept, any text you kept because you could not classify it, nested roots left on older versions, whether the packaged template was refreshed, and what the `/dox-fix` pass changed.
