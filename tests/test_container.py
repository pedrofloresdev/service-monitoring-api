"""The container starts the project start command directly (spec 003d RF-9)."""

import json
import re
from pathlib import Path

DOCKERFILE = Path(__file__).resolve().parent.parent / "Dockerfile"


def _instruction(name: str) -> str:
    """The full text of the last instruction `name`, with line continuations joined."""
    text = DOCKERFILE.read_text().replace("\\\n", " ")
    lines = [line.strip() for line in text.splitlines() if line.strip().startswith(name + " ")]
    assert lines, f"no {name} instruction"
    return lines[-1]


def test_cmd_is_the_start_command_in_exec_form() -> None:
    # Exec form (a JSON array), so the start command is PID 1 and receives stop signals.
    assert json.loads(_instruction("CMD")[len("CMD ") :]) == ["python", "-m", "app.serve"]


def test_healthcheck_start_period_covers_the_worst_case_startup() -> None:
    match = re.search(r"--start-period=(\d+)s", _instruction("HEALTHCHECK"))
    assert match is not None and int(match.group(1)) == 180
