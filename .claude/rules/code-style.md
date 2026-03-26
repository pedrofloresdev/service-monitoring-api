# Code style rules

## Formatter
- Use **Black** with default settings (line length 88).
- Run `black .` before considering any file complete.

## Style standard
- Follow **PEP 8** strictly.
- No unused imports. No unused variables.
- No commented-out code left in files.

## Type hints
- Every function and method must have full type annotations on all parameters and return values.
- Use `Optional[X]` instead of `X | None` for Python < 3.10 compatibility unless project specifies otherwise.
- Use `from __future__ import annotations` at the top of every file.

## Docstrings
- Every function, method, and class must have a docstring.
- Format: Google style docstrings.

```python
def calculate_total(price: float, tax_rate: float) -> float:
    """Calculate the total price including tax.

    Args:
        price: The base price before tax.
        tax_rate: The tax rate as a decimal (e.g. 0.08 for 8%).

    Returns:
        The total price including tax.

    Raises:
        ValueError: If price or tax_rate is negative.
    """
```

## Naming
- Variables and functions: `snake_case`
- Classes: `PascalCase`
- Constants: `UPPER_SNAKE_CASE`
- Private members: `_leading_underscore`

## Imports order (isort compatible)
1. Standard library
2. Third-party packages
3. Local modules
(Blank line between each group)

## Error handling
- Never use bare `except:`. Always catch specific exceptions.
- Always log errors before re-raising or handling.
- Use custom exception classes for domain errors.

## File length
- Aim for files under 300 lines. If a file exceeds this, consider splitting by responsibility.
