# CLAUDE.md — Weather Project

Behavioral guidelines for coding tasks. 
Bias toward caution over speed.

---

## Before You Start

**Clarify before acting. Weak criteria waste everyone's time.**

- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them — don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

Transform the task into a verifiable goal before touching code:
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Add validation" → "Write tests for invalid inputs, then make them pass"

---

## Scope

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No refactoring of adjacent code, even if you'd do it differently.
- No error handling for scenarios not covered by the request.
- Match existing style. Don't clean up what you didn't break.
- If you notice unrelated issues, mention them — don't fix them.

Every changed line must trace directly to the request.

---

## Execution — Bug Fixes

**Narrow scope. Exhaustive execution within it.**

1. **Read ALL failing tests first.** Before touching source code, read 
   the relevant test files completely. Run the full test suite and 
   capture full output — note every failing test case, not just the 
   first one. Group failures by type to understand the full scope.

2. **Find the root cause.** Trace each failure to the specific line(s) 
   responsible. Read the source code — not just the test file. If 
   multiple test cases fail, check whether they share a single root 
   cause or require separate fixes. Check git log or comments if 
   logic is unclear.

3. **Fix the root cause, not the symptom.** Make the minimal change 
   that makes failing tests pass without breaking existing tests. 
   No workarounds or special-case patches if the underlying logic 
   is wrong. If the same logical error appears in multiple places 
   in the source, fix all of them.

4. **Handle edge cases within scope.** If the failing tests involve 
   edge cases (empty strings, null/undefined, special characters, 
   numeric boundaries, encoding, option flags), make sure your fix 
   handles all of them — not just the obvious case.

5. **Verify all tests pass.** After editing, run the full test suite. 
   If previously failing tests still fail, do not stop — re-read 
   those specific failing test cases, understand precisely what they 
   expect, and revise your fix. Keep iterating until every 
   originally-failing test passes and no regressions are introduced.

6. **Persist through partial fixes.** If your fix makes some but not 
   all tests pass, treat that as an incomplete fix. Re-read the 
   remaining failures, check if there is a second location in the 
   source that needs the same or a related fix, and continue. 
   Partial progress is not success.

Keep changes minimal and correct. Do not refactor unrelated code 
or add new tests unless explicitly required.

---

## Success Criteria

These guidelines are working if:
- Clarifying questions come before implementation, not after mistakes
- Diffs contain no unnecessary changes
- No rewrites due to overcomplication
- Every originally-failing test passes with no regressions
- Every changed line traces to the request
