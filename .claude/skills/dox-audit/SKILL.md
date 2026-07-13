---
name: dox-audit
description: Audit / lint the health of this project's DOX tree (the hierarchy of AGENTS.md files). READ-ONLY — reports problems, never edits. Use when the user asks to audit DOX, lint the docs, check DOX health, find stale or missing AGENTS.md files, or verify doc coverage. To actually fix the findings, use dox-fix instead.
---

# DOX Audit (read-only)

Check the DOX tree for drift and report findings. This skill NEVER changes any file — if the user wants repairs, point them to `/dox-fix`.

## Step 0 — Precondition

Open the project root `AGENTS.md`. If it is missing or does not contain the heading `# DOX framework`, STOP and suggest `/dox-init`. Otherwise read it fully — especially "Where a doc goes: boundaries" (including "Nested roots") and "Child Doc Shape" — before auditing.

If the user named a specific folder, scope every step below to that folder and its parents' chain only.

## Step 1 — Map

Map the folders (skip `node_modules`, `dist`, `build`, `target`, `.git`, `.svn`, and similar) and apply the boundary test to every folder at EVERY depth. Mark every folder that carries its own root AGENTS.md with the full DOX rules as a **NESTED ROOT** — that covers git submodules, SVN externals, Perforce mapped paths, and any other independently versioned subproject.

## Step 2 — Check, in this order

1. **Coverage:** list any boundary — especially any submodule or subproject — that has no AGENTS.md, and any AGENTS.md sitting on a folder that is not a boundary.
2. **Child DOX Index:** for every doc, check it lists every direct child doc (one line each, `(none)` at a leaf) with no missing, extra, or leftover "Not yet indexed" entries.
3. **Feature Map:** for every listed feature, confirm its Start file and other files still exist; flag entries pointing to moved or deleted files, obvious features that have no entry, and any leftover "Not yet mapped" placeholder.
4. **Shape:** flag docs that skip required sections, use them out of order, or that should be sub-roots but are not. Exception: a nested root correctly keeps the full DOX rules and root shape — never flag it as a shape violation.
5. **Contracts:** flag any child or sub-root rule that conflicts with or weakens a parent, and any rule describing behavior or files that no longer exist. Mark conflicts involving a nested root as "decide with the owner" — the fix may belong in the parent, not the nested project.
6. **Framework version (best-effort):** read the `DOX v` line under `# DOX framework` in the root AGENTS.md, and fetch the same line from `https://raw.githubusercontent.com/jpbaking/dox/main/AGENTS.md`. If the project's version is older or the line is missing, flag it (minor) and suggest `/dox-upgrade`. Also note any nested root on an older version (its upgrade runs in its own repo). If the fetch fails, skip this check silently.

## Step 3 — Report

Group findings by severity — **broken/missing**, **stale**, **minor** — each with the file path and a one-line suggested fix. Do not fix anything. End by offering `/dox-fix` for the mechanical items, and `/dox-upgrade` if the framework version is outdated.
