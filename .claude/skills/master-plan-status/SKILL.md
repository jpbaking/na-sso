---
name: master-plan-status
description: >-
  READ-ONLY progress report on the current master plan: phase/sub-task
  counts, pending needs-human questions, git commit trail versus baseline,
  and any bookkeeping inconsistencies — without changing a single file or
  running the plan. Use when the user asks "where are we", "plan status",
  or "what's left". To continue work use master-plan-resume; to abandon use
  master-plan-clear.
---

# MASTER PLAN — status (read-only)

Report the true state of the plan. Nothing is written: no status flips, no log lines, no commits, no repairs — problems are reported with a pointer to the skill that fixes them.

Read sections 1–3 of the `master-plan` skill (sibling folder `../master-plan/SKILL.md`) for the contract. No `MASTER-PLAN.md` → say there is no plan and stop.

Read MASTER-PLAN.md and every phase file; if `Git: yes`, also `git status --porcelain` and `git log --oneline <baseline>..HEAD`. Then report:

```
Plan: <title> — <plan status>
Goal: <goal line>
Phases: <X> done, <Y> skipped, <Z> needs-human, <W> todo, <V> ongoing
Per phase:
- phase-NNNN <status> — <title> (<a>/<b> sub-tasks done)
Git: <N> plan commits on <branch> since <baseline short-hash>; tree <clean | DIRTY — possible crash>   (omit if Git: no)
Needs you:
- phase-NNNN sub-task N: <exact question / error>   (omit if none)
Warnings:   (omit if none)
- <two [ongoing] items / master–phase mismatch / done phase without commit / foreign commits after baseline / dirty tree>
```

End with one recommendation: warnings or a dirty tree, or the user wants to continue → `master-plan-resume` (verifies and repairs first); status `done` → the plan finished, the scratch folder can be cleared or replaced; the user sounds done with it → `master-plan-clear` (which warns before anything destructive).
