# Conventions

Patterns that have been established in this codebase.
Agents must follow these in addition to the base rules in `.claude/rules/`.

## Naming patterns
- {e.g. "All service classes are named {Domain}Service"}
- {e.g. "All route files are named {resource}_routes.py"}

## Error handling patterns
- {e.g. "All domain errors inherit from AppBaseError"}
- {e.g. "HTTP errors are always returned as {status, message, detail} JSON"}

## Response format (if API project)
```json
{
  "status": "success | error",
  "data": {},
  "message": ""
}
```

## Database conventions (if applicable)
- {e.g. "All table names are plural snake_case"}
- {e.g. "All primary keys are UUID"}
- {e.g. "All tables have created_at and updated_at timestamps"}

## Test conventions
- {e.g. "All fixtures live in tests/conftest.py"}
- {e.g. "Integration tests are in tests/integration/, unit tests in tests/unit/"}

## Patterns to avoid
- {e.g. "Do not use global state"}
- {e.g. "Do not import from tests/ into src/"}

---
*Update this file whenever a new pattern is established during development.*
