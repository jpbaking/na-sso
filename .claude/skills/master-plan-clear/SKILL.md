---
name: master-plan-clear
description: >-
  Abandon the current master plan: optionally revert the git commits made
  during its execution (with explicit warnings — reverting discards code
  changes), then permanently delete .tmp-agent-scratch/ (unrecoverable).
  Run ONLY when the user explicitly asks to abandon, cancel, reset, scrap,
  or clear the plan — never on your own judgment and never as a shortcut
  around a blocked or messy plan.
---

# MASTER PLAN — clear / abandon

Two independent destructive decisions, each user-gated with a plain warning and a default of NO — anything short of a clear yes means do nothing for that decision.

**Step 0.** Read sections 1–3 of the `master-plan` skill (sibling folder `../master-plan/SKILL.md`). No `MASTER-PLAN.md` → nothing to clear, stop. Show the user what they are abandoning: the Goal, progress counts, and any pending needs-human questions (those are lost too).

**Step A — revert the plan's commits?** Skip (and say so) if `Git: no`, `Baseline commit:` is `-`, or `git log <baseline>..HEAD` is empty.
1. Show `git log --oneline <baseline>..HEAD`.
2. Warn before asking: reverting permanently discards ALL code changes the plan made — the work itself, not bookkeeping. If the tree is dirty, warn that uncommitted changes die too.
3. Foreign commits in the range (anything not a recorded plan commit) make a hard reset destroy someone else's work — REFUSE it; offer `git revert` of the plan commits individually, or leaving git untouched.
4. Ask: "Revert to baseline `<short-hash>`, discarding these <N> commits?" On yes (no foreign commits): `git reset --hard <baseline>`; confirm what was discarded. On no: every commit stays.
5. Never touch commits at or before the baseline, never push, never switch branches.

**Step B — delete the plan files?**
1. Warn: deletion is NOT recoverable — the plan, phase files, logs, and pending questions are permanently gone. If Step A was declined/skipped, also state that the code changes stay; only bookkeeping is deleted.
2. Ask for explicit confirmation. On yes: delete `.tmp-agent-scratch/` entirely; confirm it is gone and restate whether project files/commits were touched (per Step A). On no: change nothing — a kept plan can still be resumed via `master-plan-resume`.
