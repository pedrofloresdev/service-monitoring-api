---
description: SDD - validate a spec requirement by requirement. Usage /sdd-validate 001-name
argument-hint: <NNN-name>
---
Use the reviewer subagent to go through specs/$1/spec.md requirement by requirement. For each RF: which test covers it and the result of running it. Requirements without automated tests: verify by running the app and say exactly what was checked.
If any RF is uncovered or failing, say so clearly. Do NOT fix anything yet.
Then check the completion criteria and give a verdict: is the spec fulfilled?
