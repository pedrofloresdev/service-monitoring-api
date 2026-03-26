# SDD workflow guide

A practical reference for using this template day-to-day.

---

## Starting a new feature

### Step 1 — Describe your idea to Claude Code
Open your terminal in the project root and run:
```bash
claude
```
Then type:
```
/project:create-spec I want to add {your feature description here}
```
Claude will generate `specs/{feature-name}/spec.md` and show it to you.

### Step 2 — Review and approve the spec
Read it carefully. Check:
- Does it capture what you actually want?
- Are the acceptance criteria testable and specific?
- Are the rejection criteria real constraints?
- Is anything missing or wrong?

If you want changes, tell Claude what to fix. When satisfied, type:
```
approved
```

### Step 3 — Create the plan
```
/project:create-plan
```
Claude reads the approved spec and designs the technical approach.
Review it, ask questions, then type `approved`.

### Step 4 — Generate tasks
```
/project:create-tasks
```
Claude breaks the plan into atomic tasks. Confirm the list looks right.

### Step 5 — Implement task by task
```
/project:implement
```
Claude will ask which task to start. After each task is complete,
it will pause and wait for you to say "next" or redirect it.

### Step 6 — Write tests
```
/project:write-tests
```
Claude maps every acceptance criterion to a test and runs them.

### Step 7 — Final review
```
/project:review
```
Claude validates everything against the original spec and gives a verdict.

---

## Approval rules

| What you type | What it means |
|---|---|
| `approved` | Advance to the next stage |
| `APPROVED` | Same |
| `looks good` | NOT an approval — Claude will ask again |
| `ok` | NOT an approval — Claude will ask again |

This is intentional. Explicit approval prevents Claude from moving forward on a misunderstanding.

---

## When Claude gets something wrong

If Claude's output doesn't match your intent at any stage:
1. Do NOT approve — tell it what's wrong.
2. It will revise and show you the updated version.
3. Only approve when it's right.

If the spec was approved but the plan misinterprets it, say:
```
The plan contradicts AC-002. Revise the plan to address this.
```

---

## Updating the Memory Bank

The Memory Bank is what keeps Claude coherent across sessions. Keep it accurate:

- **After approving a spec:** Claude updates `progress.md` automatically.
- **After a key design decision:** Tell Claude: "Update architecture.md with this decision."
- **After establishing a new pattern:** Tell Claude: "Add this to conventions.md."
- **At the start of a new session:** Claude reads the Memory Bank automatically — no action needed.

---

## Common commands reference

| Command | What it does |
|---|---|
| `/project:create-spec` | Generate a spec from your description |
| `/project:create-plan` | Generate a technical plan from approved spec |
| `/project:create-tasks` | Break the plan into atomic tasks |
| `/project:implement` | Implement one task at a time |
| `/project:write-tests` | Write pytest tests from spec criteria |
| `/project:review` | Full validation against spec |

---

## Tips from experience

- **Write the rejection criteria yourself** if Claude's feel too generic. They become your best negative tests.
- **Keep specs small.** One feature per spec. If it's getting long, split it.
- **The Memory Bank is your superpower.** The more accurate it is, the better every agent performs.
- **Don't rush approvals.** Two minutes reading a spec saves two hours fixing wrong code.
