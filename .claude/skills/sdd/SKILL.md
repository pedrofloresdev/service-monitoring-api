---
name: sdd
description: Use whenever working with Spec-Driven Development in this project (docs/constitution.md or anything under specs/) - writing, reviewing or changing specs, plans and tasks, or implementing and validating spec tasks.
---

# Spec-Driven Development (SDD)

## Flow
Constitution -> Spec -> Clarify -> Plan -> Tasks -> Implement -> Validate -> Change.
- Never move to the next phase without explicit user approval.
- If it is not in the spec, do not implement it. If a decision is missing, stop and ask.
- A requirement change goes first to the spec, then plan and tasks, last to the code.
- Each spec lives in `specs/NNN-name/` with `spec.md`, `plan.md`, `tasks.md`.
- At the end of each phase, update `MEMORY.md`.

## Spec template
Header: `Status: draft | approved | implemented`. Sections: Context and goal, Users, User stories, Definitions (only if ambiguous), Functional requirements, Non-functional requirements, Edge cases, Out of scope, Completion criteria, Open questions.
WHAT and WHY only. No stack, architecture or file names.

## Requirements in EARS
- RF-x: WHEN <event>, THE SYSTEM SHALL <response>.
- RF-x: IF <unwanted condition>, THEN THE SYSTEM SHALL <response>.
- RF-x: WHILE <state>, THE SYSTEM SHALL <response>.
- RF-x: THE SYSTEM SHALL <permanent behavior>.
Every RF must be verifiable. No "fast", "easy", "smooth" without a measurable criterion.

## Plan (plan.md)
Files and responsibilities, pure logic functions, data/config/schema changes, algorithms, UI behavior, decisions with the discarded alternative, test strategy (automated vs manual check). Map each part to the RFs it covers. Sensitive areas for this project (DB schema and Alembic migrations, scheduler lifecycle, Docker/production config, new dependencies) need explicit approval.

## Tasks (tasks.md)
`- [ ] **Tn. Description.** RF-x, RF-y` then `- Done when: <verifiable check>`. 20-30 minutes max each, dependency order. More than 10 tasks -> propose splitting the spec.

## Implementation
One task at a time. Logic: tests first (red), code, green. UI or interactive behavior: implement, then verify manually with a concrete check. Mark the task done, stop.
