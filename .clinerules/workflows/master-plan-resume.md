# /master-plan-resume — explicit shortcut to the master-plan-resume skill

This workflow is a shortcut that explicitly triggers the `master-plan-resume` skill (continue an unfinished master plan; crash and compaction recovery).

1. Read `.cline/skills/master-plan-resume/SKILL.md` in full (it will direct you to the shared contract in the master-plan skill).
2. Follow its instructions exactly: verify anything marked [ongoing], use the git state (dirty tree, commit trail) to find where the previous session actually stopped, repair the bookkeeping, then re-enter the execution loop.
3. Never revert or discard work during resume — that belongs to `/master-plan-clear.md`.
