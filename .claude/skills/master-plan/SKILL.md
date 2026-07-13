---
name: master-plan
description: >-
  Create and execute a persistent on-disk plan for multi-phase or long-running
  work: a MASTER-PLAN.md plus phase-NNNN.md files in .tmp-agent-scratch/, a
  user-approval gate before execution, an autonomous execution loop with
  durable status markers, and a git commit after every completed phase
  (squashed into one meaningful commit when the user accepts the result).
  Use when a task needs several distinct phases, will likely span more than
  one session, or the user asks for a master plan. Sections 1–3 are the
  shared contract also loaded by master-plan-resume, master-plan-status, and
  master-plan-clear.
---

# MASTER PLAN — plan, get approval, execute

A plan that lives on disk so any session — this harness or another, before or after a crash or compaction — can continue exactly where the last one stopped. The durability rules below look pedantic; they are the whole point. Follow them exactly even when you could hold the plan in context, because the next reader may not be you.

Sections 1–3 are the **shared contract**, also honored by `master-plan-resume`, `master-plan-status`, and `master-plan-clear`. The file format is deliberately identical across every harness this skill family ships for — a plan started under one agent resumes under any other.

## 1. The scratch folder

- **Location:** `<project root>/.tmp-agent-scratch/`. Project root = the folder owning the task's files (its own `.git` or project manifest), never a multi-project workspace root. A task spanning projects keeps its plan in the project owning most of the work.
- **Gitignore it on creation:** ensure `.gitignore` contains a `.tmp-agent-scratch/` line (create `.gitignore` if the repo lacks one; skip outside git).
- **Contents:** exactly one `MASTER-PLAN.md` plus one `phase-NNNN.md` per phase (`phase-0001.md`, ...).
- **One plan at a time.** A previous plan with status `done` may be deleted and replaced. A non-done plan is never overwritten — resume it (`master-plan-resume`) or let the user abandon it (`master-plan-clear`). Never delete the folder on your own initiative, and never index it in any AGENTS.md.
- **Single writer:** only the main session writes plan files. Subagents (Task tool) are welcome for research within a sub-task, but you record their findings in the Log and you flip the statuses — never delegate plan writes. If you ARE a subagent, ignore this skill entirely.

## 2. Templates and status markers

**Status vocabulary** — exactly these five, lowercase, in brackets: `[todo]`, `[ongoing]`, `[done]`, `[skipped] — reason: <why>`, `[needs-human] — reason: <question/error>`. At most ONE `[ongoing]` phase and ONE `[ongoing]` sub-task may exist at any moment — `[ongoing]` is the crash flag a resuming session uses to know what to distrust.

`MASTER-PLAN.md`:

```markdown
# MASTER PLAN — <short task title>

## Meta
- Goal: <one sentence>
- Done when: <overall completion check>
- Plan status: drafting
- Plan status meaning: drafting | approved | executing | done | blocked-on-human
- Git: <yes | no — not a git repo>
- Branch: <branch, once execution starts; otherwise "-">
- Baseline commit: <full hash of HEAD at execution start; otherwise "-">

## Phases
- [todo] phase-0001 — <title>

## Log
- created plan with <N> phases
```

`phase-NNNN.md`:

```markdown
# phase-NNNN — <title>

- Status: todo
- Depends on: none
- Goal: <one line>
- Done when: <check for the whole phase>

## Sub-tasks
1. [todo] <action> — done when: <check>

## Log
- (append-only, one line per event)
```

Rules: the phase file's `Status:` is the source of truth, the master's line is a mirror — update both together, phase file wins on mismatch. Every phase and sub-task needs a runnable/observable "done when". 2–7 phases of 2–7 sub-tasks; more work means more phases, not longer lists. Statuses sit at line start so they flip with a targeted edit. Never renumber; new work gets the next free number plus a master Log line.

## 3. Git contract — commit trail, baseline, squash

Applies only when `Git: yes`; never `git init` on your own.

