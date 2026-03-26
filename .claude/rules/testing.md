# Testing rules

## Framework
- Use **pytest** exclusively. No unittest.
- Test files live in `tests/` mirroring the `src/` structure.
- Test file naming: `test_{module_name}.py`

## Test naming
- Test functions: `test_{what_it_does}_{expected_outcome}`
- Examples:
  - `test_create_user_returns_201_on_valid_input`
  - `test_create_user_raises_value_error_on_missing_email`

## Coverage requirement
- Every acceptance criterion in the spec must have at least one test.
- Every rejection criterion must have at least one test confirming the system rejects it.
- Aim for 80% line coverage minimum on `src/`.

## Test structure — AAA pattern
Every test must follow Arrange / Act / Assert:

```python
def test_calculate_total_applies_tax_correctly() -> None:
    """Test that total price correctly includes tax amount."""
    # Arrange
    price = 100.0
    tax_rate = 0.08

    # Act
    result = calculate_total(price, tax_rate)

    # Assert
    assert result == 108.0
```

## Fixtures
- Use `conftest.py` for shared fixtures.
- Keep fixtures minimal — only set up what the test actually needs.
- Name fixtures clearly: `sample_user`, `mock_db_session`, `auth_headers`.

## Mocking
- Use `pytest-mock` (`mocker` fixture) for mocking.
- Mock at the boundary (external APIs, DB, filesystem), not internal logic.

## Running tests
```bash
pytest tests/ -v --tb=short
pytest tests/ --cov=src --cov-report=term-missing
```

## What NOT to test
- Do not test third-party library behavior.
- Do not test Python built-ins.
- Test your logic, not the framework.
