---
name: master-plan-resume
description: >-
  Resume an unfinished master plan in a new session, after a crash, or after
  a context compaction: verify anything marked [ongoing], use git (dirty
  tree, commit trail vs plan logs) to detect where the previous session
  actually died, repair status/log drift, then re-enter the master-plan
  execution loop. Use when .tmp-agent-scratch/MASTER-PLAN.md exists with a
  Plan status other than done, when the user says "resume" or "continue the
  plan", or immediately after any compaction while a plan is executing. For
  creating a new plan use master-plan; for a report only use
  master-plan-status; for abandoning use master-plan-clear.
---

# MASTER PLAN — resume / crash recovery

You are picking up a plan another session (or your pre-compaction self) left behind. Assume that session may have died mid-write: statuses, logs, and commits can each be slightly behind reality. Your job is to find the true state, repair the bookkeeping, and continue — never to redo verified work and never to trust unverified work.

## Step 0 — load the contract

Read sections 1–3 of `.cline/skills/master-plan/SKILL.md` (scratch folder, templates/status markers, git contract). Everything below uses those definitions. If you are a read-only subagent, stop: this skill is not for you.

If `<project root>/.tmp-agent-scratch/MASTER-PLAN.md` does not exist, there is nothing to resume — say so and suggest `master-plan`. If its Plan status is `done`, say the plan already finished and stop.

## Step 1 — decide whether to resume at all

- The user asked to continue/resume, or their request matches the plan's Goal → resume without asking.
- The plan is `blocked-on-human` and the user's message answers its pending questions → record the answers in the affected items' Log, flip those items back to `[todo]`, set Plan status to `executing`, then continue below.
- The plan is `drafting` or `approved` (execution never started) → no crash recovery needed; show the phase list and re-enter `master-plan` at its approval gates.
- The user's request is unrelated → tell them an unfinished plan exists (quote its Goal and progress counts) and ask: resume it, set it aside and do the new task, or abandon it (`master-plan-clear`). Never silently delete it.

## Step 2 — establish ground truth from git

Skip this step if `Git: no` in the Meta.

1. `git status --porcelain`. **Dirty tree = crash evidence.** The previous session committed after every completed phase, so uncommitted changes mean it died mid-phase — after doing work but possibly BEFORE updating statuses or logs. Treat the diff (`git diff` + untracked files) as belonging to the `[ongoing]` item; read it to see how far the work actually got.
2. `git log --oneline <baseline>..HEAD` and compare against the `commit <hash>` lines in the plan Logs:
   - A phase marked `done` with NO matching commit and a clean tree → its changes were probably committed under a later hash or lost; verify the phase's "done when" check before trusting it.
   - A `wip(master-plan)` commit for phase-NNNN exists but the phase file still says `ongoing` → the crash happened between commit and status write; verify the phase check, then flip it to `done`, log `reconciled from commit <short-hash> on resume`.
   - Foreign commits (not `wip(master-plan)`/`pre(master-plan)`) after the baseline → note them in the master Log; they make the final squash unsafe, and the user must be told at acceptance time.
3. Never reset, revert, or discard anything in this skill — recovery only reads git and commits verified work. Reverting belongs to `master-plan-clear`.

## Step 3 — verify and repair the plan files

1. Read MASTER-PLAN.md fully. Find any `[ongoing]` phase; read its file; find any `[ongoing]` sub-task.
2. **Trust nothing marked `[ongoing]`.** Run that sub-task's "done when" check and read its Log plus the git diff from Step 2:
   - Check passes → flip to `[done]`, log `verified on resume`.
   - Check fails or the work is half-done → redo the sub-task from its start (it stays `[ongoing]` while you do).
3. Fix any invariant violations (two `[ongoing]` items, master/phase status mismatch — the phase file wins, missing ` — reason:` suffixes) before doing anything else.
4. If Step 2 found a dirty tree and the `[ongoing]` phase turns out to be fully complete after verification, close it properly: phase Status `done`, mirror, Log, and the phase commit per the git contract.
5. Append `resumed` to the master Log.

## Step 4 — continue

Re-enter the execution loop: section 6 of `.cline/skills/master-plan/SKILL.md`, step 1. All of that skill's discipline (status flips before/after work, phase commits, boundary compaction, stop conditions, reporting, acceptance + squash) applies from here on.
