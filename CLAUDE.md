# CLAUDE.md — flobell/sdd-template-base

## Identity
This is a Spec-Driven Development (SDD) project owned by **flobell**.
Every agent interaction starts here. Read this file completely before doing anything else.

---

## Step 0 — Always read the Memory Bank first

Before planning, writing, reviewing, or modifying anything, load the Memory Bank in this exact order:

1. `memory-bank/project-brief.md` — what this project is and why it exists
2. `memory-bank/architecture.md` — technical decisions already made
3. `memory-bank/conventions.md` — coding patterns established in this codebase
4. `memory-bank/progress.md` — what is done, what is in progress, what is pending

If any of these files does not exist yet, create it with empty sections before continuing.

---

## The SDD Workflow

Never write code without an approved spec. Follow these stages in order:

```
Task/Idea
   ↓
[/project:create-spec]   → specs/{feature}/spec.md       → flobell reviews & approves
   ↓
[/project:create-plan]   → specs/{feature}/plan.md       → flobell reviews & approves
   ↓
[/project:create-tasks]  → specs/{feature}/tasks.md      → auto-generated from plan
   ↓
[/project:implement]     → src/ code, one task at a time
   ↓
[/project:write-tests]   → tests/ aligned to spec criteria
   ↓
[/project:review]        → validates code against spec.md
```

**Hard rules:**
- Never skip or merge stages without explicit approval from flobell.
- If a task is ambiguous, stop and ask. Do not assume.
- If the spec and the code conflict, the spec wins. Flag it, do not silently fix it.

---

## Agents

Each agent lives in `.claude/agents/`. Use the correct agent for each stage:

| Stage | Agent file |
|---|---|
| Write spec | `spec-writer.md` |
| Write plan | `planner.md` |
| Break into tasks | `task-manager.md` |
| Implement code | `developer.md` |
| Write tests | `test-writer.md` |
| Review & validate | `reviewer.md` |

---

## Rules

All rules in `.claude/rules/` apply at all times:

- `code-style.md` — Python conventions for this project
- `testing.md` — pytest conventions and coverage requirements
- `spec-format.md` — required structure for every spec file

---

## Approval Gates

flobell must explicitly write **"approved"** or **"APPROVED"** before moving to the next stage.
A response like "looks good" or "ok" is NOT an approval. Ask for confirmation.

---

## Memory Bank Updates

After completing any stage, update `memory-bank/progress.md` with:
- What was completed
- Any decisions made that differ from the original plan
- Open questions or blockers

If a new architectural decision was made, also update `memory-bank/architecture.md`.

## Project-specific overrides
- Source code lives in `app/` not `src/`
- Tests live in `tests/` (create this folder if it doesn't exist)
- App entry point: `app/main.py`