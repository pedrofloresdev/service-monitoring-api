# /project:create-tasks

Use the `task-manager` agent.

Requires: an approved `specs/{feature-name}/plan.md` must exist.

Read the Memory Bank first (see CLAUDE.md Step 0).
Read the approved spec and plan completely before generating tasks.

Generate a tasks file at `specs/{feature-name}/tasks.md`.

Each task must be:
- Atomic (completable in one focused session)
- Mapped to at least one acceptance criterion from spec.md
- Ordered by dependency (no task should require another unfinished task)
- Tagged with an ID: TASK-001, TASK-002, etc.

Display the tasks list and wait for flobell's confirmation before implementing.
