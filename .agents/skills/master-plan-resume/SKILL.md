---
name: master-plan-resume
description: >-
  Resume an unfinished master plan in a new session, after a crash, or after
  a context compaction: verify anything marked [ongoing], use git (dirty
  tree, commit trail vs plan logs) to detect where the previous session
  actually died, repair status/log drift, then re-enter the master-plan
  execution loop. Use when .tmp-agent-scratch/MASTER-PLAN.md exists with a
  Plan status other than done, when the user says "resume" or "continue the
  plan", or when plan state looks inconsistent after a compaction.
---

# MASTER PLAN — resume / crash recovery

Another session (possibly under a different harness, possibly your pre-compaction self) left this plan behind and may have died mid-write: statuses, logs, and commits can each lag reality. Establish the true state, repair the bookkeeping, continue. Never redo verified work; never trust unverified work.

**Step 0 — contract.** Read sections 1–3 of the `master-plan` skill (sibling folder `../master-plan/SKILL.md`). No `MASTER-PLAN.md` → nothing to resume, suggest `master-plan`. Status `done` → say so and stop. Subagents: this skill is not for you.

**Step 1 — should you resume?**
- User asked to continue, or their request matches the Goal → resume without asking.
- `blocked-on-human` and the user's message answers the pending questions → record the answers in the affected Logs, flip those items to `[todo]`, status `executing`, continue.
- `drafting`/`approved` (execution never started) → no recovery needed; re-enter master-plan at its approval gates.
- Unrelated request → surface the plan (Goal + progress counts) and ask: resume, set aside, or abandon (`master-plan-clear`). Never silently delete.

**Step 2 — ground truth from git** (skip if `Git: no`).
- `git status --porcelain`: a dirty tree is crash evidence — phases commit on close, so uncommitted changes belong to the `[ongoing]` item. Read the diff to see how far the work actually got.
- `git log --oneline <baseline>..HEAD` vs the `commit <hash>` Log lines: a phase-NNNN plan commit with the phase still `ongoing` means the crash fell between commit and status write — verify the phase check, flip to `done`, log `reconciled from commit <short-hash> on resume`. A `done` phase with no matching commit gets its "done when" re-verified before you trust it. Note any foreign commits in the master Log — they make the final squash unsafe and the user must hear about them at acceptance.
- Recovery only reads git and commits verified work — resetting/reverting belongs to `master-plan-clear`.

**Step 3 — verify and repair the files.**
- Trust nothing `[ongoing]`: run its "done when"; passes → `[done]`, log `verified on resume`; fails or half-done → redo it from the start.
- Fix invariant violations (two `[ongoing]` items; master/phase mismatch — phase file wins; missing `— reason:` suffixes).
- If the dirty tree turned out to be a fully complete phase, close it properly: status, mirror, Log, phase commit.
- Append `resumed` to the master Log.

**Step 4 — continue** at the execution loop (master-plan §6); all its discipline applies from here, through acceptance and squash.
