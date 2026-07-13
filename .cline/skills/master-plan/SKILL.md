---
name: master-plan
description: >-
  Create and execute a persistent on-disk plan for multi-phase or long-running
  work: a MASTER-PLAN.md plus phase-NNNN.md files in .tmp-agent-scratch/, a
  user-approval gate before execution, an autonomous execution loop with
  mechanical status markers, and a git commit after every completed phase
  (squashed into one meaningful commit when the user accepts the result).
  Use when a task needs 2+ distinct phases or roughly 5+ sub-tasks, will
  likely span more than one session, or the user says "plan" or "master
  plan". Sections 1–3 are the shared contract also loaded by
  master-plan-resume, master-plan-status, and master-plan-clear. For
  resuming an existing plan use master-plan-resume; for a progress report
  use master-plan-status; for abandoning use master-plan-clear.
---

# MASTER PLAN — plan, get approval, execute

This skill gives multi-phase tasks a plan that lives on disk, executes autonomously after the user approves it, and survives crashes. The plan is a folder of markdown files with mechanical status markers plus a git commit trail; any new session can read them and continue exactly where the last one stopped (that continuation is the `master-plan-resume` skill).

This skill composes with the rule files when present: if `00-core-reasoning-rules.md` is active, your `<steps>` block just points at the master plan file (do not duplicate the plan in chat); if the `dox.md` rule is active, every file edit during execution still follows its DOX contract.

Because this text lives in the conversation (not the system prompt), a context compaction can erase it. **After ANY compaction or session restart while a plan is executing: load the `master-plan-resume` skill and follow it before continuing.** The plan files on disk always win over your memory of them.

---

Sections 1–3 below are the **shared contract**. `master-plan-resume`, `master-plan-status`, and `master-plan-clear` must read them before doing anything.

## 1. The scratch folder

- **Location:** `<project root>/.tmp-agent-scratch/`. The project root is the folder the task's files belong to — the one with its own `.git` or project manifest (`package.json`, `pyproject.toml`, `go.mod`, `Cargo.toml`, `Makefile`), found by walking up from the task's files. In a multi-project workspace, never the shared workspace root — one scratch folder per project. If a task truly spans several projects, put the plan in the project owning most of the work and name the other project paths in the master plan Goal.
- **Gitignore it, mechanically, right after creating it:** if the project has a `.gitignore`, run `grep -qx '.tmp-agent-scratch/' .gitignore || echo '.tmp-agent-scratch/' >> .gitignore` (and commit nothing yet — the baseline commit in section 3 handles it). If the project is a git repo with no `.gitignore`, create one containing that line. If it is not a git repo, skip this.
- **Contents:** exactly one `MASTER-PLAN.md` plus one `phase-NNNN.md` per phase (zero-padded: `phase-0001.md`, `phase-0002.md`, ...).
- **One plan at a time.** If a previous plan exists and its Plan status is `done`, you may delete those files and start fresh. If it is NOT done, never overwrite it — load `master-plan-resume`, or, if the user wants to drop it, `master-plan-clear`.
- Never delete the scratch folder on your own initiative — deletion happens only through `master-plan-clear`, at the user's explicit request. Never list `.tmp-agent-scratch/` in any `AGENTS.md` or Child DOX Index — it is temporary and gitignored; DOX Closeout does not apply to files inside it.
- **Single writer.** Only the main agent session writes plan files. Cline subagents are read-only by design and cannot update a status — never delegate a plan edit or status flip to one. Using subagents for research inside a sub-task is fine and encouraged: the `[ongoing]` marker is already on disk before you dispatch them, and YOU record their findings in the phase Log when they return. **If you are a read-only subagent:** ignore this skill entirely — do not check for, read, resume, or report on any plan; just perform your assigned research and return your findings.

## 2. Templates and status markers — copy exactly

**Status vocabulary** — these five, nothing else, always lowercase in square brackets:

