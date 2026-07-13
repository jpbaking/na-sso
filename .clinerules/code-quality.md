# Code Quality — maintainable, unit-testable code

Language-agnostic rules for every piece of code you write or change, in any
language. They all serve ONE outcome: **code a unit test can construct, run,
and assert on without real I/O.** These rules supplement the core reasoning
rules; if rules conflict, the core rules win.

**Scope guard:** apply these to the code YOU are writing or editing in this
task. Never refactor untouched code just to satisfy a limit here. If an
existing function you must edit already violates a limit, improve only the
part your change touches, and mention the larger cleanup in your completion
summary instead of doing it unasked.

---

## 1. Keep every unit small and flat (low cyclomatic complexity)

Hard limits for any function/method you write or grow:

- **≤ 40 lines** of code (blank lines and comments don't count).
- **≤ 4 parameters.** More → bundle the related ones into one
  object/struct/record parameter.
- **≤ 2 levels of nesting** inside the body.
- **≤ 8 branch points** (each `if` / `else if` / `case` / loop / `catch` /
  boolean operator inside a condition counts as one). This approximates a
  cyclomatic complexity of ~10 or less.

When a limit would be crossed, apply the FIRST fix on this list that fits:

1. **Guard clauses:** handle invalid/edge cases first and return early, so
   the happy path stays unindented.
2. **Extract a helper:** pull a coherent block into its own named function.
   One function = one job — if an honest name for it needs the word "and",
   split it further.
3. **Dispatch table:** replace a long `if/else if` or `switch` chain that
   maps a value to behavior with a lookup map of value → function.
4. **Split the loop:** a loop that filters AND transforms AND accumulates
   becomes separate steps (or pipeline/stream operations if the language
   has them).

## 2. One file = one topic (modularity)

- Before adding a function or class to a file, state that file's purpose in
  one sentence without the word "and". If the new code does not fit that
  sentence, it goes in a different — possibly new — file. Never append code
  to a file just because the file was already open.
- A file you touch that exceeds **~400 lines** is a split candidate: say so
  in your completion summary. Never push a file past that limit yourself by
  adding unrelated code to it.
- Group files by feature/domain (`billing/`, `auth/`), not by kind
  (`utils/`, `helpers/`, `managers/`). A grab-bag `utils` file is where
  unrelated functions go to hide; add to one only when the function truly
  has no feature home, and keep it dependency-free.

## 3. Inject dependencies — never construct them inside logic

A unit is testable only if a test can hand it fakes. Therefore:

- Inside business logic, NEVER directly construct or reach for anything that
  does I/O or is global: HTTP/network clients, database handles, the file
  system, the clock (`now()`), random, environment variables, singletons.
- Receive collaborators as constructor arguments or function parameters
  instead. The signature of a unit must reveal everything it talks to.
- Construct concrete objects only at the EDGE: the program's entry point
  (main / request handler / composition root) or a small **factory** — an
  ordinary function that builds and wires objects. Use a factory when
  construction is conditional or repeated. Default implementations live
  there, never inside the logic.
- Time and randomness are dependencies too: pass `now` / `random` (or a
  clock/rng object) in; never call the global ones inside logic you want
  to test.

Mechanical smell test: a constructor call or global lookup inside a function
that also branches on data means the function mixes wiring with logic —
split it into a factory (wiring) and a unit that receives the result (logic).

## 4. Depend on contracts, not concrete classes

- Type parameters and fields as the NARROWEST contract the unit needs — an
  interface, protocol, trait, function type, or duck-typed shape, whatever
  the language offers — not as a concrete class. If the unit only calls
  `save(x)`, depend on a one-method contract, not on `PostgresRepository`.
- Prefer interfaces (pure contracts) over abstract base classes that carry
  behavior. Inherited behavior couples you to a hierarchy a test must fight.
  If two classes need the same code, extract it into its own unit and inject
  or compose it — composition over inheritance.
- Accept general, return specific: take the loosest input type that works
  (iterable, sequence, reader); return the most informative concrete result.
- Do NOT invent an interface for something with one implementation and no
  I/O — a pure, easily constructed concrete class needs no contract in front
  of it. The rule is "depend on the narrowest thing that exists", not "wrap
  everything".

## 5. Separate decisions from side effects

- **Pure core, thin shell:** put decisions (validation, calculation,
  branching, transformation) in pure functions — input in, result out, no
  I/O, no external mutation. Keep I/O in thin outer functions that call the
  pure core. Tests then cover the core directly; the shell stays too simple
  to break.
- Return new values instead of mutating arguments or module/global state.
- A function either returns an answer or performs an effect — never hide an
  effect inside something that looks like a query.

## 6. Testability check — run before finishing

For every function/class you added or changed, answer each line with PASS,
FAIL, or N/A — one line each. (If the core rules are installed, run this
alongside their REVIEW checklist.)

```
T1. Can a test construct this unit with fakes only — no real network, disk,
    DB, clock, sleep, or env?
T2. Does the signature show every collaborator (nothing pulled from a
    global or singleton inside the body)?
T3. Is every injected dependency typed as a contract narrow enough to fake
    in a few lines?
T4. Do the unit limits hold (≤40 lines, ≤4 params, ≤2 nesting, ≤8 branches)?
T5. Is the decision logic reachable without triggering any side effect?
T6. Does the file this code landed in still pass the one-sentence test?
```

Any FAIL → restructure using sections 1–5 (usually: extract the pure part,
inject the impure part), then re-run the checklist. If fixing a FAIL would
require refactoring code outside this task's scope, keep your change as
close to passing as the scope allows and report the FAIL honestly in your
completion summary.
