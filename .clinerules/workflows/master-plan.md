# /master-plan — explicit shortcut to the master-plan skill

This workflow is a shortcut that explicitly triggers the `master-plan` skill (persistent on-disk plan: draft → user approval → autonomous execution with a git commit per phase).

1. Read `.cline/skills/master-plan/SKILL.md` in full.
2. Follow its instructions exactly, treating everything the user wrote after the workflow invocation as the task to plan.
3. If `.tmp-agent-scratch/MASTER-PLAN.md` already exists with a Plan status other than done, do NOT overwrite it — switch to `/master-plan-resume.md` (or `/master-plan-clear.md` if the user wants to drop it).
4. Respect both approval gates: never start executing before the user approves the plan AND authorizes execution.
