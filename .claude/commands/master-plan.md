---
description: Draft a persistent on-disk master plan for a multi-phase task, get approval, then execute it (git commit per phase)
---
Invoke the `master-plan` skill and follow it exactly, with this as the task to plan: $ARGUMENTS

If `.tmp-agent-scratch/MASTER-PLAN.md` already exists with a Plan status other than done, do NOT overwrite it — switch to `/master-plan-resume` (or `/master-plan-clear` if the user wants to drop it). Never start executing before the user approves the plan AND authorizes execution.
