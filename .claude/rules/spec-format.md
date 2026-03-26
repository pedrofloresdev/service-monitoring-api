# Spec format rules

Every spec file must follow this exact structure. Do not skip sections.
Do not rename sections. Do not add extra top-level sections without approval.

---

```markdown
# Spec: {Feature Name}

**Status:** DRAFT | APPROVED | REJECTED  
**Created:** YYYY-MM-DD  
**Author:** flobell  
**Feature ID:** FEAT-{NNN}

---

## Objective
One or two sentences. What is this feature trying to achieve, and why does it matter?

## Description
A clear, detailed explanation of the feature. Write it so that someone unfamiliar
with the codebase can understand what it does and how it fits into the project.
No code here — only human-readable explanation.

## Acceptance criteria
Each criterion must be testable. Write them as: "Given X, when Y, then Z."

- [ ] AC-001: Given ..., when ..., then ...
- [ ] AC-002: Given ..., when ..., then ...

## Rejection criteria
Conditions that must NEVER be true. These become negative tests.

- [ ] RC-001: The system must NOT ... if ...
- [ ] RC-002: The feature must NEVER ...

## Technical constraints
Hard limits that the implementation must respect.

- Language / runtime version requirements
- Libraries that must or must not be used
- Performance requirements (e.g. response under 200ms)
- Security requirements

## Considerations
Things to keep in mind that don't fit elsewhere: edge cases, known risks,
dependencies on other features, questions that need answering.

## Out of scope
Explicitly list what this spec does NOT cover, to prevent scope creep.

## Open questions
List any unresolved questions before approval. This section should be empty
before the spec is marked APPROVED.
```

---

## Rules for writing specs

- Write acceptance criteria first — before descriptions. This forces clarity.
- Every criterion must have a unique ID (AC-001, AC-002...).
- "The system should..." is not a criterion. "Given a valid email, when the user registers, then the system creates an account and returns 201" is.
- Rejection criteria are mandatory. If you can't think of any, think harder.
- Mark status as DRAFT until flobell writes "approved".