- `[todo]` — not started.
- `[ongoing]` — being worked on RIGHT NOW. At most ONE `[ongoing]` phase and ONE `[ongoing]` sub-task may exist at any moment, ever. This marker is the crash flag: a resuming session treats `[ongoing]` work as unverified.
- `[done]` — finished and its "done when" check passed.
- `[skipped]` — you decided it is not needed. MUST end with ` — reason: <why>`.
- `[needs-human]` — cannot proceed without the user (a decision, clarification, credential, or a failure you could not fix). MUST end with ` — reason: <the exact question or error for the user>`.

`MASTER-PLAN.md` template:

```markdown
# MASTER PLAN — <short task title>

## Meta
- Goal: <one sentence, from <understand>>
- Done when: <overall completion check>
- Plan status: drafting
- Plan status meaning: drafting | approved | executing | done | blocked-on-human
- Git: <yes | no — not a git repo>
- Branch: <branch name, once execution starts; otherwise "-">
- Baseline commit: <full hash of HEAD at execution start; otherwise "-">

## Phases
- [todo] phase-0001 — <title>
- [todo] phase-0002 — <title>

## Log
- created plan with <N> phases
```

`phase-NNNN.md` template:

```markdown
# phase-NNNN — <title>

- Status: todo
- Depends on: none            <!-- or: phase-0001, phase-0003 -->
- Goal: <one line>
- Done when: <check for the whole phase>

## Sub-tasks
1. [todo] <action> — done when: <check>
2. [todo] <action> — done when: <check>

## Log
- (append one line per event; never edit or delete old lines)
```

Rules:

- A phase file's `Status:` line is the source of truth for that phase; the matching line in MASTER-PLAN.md is a mirror. Update both in the same breath. On any mismatch, the phase file wins — fix the master.
- Every phase and every sub-task MUST have a "done when" check you can actually run or observe.
- Keep it small: 2–7 phases, 2–7 sub-tasks each. Work that needs more sub-tasks becomes another phase, not a longer list.
- Statuses live at the start of the line so they can be flipped with a single targeted replace (`1. [todo] ...` → `1. [ongoing] ...`). Never rewrite a whole plan file just to change one status.
- Never renumber existing phases or sub-tasks. New work discovered mid-flight gets the next free number, plus a Log line in the master saying what was added and why.

## 3. Git contract — commit trail, baseline, squash

All git steps apply ONLY when `Git: yes` in the master plan Meta. If the project is not a git repo, set `Git: no` and skip every git step in every master-plan skill — never `git init` on your own.

- **Baseline (recorded once, right before the first sub-task of the first phase):**
  1. Run `git status --porcelain`. If it is NOT empty, STOP and ask the user: commit their changes themselves, let you commit them as `pre(master-plan): baseline snapshot`, or stash them. Never start executing on a dirty tree — the crash-detection heuristic in `master-plan-resume` depends on "dirty tree = crashed mid-work".
  2. Record the current branch in `Branch:` and the full hash of `HEAD` in `Baseline commit:` in the master plan Meta. Log `baseline <short-hash> on <branch>` in the master Log.
- **Commit on every completed phase:** immediately after a phase closes as `done` (or `needs-human` with partial work), run `git add -A && git commit -m "wip(master-plan): phase-NNNN — <title>"`. The scratch folder is gitignored, so plan bookkeeping is never committed. Append `commit <short-hash>` to the phase Log and master Log. If the phase changed no files, log `no changes to commit` instead.
- **Optional mid-phase checkpoint:** after a sub-task whose changes were large or risky, you MAY commit early with `wip(master-plan): phase-NNNN sub-task N — <action>`. Same logging.
- **Never** push, switch branches, rebase, amend, or touch commits older than the baseline. The trail is strictly linear commits on top of the baseline.
- **Squash on acceptance (final step, user-gated):** when the plan finishes and the user accepts the result:
  1. Run `git log --oneline <baseline>..HEAD`. If it contains ANY commit that is not one of the plan's recorded `wip(master-plan)`/`pre(master-plan)` commits, do NOT squash — foreign commits are interleaved; tell the user and let them decide.
  2. Otherwise ask: "Squash the <N> plan commits into one?" On yes: `git reset --soft <baseline>` then one commit whose message is derived from the plan Goal — imperative subject line, body listing the phases. Log `squashed into <short-hash>` in the master Log.
  3. On no: keep the wip commits as they are.
  4. Either way, set Plan status to `done` afterwards.

