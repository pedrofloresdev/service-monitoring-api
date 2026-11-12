---
description: SDD - new requirement: spec first, then code. Usage /sdd-change 001-name requirement text
argument-hint: <NNN-name> <change>
---
New requirement for specs/$1/ (text after the folder name): $ARGUMENTS

Do NOT touch code. Use the sdd skill.
1. Update specs/$1/spec.md: new or changed RF in EARS, its edge cases and what stays out of scope.
2. Say which parts of plan.md and tasks.md would need to change afterwards.
3. Show me the diff of the spec and wait for my approval.
