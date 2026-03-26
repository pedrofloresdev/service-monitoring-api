# /project:implement

Use the `developer` agent.

Requires: an approved `specs/{feature-name}/tasks.md` must exist.

Read the Memory Bank first (see CLAUDE.md Step 0).
Read spec.md, plan.md, and tasks.md completely before writing any code.

Implement ONE task at a time. After each task:
1. Show the code written
2. Mark the task as complete in tasks.md (change `[ ]` to `[x]`)
3. Update `memory-bank/progress.md`
4. Ask flobell: "Ready to move to TASK-00X?"

Never implement multiple tasks in one step without explicit instruction.
Follow all rules in `.claude/rules/code-style.md`.
