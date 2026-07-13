# /master-plan-clear — explicit shortcut to the master-plan-clear skill

This workflow is a shortcut that explicitly triggers the `master-plan-clear` skill (abandon the current master plan; optionally revert its commits).

1. Read `.cline/skills/master-plan-clear/SKILL.md` in full (it will direct you to the shared contract in the master-plan skill).
2. Follow its instructions exactly: show what is being abandoned, then gate the two destructive decisions separately — (A) revert the plan's git commits, (B) delete the plan files — each with a plain warning and a default of NO.
3. Deleting the plan is unrecoverable, and reverting discards real code changes — never proceed on anything less than an explicit yes.
