# Master Plan family

Multi-phase tasks get a plan that lives on disk and survives crashes: `<project root>/.tmp-agent-scratch/` holding a `MASTER-PLAN.md` plus `phase-NNNN.md` files, with a git commit after every completed phase. The procedures live in four skills — this rule only says when to load which. Load exactly ONE:

| Situation | Load |
|---|---|
| New big task, no unfinished plan on disk | **master-plan** — create the plan, get user approval, execute |
| Unfinished plan exists / "resume" / "continue" / after a crash or compaction mid-plan | **master-plan-resume** — verify, repair, continue |
| "where are we" / "plan status" / "what's left" | **master-plan-status** — read-only report |
| "abandon" / "cancel" / "scrap" / "clear the plan" (explicit only) | **master-plan-clear** — warns, then optionally reverts commits and deletes the plan |

**Resume check — at the start of EVERY task, even trivial ones, before planning anything:** if `<project root>/.tmp-agent-scratch/MASTER-PLAN.md` exists and its `Plan status:` line is not `done`, an unfinished plan exists. Load the **master-plan-resume** skill NOW and follow it before anything else. Never delete or overwrite that folder on your own initiative — that is only ever done through **master-plan-clear** at the user's explicit request.

**New-task check — load the master-plan skill when ANY of these is true:**

- The task needs 2 or more distinct phases of work, or roughly 5 or more sub-tasks.
- The task will likely span more than one session, or the user may interrupt it.
- The user says "master plan", asks for a plan that persists on disk, or names the scratch folder.

Below that size, skip it and plan in your reply as usual. When unsure, load it — a small plan costs little; a lost big task costs everything. NOT a trigger by itself: Cline's PLAN MODE, or ordinary in-reply planning — those happen with or without this skill.

**If `00-core-reasoning-rules.md` is also active** (skip this paragraph if it is not installed): load the skill during `<understand>` (its step 5). Once plan files exist, your `<steps>` block is one line pointing at MASTER-PLAN.md — never duplicate the plan in chat. The resume procedure IS the core rules' post-compaction re-anchor for a mid-plan session (their compaction exception); the plan files on disk replace re-deriving steps from memory.

**While a plan is executing:**

- The skill text lives in the conversation, so a context compaction can erase it. After ANY compaction or session restart mid-plan: load the **master-plan-resume** skill and follow it before continuing. The plan files on disk always win over your memory of them.
- Only the main session writes plan files. **If you are a read-only subagent:** ignore all of this — do not check for, read, or resume any plan; just perform your assigned research and return your findings.
- This workflow expects Cline's **Strict Plan Mode** setting OFF: plan files are written during planning, even in PLAN MODE.
