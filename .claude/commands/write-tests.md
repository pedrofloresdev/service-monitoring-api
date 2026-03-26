# /project:write-tests

Use the `test-writer` agent.

Requires: implementation for the feature must exist in `src/`.

Read the Memory Bank first (see CLAUDE.md Step 0).
Read spec.md to extract every acceptance criterion — each one becomes at least one test.
Read the implemented code to understand function signatures and structure.

Follow all rules in `.claude/rules/testing.md`.

After writing tests, run them with `pytest` and show the output.
If any test fails, fix the test (or flag if the implementation is wrong) before finishing.