---

## 4. Writing the plan

1. Before writing any plan file, state the task in one sentence, state what "done" means, and choose one approach — inside the `<understand>` and `<plan>` blocks if `00-core-reasoning-rules.md` is active, otherwise plainly in your reply. The chosen approach becomes the master plan (its Goal and "Done when" come straight from this); its decomposition becomes the phases.
2. **Write the plan files immediately — in either mode.** Creating `.tmp-agent-scratch/` and its files is part of planning itself, not part of executing: this workspace expects Cline's **Strict Plan Mode** setting to be disabled, so these writes are allowed even in PLAN MODE. Create the scratch folder, gitignore it, and write MASTER-PLAN.md (Plan status `drafting`) and every phase file now, before any other work.
3. **Fallback — only if file writes are actually blocked** (Strict Plan Mode turned out to be enabled): compose the full MASTER-PLAN.md and every phase file inside your response, as fenced blocks. When switched to ACT MODE, your FIRST tool actions — before any other work — are: create the scratch folder, gitignore it, write those files to disk exactly as composed.

## 5. Approval gates — two, in order, no skipping

**Gate A — approve the plan.** Show the user the Goal, the "Done when", and the phase list (one line each; sub-task counts, not sub-task text). Ask plainly: "Do you approve this plan?" Then:

- Feedback → edit the plan files (add/remove/reword phases and sub-tasks), show the updated phase list, ask again. Repeat until approved.
- Approved → set Plan status to `approved`, log `plan approved by user`.

**Gate B — permission to execute.** After approval, ask: "Start executing now?" Do not begin the loop until the user clearly says yes. On yes: set Plan status to `executing`, log `execution authorized`, record the git baseline (section 3), and enter the loop.

One-message shortcut: if a single user message clearly gives both ("plan approved, go ahead"), that satisfies both gates — flip both statuses and start. Silence, "looks interesting", or an unrelated question satisfies neither. Never treat the original task prompt as pre-approval of a plan the user has not seen.

## 6. Execution loop — autonomous

**Status discipline (the crash-safety core):** write `[ongoing]` to disk BEFORE starting an item, and write its terminal status (`[done]` / `[skipped]` / `[needs-human]`) to disk IMMEDIATELY after finishing it — before touching the next item, never batched, never deferred to the end.

**Focus Chain:** if Cline's Focus Chain todo list is enabled, keep it a COARSE mirror of the master plan — one focus-chain item per phase, ticked when the phase closes. Never copy sub-tasks into it. The plan files are the source of truth; if the two disagree, fix the focus chain to match the files, never the reverse.

**Permissions:** the user grants tool permissions through Cline's auto-approve settings for the session. Whatever those settings have checked, you are explicitly allowed to do throughout this loop WITHOUT asking in chat — Gate B was the permission to execute. If an action is NOT covered, Cline itself shows an approval prompt — wait for the click and continue. An approval pause is the harness working as intended: not a failure, and never a reason to mark an item `needs-human`.

Loop:

1. Read MASTER-PLAN.md. Pick the first phase that is `[todo]`.
2. Check its `Depends on:` list. Every listed phase must be `[done]`. If not, leave it `[todo]`, log `deps not met` in the master Log, and try the next `[todo]` phase.
3. Mark the phase `[ongoing]` (phase file Status line AND master mirror). Log `started` in the phase Log.
4. For each sub-task in order:
   a. Flip it to `[ongoing]`. Save the file.
   b. Do the work. Any other active rules still apply here — e.g. `00`'s read-before-edit and verification discipline, the DOX chain from `dox.md`.
   c. Run its "done when" check.
   d. Flip it to `[done]`, or `[skipped] — reason: ...`, or — after 2 failed attempts on the same sub-task — `[needs-human] — reason: <exact error/question>`. Save. Append one Log line saying what happened.
   e. A `[needs-human]` sub-task does NOT stop the loop: continue with the next sub-task if it does not depend on the failed one; otherwise close the phase now (step 5).
