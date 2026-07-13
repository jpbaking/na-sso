---
name: master-plan-status
description: >-
  READ-ONLY progress report on the current master plan: phase/sub-task
  counts, pending needs-human questions, git commit trail versus baseline,
  and any bookkeeping inconsistencies — without changing a single file or
  running the plan. Use when the user asks "where are we", "plan status",
  "what's left", or wants to review progress before deciding to resume or
  abandon. To continue work use master-plan-resume; to abandon use
  master-plan-clear.
---

# MASTER PLAN — status (read-only)

Report the true state of the plan. This skill NEVER writes: no status flips, no log lines, no commits, no repairs. If you find problems, you report them and name the skill that fixes them.

## Step 0 — load the contract

Read sections 1–3 of `.cline/skills/master-plan/SKILL.md`. If `<project root>/.tmp-agent-scratch/MASTER-PLAN.md` does not exist, say there is no plan and stop.

## Step 1 — gather (reads only)

1. Read MASTER-PLAN.md and every `phase-NNNN.md`.
2. If `Git: yes`: run `git status --porcelain` and `git log --oneline <baseline>..HEAD` (both read-only).

## Step 2 — report

```
Plan: <title> — <plan status>
Goal: <goal line>
Phases: <X> done, <Y> skipped, <Z> needs-human, <W> todo, <V> ongoing
Per phase:
- phase-NNNN <status> — <title> (<a>/<b> sub-tasks done)
Git: <N> plan commits on <branch> since <baseline short-hash>; tree <clean | DIRTY — possible crash>   <- omit if Git: no
Needs you:
- phase-NNNN sub-task N: <the exact question / error>   <- omit section if none
Warnings:   <- omit section if none
- <inconsistency found: two [ongoing] items / master–phase mismatch / phase done but no commit / foreign commits after baseline / dirty tree>
```

## Step 3 — point, don't fix

End with exactly one recommendation line:

- Warnings or a dirty tree, or the user wants to continue → suggest `master-plan-resume` (it verifies and repairs before continuing).
- Plan status `done` → say the plan finished; the scratch folder may be deleted via `master-plan-clear` or reused for a new plan.
- User sounds like they want out → mention `master-plan-clear`, noting it will warn before anything destructive.
