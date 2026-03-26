# /project:create-spec

Use the `spec-writer` agent.

Read the Memory Bank first (see CLAUDE.md Step 0).

Then generate a spec file at `specs/{feature-name}/spec.md` following the exact
format defined in `.claude/rules/spec-format.md`.

The feature name should be kebab-case derived from the task description.

After creating the file, display its full contents and wait for flobell to
write "approved" before proceeding to any next step.
