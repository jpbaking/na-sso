---
name: dox-init
description: Initialize the DOX documentation tree (hierarchy of AGENTS.md files) for this project. Use when the user asks to set up DOX, initialize DOX, bootstrap AGENTS.md docs, or index the project with a doc tree. Handles both brand-new projects (little or no code yet) and existing codebases with code but no docs. Do not use for checking or repairing an existing DOX tree — that is dox-audit / dox-fix.
---

# DOX Init

Build this project's DOX tree: one AGENTS.md per real boundary, from the root down.

## Step 0 — Ensure the framework is present

1. Open `AGENTS.md` in the project root.
2. If it exists and contains the heading `# DOX framework`, the framework is present — go to Step 1.
3. If it is missing or has no DOX rules, install the framework as `AGENTS.md` in the project root, taking its text from the first of these sources that works:
   1. **Packaged template (preferred — no network needed):** copy `templates/AGENTS.md` from this skill's own folder (the folder containing this SKILL.md).
   2. **Fetch fallback:** if the packaged template is missing, fetch `https://raw.githubusercontent.com/jpbaking/dox/main/AGENTS.md`.
   3. If both fail, STOP. Ask the user to copy `AGENTS.md` from https://github.com/jpbaking/dox into the project root, then run `/dox-init` again.

   Either way: if a root AGENTS.md already existed with other content, keep that content — place the framework text at the top, and move the old content below it under a heading `## Project rules (imported)`. Tell the user to review that section. (The packaged template may lag the latest release; `/dox-upgrade` brings it current later.)
4. Read the whole root AGENTS.md now. Every step below follows its rules exactly.

## Step 1 — Decide the mode

Look at the project and pick exactly one:

- **Already initialized** — child AGENTS.md files already exist, or the root's Child DOX Index is filled. Do NOT re-initialize. Say so and suggest `/dox-audit` (check) or `/dox-fix` (repair). STOP.
- **New project** — little or no code yet: empty repo, scaffolding only, no meaningful source files.
- **Existing project** — real code exists, but no DOX child docs yet.

## Step 2A — New project (little or no code)

1. Ask the user for a one-line description of what they are building, if they have not given one.
2. Fill the root AGENTS.md live sections (User Preferences, Feature Map, Child DOX Index) with the project-wide rules you can state from that description.
3. Create a child AGENTS.md for each boundary already known to be needed (each submodule, subproject, or area with its own build/run/test), using **Child Doc Shape** from the root AGENTS.md.
4. Fill every Child DOX Index, and fill the Feature Map for each area you can already describe.
5. Go to Step 3.

## Step 2B — Existing project (has code, no docs)

Follow the **Initialization** procedure in the root AGENTS.md exactly — all steps, no skipping. While doing so, enforce these points:

1. Apply the boundary test at EVERY depth, not just the top level. Every submodule and subproject gets its own AGENTS.md (a sub-root); recurse into each one.
2. Any folder that already carries its own root AGENTS.md with the full DOX rules is a **nested root**: leave its whole doc tree unchanged and just index it as a child. Never rewrite it.
3. Fill every Child DOX Index (one line per direct child; `(none)` at a leaf).
4. Fill every Feature Map — the root AGENTS.md's included — giving each feature its Start file and its other files.
5. Stop only when the "Done when" check in the Initialization procedure is satisfied.

## Step 3 — Report

Print the full tree of AGENTS.md files you created, name any folder you deliberately left without a doc, and list any nested roots you found and left untouched.
