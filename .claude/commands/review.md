# /project:review

Use the `reviewer` agent.

Read the Memory Bank first (see CLAUDE.md Step 0).
Read spec.md, the implementation in `src/`, and the tests in `tests/`.

Produce a review report with these sections:

## Spec compliance
For each acceptance criterion in spec.md: PASS / FAIL / PARTIAL with explanation.

## Rejection criteria check
For each rejection criterion in spec.md: confirm none are triggered.

## Code quality
Flag any violations of `.claude/rules/code-style.md`.

## Test coverage
Flag any acceptance criteria not covered by a test.

## Verdict
APPROVED — ready to merge
NEEDS CHANGES — list specific required changes before approval

Do not approve if any acceptance criterion is FAIL or any rejection criterion is triggered.
