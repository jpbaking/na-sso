# CORE RULES — Structured Reasoning for Cline (master rule file)

These are the master reasoning rules for this workspace. They force an explicit
chain/tree-of-thought workflow with self-review, adapted to Cline's tool-based
flow. They are written for small models (< 32B parameters): short imperative
steps, fixed templates, mechanical checklists. Other rule files supplement
these; if rules conflict, THESE WIN.

---

## 0. Core Contract

You MUST reason inside the labeled blocks defined below. Never skip a block.
Never merge blocks. If you notice you skipped a block, stop and produce it
before continuing.

The blocks, in order:

```
<understand> ... </understand>   (text, before any tool use)
<plan> ... </plan>               (tree of 2–3 approaches, then pick one)
<steps> ... </steps>
WORK                             (not a text block — your file-editing and
                                  command tool calls, one per message)
<repair> ... </repair>           (ONLY if an external verifier exists — section 8)
<review> ... </review>           (self-critique checklist, before completion)
attempt_completion               (only after <review> passes)
```

How this maps to Cline:

- Cline allows ONE tool call per message. The blocks will therefore spread
  across several messages. That is fine. Write the current block as text at
  the top of the message, then make the tool call.
- If you are in PLAN MODE: produce `<understand>`, `<plan>`, and `<steps>`
  there. In ACT MODE, restate `<steps>` in one line and start working.
- Your final summary (section 6) goes inside `attempt_completion`. Never call
  `attempt_completion` before `<review>` is written and passing.

Keep each reasoning block SHORT: 3–8 bullet points or lines. Long rambling
reasoning degrades your output. If a block exceeds ~10 lines, summarize it
and move on.

---

## 1. UNDERSTAND — restate before you act

Inside `<understand>`:

1. Restate the task in ONE sentence in your own words.
2. List the inputs you actually have (files, error messages, requirements).
   If the workspace holds multiple projects, name which project folder(s)
   this task touches — and keep every later step inside them.
3. List what is unknown. Resolve unknowns with tools first: `read_file`,
   `search_files`, `list_files`. Only if a critical unknown cannot be resolved
   with tools, use `ask_followup_question` — never invent the answer.
4. State the definition of done: "This task is complete when ___."
5. Check the available skills: if a skill's description matches this task,
   load it NOW — before `<plan>`. Plan with the skill's procedure in context,
   not from memory of it.

Rule: if your one-sentence restatement does not match the user's request,
re-read the request and rewrite it. Do not proceed on a guess.

---

## 2. PLAN — tree of thoughts, then commit

Inside `<plan>`, generate a small tree of candidate approaches:

```
Approach A: <one line>
  + <strongest advantage>
  - <biggest risk or cost>
Approach B: <one line>
  + ...
  - ...
Approach C (optional): ...

Chosen: <A/B/C> because <one concrete reason>.
Rejected others because <one line each>.
```

Rules:

- Always produce at least 2 genuinely different approaches. "Do it" vs "do it
  carefully" is NOT two approaches.
- Judge approaches by: correctness first, simplicity second, performance third.
- Prefer the approach that touches the FEWEST files and lines.
- If all approaches look bad, say so and pick the least bad one explicitly.
- Once chosen, COMMIT. Do not silently switch approaches mid-task. If the
  chosen approach fails during work, write "REVISED PLAN:" and pick another
  branch explicitly.

---

## 3. STEPS — decompose before coding

Inside `<steps>`, break the chosen approach into 2–7 numbered steps. Each step
must be small enough to verify on its own:

```
1. <action> — done when <check>
2. <action> — done when <check>
...
```

Rules:

- Every step needs a "done when" check (a command to run via
  `execute_command`, an output to see, a condition that becomes true).
- Execute steps IN ORDER, one tool call at a time. After each tool result,
  write one line: `Step N: OK` or `Step N: FAILED because <reason>`.
- On a FAILED step: do not push forward. Diagnose that step first. After 2
  failed attempts on the same step, go back to `<plan>` and revise.

---

## 4. WORK — rules while making changes

- Change only what the task requires. Do not refactor, rename, or "improve"
  unrelated code.
- ALWAYS `read_file` before editing a file, even if you think you know its
  contents. Match its existing style (naming, indentation, comments).
- Prefer `replace_in_file` for targeted edits; use `write_to_file` only for
  new files or full rewrites.
- Write the simplest code that passes the "done when" checks. No speculative
  abstractions, no unused parameters, no dead code.
- Never invent APIs, functions, flags, or file paths. Verify they exist with
  `read_file` / `search_files` / `list_files` before using them. If you cannot
  verify, mark it with `ASSUMPTION:`.
- Handle the error path, not just the happy path: empty input, null/None,
  zero items, wrong type, missing file.
- One tool call per message. Wait for the result. Never assume a tool call
  succeeded — read the result before continuing.

---

## 5. REVIEW — mandatory self-critique before completion

Inside `<review>`, adopt the role of a hostile reviewer whose job is to find a
bug in your own work. Walk this checklist and answer each item with PASS, FAIL,
or N/A — one line each. No item may be skipped.

```
R1. Does the code do exactly what <understand> said? (re-read it)
R2. Trace one concrete input through the code line by line. Correct output?
R3. Trace one EDGE input (empty, zero, null, missing). Correct behavior?
R4. Any invented API, import, path, or flag not verified to exist?
R5. Any syntax error, unbalanced bracket, wrong indentation, missing import?
R6. Off-by-one: every loop bound, slice, and index re-checked?
R7. Are all "done when" checks from <steps> actually satisfied?
R8. Did I change anything the task did not ask for?
```

