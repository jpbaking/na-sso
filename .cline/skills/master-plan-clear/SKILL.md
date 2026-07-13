---
name: master-plan-clear
description: >-
  Abandon the current master plan: optionally revert the git commits made
  during its execution (with explicit warnings — reverting discards code
  changes), then permanently delete .tmp-agent-scratch/ (unrecoverable).
  Run ONLY when the user explicitly asks to abandon, cancel, reset, scrap,
  or clear the plan — never on your own judgment and never as a shortcut
  around a blocked or messy plan. For continuing instead use
  master-plan-resume; for just looking use master-plan-status.
---

# MASTER PLAN — clear / abandon

Two independent, both destructive, both user-gated decisions: (A) revert the code changes the plan committed, (B) delete the plan files. Ask about each separately, warn plainly, and default to NO — anything other than a clear yes means do nothing for that decision.

## Step 0 — load the contract and show what's at stake

1. Read sections 1–3 of `.cline/skills/master-plan/SKILL.md`. If `<project root>/.tmp-agent-scratch/MASTER-PLAN.md` does not exist, say there is no plan to clear and stop.
2. Show the user exactly what they are abandoning: quote the Goal, the progress counts (X done, Y skipped, Z needs-human, W todo), and list any pending needs-human questions — those will be lost too.

## Step A — revert the plan's commits? (git repos only)

Skip this step entirely — and say you are skipping it — if `Git: no`, if `Baseline commit:` is `-` (execution never started), or if `git log --oneline <baseline>..HEAD` is empty.

1. Show `git log --oneline <baseline>..HEAD` so the user sees exactly which commits exist.
2. **Warn, plainly, before asking:** reverting permanently discards ALL code changes made during the plan's execution — the work itself, not just bookkeeping. If `git status --porcelain` is dirty, warn additionally that uncommitted changes will be destroyed too.
3. Check for foreign commits: if anything in `<baseline>..HEAD` is not a recorded `wip(master-plan)`/`pre(master-plan)` commit, a hard reset would destroy someone else's work — REFUSE it. Offer instead: `git revert` of the plan commits individually (keeps history, needs conflict handling), or leave git untouched. Let the user choose.
4. Ask: "Revert to baseline `<short-hash>`, discarding these <N> commits?" Default NO.
   - Yes (and no foreign commits): `git reset --hard <baseline>`. Confirm what was discarded.
   - No: leave every commit exactly as it is and say so — the work stays in the project.
5. Never touch commits at or before the baseline, never push, never switch branches.

## Step B — delete the plan files?

1. **Warn, plainly:** deleting is NOT recoverable. The master plan, every phase file, their logs, and all pending needs-human questions are permanently gone; no resumed or future session can get them back. (If Step A was declined or skipped, also state: the code changes stay — only the plan bookkeeping is deleted.)
2. Ask for explicit confirmation. Default NO.
   - Yes: delete the entire `.tmp-agent-scratch/` folder. Confirm it is gone, and restate whether project files/commits were or were not touched (per Step A's outcome).
   - No: change nothing, say so in one line, and continue with whatever the user wants next. A kept plan can still be resumed via `master-plan-resume`.