- **Baseline** (once, right before the first sub-task): `git status --porcelain` must be empty — if not, stop and ask the user (commit / let you commit as `pre(master-plan): baseline snapshot` / stash). The resume crash-heuristic depends on "dirty tree = died mid-work". Record `Branch:` and full `Baseline commit:` in Meta; log `baseline <short-hash> on <branch>`.
- **Commit every completed phase:** `git add -A && git commit -m "wip(master-plan): phase-NNNN — <title>"` immediately after the phase closes (also on `needs-human` with partial work). Log `commit <short-hash>` in phase and master Logs; log `no changes to commit` if nothing changed. Optional mid-phase checkpoints after risky sub-tasks use `wip(master-plan): phase-NNNN sub-task N — <action>`.
- **Never** push, switch branches, rebase, amend, or touch anything at or before the baseline.
- **Squash on acceptance** (user-gated, after the user accepts the finished result):
  1. If `git log <baseline>..HEAD` contains any commit that is not a recorded plan commit, do NOT squash — tell the user and let them decide.
  2. Otherwise ask; on yes, `git reset --soft <baseline>` and commit once with a meaningful message derived from the Goal (imperative subject, body listing the phases). On no, keep the wip commits.
  3. Either way, set Plan status `done`.

---

## 4. Write the plan

State the goal, the overall "done when", and your chosen approach in one short block, then create the scratch folder and write MASTER-PLAN.md (status `drafting`) and all phase files. If your harness's plan mode blocks file writes, compose the files in your plan presentation and write them verbatim as your first action once approved to act.

## 5. Approval gates — two, in order

**Gate A — approve the plan:** show the Goal, "Done when", and the one-line phase list; ask for approval. Iterate on feedback by editing the plan files. On approval: status `approved`, log it.

**Gate B — permission to execute:** ask before entering the loop. On yes: status `executing`, log it, record the git baseline (§3), start.

A single clear "approved, go" satisfies both. The original task prompt never pre-approves a plan the user hasn't seen.

## 6. Execution loop — autonomous

**Status discipline (the crash-safety core):** `[ongoing]` goes to disk BEFORE starting an item; its terminal status goes to disk IMMEDIATELY after finishing — never batched, never deferred. Keep your harness's todo/task list (if you're using one) a coarse one-item-per-phase mirror; the files are the source of truth.

Gate B was your permission for the whole loop — don't re-ask between phases. If the harness prompts for a specific tool permission, that's normal; it is never a reason to mark anything `needs-human`.

1. Pick the first `[todo]` phase whose `Depends on:` are all `[done]` (log `deps not met` and skip otherwise).
2. Mark it `[ongoing]` (file + mirror), log `started`.
3. For each sub-task: flip `[ongoing]` → do the work → run its "done when" → flip `[done]` / `[skipped] — reason:` / (after 2 failed attempts) `[needs-human] — reason:`, with a Log line. A `needs-human` sub-task doesn't stop the loop unless later sub-tasks depend on it — then close the phase early.
4. **Close the phase with a review:** run the phase-level "done when" (sub-tasks can all pass while the phase goal is missed); re-read the master Goal and remaining phases and amend `[todo]` phases NOW if this phase changed what they must do (log why; never touch `[done]` phases). A failing phase check gets a fix-up sub-task; after 2 failed fix-up rounds, phase → `needs-human`. Then set the phase terminal status, mirror it, summarize in the master Log, and **commit the phase** (§3).
5. **Phase boundaries are the right compaction points:** everything durable is on disk and committed. If context is getting heavy, flush any not-yet-logged decisions/gotchas to the Logs now, compact, then re-anchor by re-reading MASTER-PLAN.md and the next phase file (after any compaction — chosen or automatic — the files win over the summary; follow `master-plan-resume` if state looks inconsistent).
6. Repeat without stopping, asking, or mid-run progress summaries. Stop only when: all phases terminal and none `needs-human` → report and ask for acceptance (then §3 squash, which sets `done`); nothing actionable remains → status `blocked-on-human`, report; or the user interrupts (the files already hold the state).

## 7. Report on stop

```
Plan: <title> — <awaiting acceptance | blocked-on-human>
Phases: <X> done, <Y> skipped, <Z> needs-human, <W> todo (blocked)
Commits: <N> on <branch> since <baseline short-hash>   (omit if Git: no)
Needs you:
- phase-NNNN sub-task N: <exact question / error>      (omit if none)
```
