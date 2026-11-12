---
description: SDD - implement exactly one task. Usage /sdd-implement 001-name T1
argument-hint: <NNN-name> <task id>
---
Implement ONLY task $2 from specs/$1/tasks.md, following specs/$1/plan.md, docs/constitution.md and the sdd skill.

1. Write the tests first and confirm they fail.
2. Write the code until they pass.
3. Run the full test suite, lint and type checks, and show me the results.
4. Mark $2 as done in tasks.md and state which RF it covers.

Ask before adding dependencies or changing schema/save data. Then STOP. Do not start the next task.