5. **Close the phase with a review.** When no `[todo]`/`[ongoing]` sub-tasks remain (or step 4e forced closure), do NOT mark the phase done yet:
   a. Run the phase's own "done when" check — the whole-phase one, not the sub-tasks'. Sub-tasks can all pass while the phase goal is still missed.
   b. Review against the master plan: re-read MASTER-PLAN.md's Goal and the remaining phases. Did this phase's outcome — or anything discovered while doing it — change what a later `[todo]` phase must do? If yes, amend those phase files NOW, while the details are still in context: edit `[todo]` phases freely, add new phases with the next free numbers, never touch `[done]` ones. Log what changed and why in the master Log.
   c. If the phase "done when" check fails: the phase is not done — add a fix-up sub-task (next free number) and continue at step 4. After 2 failed fix-up rounds, set the phase to `needs-human — reason: <what still fails>`.
   d. Now set the phase Status: `done` (all sub-tasks done/skipped AND the phase check passed) or `needs-human` (any sub-task needs-human, or step c gave up). Mirror it in the master, append a one-line summary to the master Log.
   e. **Commit the phase** per section 3 (`wip(master-plan): phase-NNNN — <title>`), and log the hash.
6. **Compact at the boundary** — context hygiene, done autonomously, no user permission needed:
   a. A phase boundary is the ideal compaction point: everything durable was just written to disk AND committed, so what the summary loses, the plan files and git still hold. A mid-phase auto-compaction picks the worst possible moment instead — this step is how you prevent it.
   b. Check your context window usage (shown in environment details). Below roughly half: skip this step entirely and go to step 7. At roughly half or more: compact now.
   c. Before compacting, do a **pre-compaction flush** — treat the compaction as a planned crash: anything you still need that is NOT yet in the plan files (a decision made, a gotcha found, a port, a path, a command that works) gets appended to the phase Log or master Log first. After compaction you must be able to continue from the plan files plus the summary alone.
   d. Compact using Cline's context condensing (the same summarization Auto Compact / `/smol` uses).
   e. Immediately after compacting, re-anchor exactly like a resume: **load the `master-plan-resume` skill and follow it** (the compaction summary will not preserve this text). Wherever the summary and the plan files disagree, the files win.
   f. Never compact mid-phase or mid-sub-task by choice — boundaries only.
7. Go back to step 1. Do NOT stop between phases, do not ask "shall I continue?", do not summarize progress mid-run. Stop ONLY when one of these is true:
   - **All phases are terminal, none needs-human** → report (section 7) and offer acceptance: ask the user to review the result; on acceptance run the squash procedure (section 3), which also sets Plan status to `done`.
   - **Nothing actionable remains** (every remaining phase is `[needs-human]` or is `[todo]` with unmet dependencies) → set Plan status to `blocked-on-human`. Report.
   - **The user interrupts** → the files and commits already hold the exact state; nothing extra to do. `master-plan-resume` picks it up later.

## 7. Reporting on stop

Whenever the loop stops (finished or blocked), report in this shape and nothing more:

```
Plan: <title> — <awaiting acceptance | blocked-on-human>
Phases: <X> done, <Y> skipped, <Z> needs-human, <W> todo (blocked)
Commits: <N> on <branch> since <baseline short-hash>   <- omit if Git: no
Needs you:
- phase-NNNN sub-task N: <the exact question / error>   <- one line per needs-human item; omit section if none
```

If the plan finished, follow the report with the acceptance question from section 6 step 7.
