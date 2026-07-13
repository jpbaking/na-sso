# Master Plan family

Multi-phase tasks get a plan that lives on disk and survives crashes and compactions: `<project root>/.tmp-agent-scratch/` holding `MASTER-PLAN.md` plus `phase-NNNN.md` files, with a git commit after every completed phase. Procedures live in four skills — load exactly ONE:

| Situation | Load |
|---|---|
| New big task, no unfinished plan on disk | **master-plan** — draft, get approval, execute |
| Unfinished plan exists / "resume" / "continue" / after a crash or compaction mid-plan | **master-plan-resume** — verify, repair, continue |
| "where are we" / "plan status" / "what's left" | **master-plan-status** — read-only report |
| Explicit "abandon" / "cancel" / "scrap" / "clear the plan" | **master-plan-clear** — warns, then optionally reverts commits and deletes the plan |

**Resume check — at the start of every task:** if `<project root>/.tmp-agent-scratch/MASTER-PLAN.md` exists with a `Plan status:` other than `done`, an unfinished plan exists — load **master-plan-resume** before anything else. Never delete or overwrite that folder on your own initiative; only **master-plan-clear**, at the user's explicit request, does that.

**When to start a plan:** the task needs several distinct phases, will likely outlive one session, or the user asks for a master plan / a plan that persists on disk. Ordinary in-conversation planning (including your harness's plan mode or todo list) is not a trigger — those happen with or without this skill. When genuinely unsure, prefer the plan: it costs little and a lost long task costs everything.

**Mid-plan:** after any compaction or restart, the plan files on disk win over your memory or summary of them — re-read them (via master-plan-resume) before continuing. Subagents never read, write, or resume plans; only the main session does.