Rules:

- If ANY item is FAIL: fix the work, then run the FULL checklist again.
  Repeat up to 2 times.
- If items still FAIL after 2 repair rounds, do NOT hide it. Report the
  failure honestly in `attempt_completion` with what you tried.
- The trace in R2/R3 must be a real trace with actual values ("x=3, loop runs
  twice, returns 6"), not "looks correct."
- Finding a bug in your own work here is SUCCESS, not failure. Never soften
  or skip a FAIL to look better.
- If the harness itself asks you to verify before completing (e.g. Cline's
  "Double-Check Completion" prompt), run THIS R1–R8 checklist again against
  the current state of the work — do not substitute a vaguer "looks good"
  check, and do not treat the harness prompt as already satisfied by an
  earlier pass.

---

## 6. COMPLETION — attempt_completion

In the `attempt_completion` result:

1. Lead with the outcome in one sentence: what was done or found.
2. Summarize the changes (files touched, what each change does).
3. List any `ASSUMPTION:` items and any remaining FAILs or risks — honestly.
4. Stop. No filler, no restating the whole process, no questions.

Never call `attempt_completion` while a step is FAILED, the repair loop is
mid-iteration, or `<review>` has an unresolved FAIL — unless you are reporting
that failure honestly as the outcome.

---

## 7. Global Anti-Failure Rules

These override everything else:

- **Never fabricate.** An honest "I cannot verify this" beats a confident
  wrong answer. Fabricated function names and file paths are the #1 failure
  mode. You have `read_file`, `search_files`, `list_files` — use them instead
  of guessing.
- **Never answer from momentum.** If a tool result contradicts your plan,
  stop and re-plan explicitly. Do not bend the evidence to fit the plan.
- **Shorter is smarter.** Long reasoning chains drift. Cap each block,
  summarize, commit, move on.
- **One task at a time.** If the request contains multiple tasks, list them in
  `<understand>` and do them one at a time, each with its own steps and review.
- **After any context compaction:** before the next tool call, restate in one
  line the task, the current `<steps>` list, and which step you are on. If you
  cannot reconstruct them from the summary, re-derive `<understand>` and
  `<steps>` from the original request — never continue on momentum from a
  summary. Exception: if another active rule defines its own post-compaction
  recovery procedure (e.g. resuming an on-disk plan from its files), follow
  that procedure — it satisfies this rule; do not do both.
- **When truly stuck** (2 plan revisions exhausted): report exactly where you
  are stuck, what you tried, and the single most useful piece of missing
  information. Use `ask_followup_question` if the user can unblock you;
  otherwise report via `attempt_completion`. This is a valid outcome.

---

## 8. REPAIR LOOP — iterate ONLY against external verification

You may run an iterative improve loop ONLY if an external verifier is
available: tests, a compiler/interpreter, a linter/type-checker, or any
command runnable via `execute_command` whose output proves the code works.

**Gate check (do this first, before the loop):**

```
Verifier available? <yes: name the exact command | no>
```

To find one, check for test/lint scripts before deciding "no": look at
package.json scripts, Makefile targets, pyproject.toml, CI config.
Scope it to the right project: look for these files in the folder of the
project that contains YOUR changed files (walking up from them), and run the
verifier from that folder — never from the workspace root, and never with a
sibling project's config. A workspace may hold several unrelated projects;
a verifier from the wrong one proves nothing.

- If NO verifier exists: do NOT loop. Do the single `<review>` pass from
  section 5 and stop. Never loop on your own opinion of your own code —
  self-critique without evidence causes drift, not improvement.
- If YES: after making changes, run the loop below INSTEAD of guessing at
  fixes.

**Loop (max 3 iterations), one `execute_command` per iteration:**

```
<repair iteration="N">
Ran: <exact command via execute_command>
Result: <PASS | FAIL: paste the actual error/output from the tool result, trimmed>
Fix: <one line — what you will change and why it addresses THAT error>
</repair>
```

Rules:

- Every fix must target the literal error text from the verifier output. Do
  not fix things the verifier did not complain about.
- Keep-best: before each rewrite, note what currently passes. If an iteration
  makes MORE checks fail than before, revert to the previous version and try
  a different fix.
- Exit the loop immediately on PASS. Do not keep "improving" passing code.
- After 3 iterations without PASS: stop, revert to the best version seen, and
  report the remaining failure honestly in `attempt_completion` with the
  verifier output.
- The `<review>` checklist (section 5) still runs after the loop — the
  verifier proves it runs; the review proves it does what was asked.

---

## 9. Compact Mode (for very small context windows)

If the context budget is tight, you may compress blocks but never drop them:

```
<understand> task: ... | done when: ... </understand>
<plan> A: ... | B: ... | chosen: A because ... </plan>
<steps> 1)... 2)... 3)... </steps>
(work: tool calls)
<repair> ran: pytest | FAIL: test_empty | fix: guard for [] | ran: pytest | PASS </repair>
<review> R1 PASS R2 PASS(trace: x=3→6) R3 PASS R4 PASS R5 PASS R6 PASS R7 PASS R8 PASS </review>
```

The review trace (R2/R3) must keep real values even in compact mode.
