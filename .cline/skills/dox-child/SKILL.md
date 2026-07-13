---
name: dox-child
description: Assess one candidate folder against the DOX boundary test and, if it really is a boundary, initialize its child AGENTS.md (and wire it into the parent). If it is not a boundary, explain why and create nothing. Use when the user gives a folder path and asks to add a child doc, "dox" a directory, or asks whether a folder deserves its own AGENTS.md. For whole-project initialization use dox-init.
---

# DOX Child

Judge one folder with the boundary test, then either initialize it as a DOX child or explain why it should not be one. Never create a doc just because the user pointed at the folder — the test decides.

## Step 0 — Preconditions

1. Open the project root `AGENTS.md`. If it is missing or does not contain the heading `# DOX framework`, STOP and suggest `/dox-init`.
2. The user must name a candidate directory (relative path). If they did not, ask for it.
3. If the path does not exist or is not a directory, STOP and say so.

## Step 1 — Check what is already there

Look inside the candidate folder:

- It already has an AGENTS.md **with the full DOX rules** (`# DOX framework` heading): it is a **nested root** — leave it completely unchanged. Check only that the parent chain indexes it; add the missing Child DOX Index line in the parent if needed. Report and STOP.
- It already has a child AGENTS.md: it is already covered. Suggest `/dox-audit` scoped to it if the user suspects it is stale. Report and STOP.
- No AGENTS.md: continue.

## Step 2 — Read the chain

Follow "Read Before Editing" from the root AGENTS.md: read every AGENTS.md from the root down to the candidate's nearest documented parent. Note that parent — it is the doc that currently covers the candidate, and the one you will update if a child is created.

## Step 3 — Apply the boundary test

Apply "Where a doc goes: boundaries" from the root AGENTS.md, exactly as written. Inspect the folder's actual contents for evidence (build files, manifests, its own purpose/contracts) — do not guess from the name.

- **Boundary** if ANY create-condition holds (submodule/subproject/nested repo; separately built/run/tested/deployed; own purpose or audience; own contracts or rules). Never skip a real submodule or subproject.
- **Not a boundary** if ALL do-NOT-create conditions hold (just a grouping folder; everything follows the parent's rules; a doc would only repeat the parent). When genuinely unsure, it is NOT a boundary — the nearest parent covers it.

## Step 4A — It is a boundary: initialize it

1. Write the child AGENTS.md using **Child Doc Shape**; write a submodule or subproject as a **sub-root**. Leave Work Guidance and Verification empty if no standard or check exists yet.
2. Fill its Feature Map from the code (Start file + other files per feature) and its Child DOX Index (`(none)` if it has no child docs).
3. Recurse: apply the boundary test inside the new child's subtree and create docs for any deeper boundaries too, per the framework's Initialization rules. A nested root found inside stays untouched.
4. Wire the parent: add one Child DOX Index line in the nearest parent doc, and move any Feature Map entries whose code now lives under the new child (locality rule — point the parent entry to the child where appropriate).

## Step 4B — It is not a boundary: explain, create nothing

Tell the user, concretely:

- which do-NOT-create conditions hold (with the evidence you saw),
- which create-conditions you checked and why none apply,
- which existing AGENTS.md already covers the folder.

Do not create or edit any file.

## Step 5 — Report

If created: list every doc written or updated, and show the folder's new place in the tree. If declined: give the Step 4B explanation. Either way, mention anything that made the call close.
