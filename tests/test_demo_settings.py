"""Demo-mode settings (spec 003a RF-1 to RF-3)."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.core.demo_settings import DemoConfig, DemoConfigError, load_demo_config
from app.services.demo_rules import SeedDefinition

REPO_ROOT = Path(__file__).resolve().parent.parent
SEED = '[{"name": "Example", "url": "https://example.com/health"}]'
DEMO_VARS = (
    "DEMO_MODE",
    "DEMO_SEED_SERVICES",
    "DEMO_GLOBAL_VISITOR_CAP",
    "DEMO_PER_HOST_VISITOR_CAP",
    "DEMO_MIN_CHECK_INTERVAL_SECONDS",
)


@pytest.fixture(autouse=True)
def clean_demo_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for name in DEMO_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)  # no .env file is picked up


def _import_app(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Import the app in a fresh interpreter, as a real startup would."""
    full_env = {k: v for k, v in os.environ.items() if k not in DEMO_VARS}
    full_env.update({"DATABASE_URL": "sqlite://", **env})
    return subprocess.run(
        [sys.executable, "-c", "import app.main"],
        cwd=REPO_ROOT,
        env=full_env,
        capture_output=True,
        text=True,
        check=False,
    )


class TestDemoModeFlag:
    def test_unset_is_off(self) -> None:
        assert Settings(DATABASE_URL="sqlite://").demo_mode is False

    @pytest.mark.parametrize(("raw", "expected"), [("true", True), ("ON", True), ("0", False)])
    def test_valid_values(self, raw: str, expected: bool) -> None:
        assert Settings(DATABASE_URL="sqlite://", DEMO_MODE=raw).demo_mode is expected

    @pytest.mark.parametrize("raw", ["", "maybe", "t"])
    def test_invalid_value_names_demo_mode(self, raw: str) -> None:
        with pytest.raises(ValidationError) as exc:
            Settings(DATABASE_URL="sqlite://", DEMO_MODE=raw)
        assert "DEMO_MODE" in str(exc.value)

    def test_invalid_value_stops_app_import(self) -> None:
        result = _import_app({"DEMO_MODE": "maybe"})
        assert result.returncode != 0
        assert "DEMO_MODE" in result.stderr

    def test_demo_off_ignores_malformed_demo_settings(self) -> None:
        result = _import_app(
            {
                "DEMO_MODE": "false",
                "DEMO_SEED_SERVICES": "{not json",
                "DEMO_GLOBAL_VISITOR_CAP": "lots",
                "DEMO_PER_HOST_VISITOR_CAP": "-1",
                "DEMO_MIN_CHECK_INTERVAL_SECONDS": "5.5",
            }
        )
        assert result.returncode == 0, result.stderr


class TestLoadDemoConfig:
    def test_defaults_with_a_seed_list(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DEMO_SEED_SERVICES", SEED)
        assert load_demo_config() == DemoConfig(
            seeds=(SeedDefinition("Example", "https://example.com/health", 200, 60),),
            global_cap=20,
            per_host_cap=3,
            min_interval=300,
        )

    def test_reports_every_bad_variable_at_once(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DEMO_SEED_SERVICES", "[]")
        monkeypatch.setenv("DEMO_GLOBAL_VISITOR_CAP", "+20")
        monkeypatch.setenv("DEMO_MIN_CHECK_INTERVAL_SECONDS", "5")
        with pytest.raises(DemoConfigError) as exc:
            load_demo_config()
        text = str(exc.value)
        for name in (
            "DEMO_SEED_SERVICES",
            "DEMO_GLOBAL_VISITOR_CAP",
            "DEMO_MIN_CHECK_INTERVAL_SECONDS",
        ):
            assert name in text
        assert len(exc.value.errors) == 3

    def test_per_host_above_global_names_both(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DEMO_SEED_SERVICES", SEED)
        monkeypatch.setenv("DEMO_GLOBAL_VISITOR_CAP", "2")
        with pytest.raises(DemoConfigError) as exc:
            load_demo_config()
        assert exc.value.errors == [
            "DEMO_PER_HOST_VISITOR_CAP (3) must not be greater than DEMO_GLOBAL_VISITOR_CAP (2)"
        ]

    def test_missing_seed_list_is_an_error(self) -> None:
        with pytest.raises(DemoConfigError) as exc:
            load_demo_config()
        assert "DEMO_SEED_SERVICES" in str(exc.value)


def test_env_example_lists_exactly_the_settings_read() -> None:
    from app.core.demo_settings import RawDemoSettings

    keys = set()
    for line in (REPO_ROOT / ".env.example").read_text().splitlines():
        line = line.lstrip("# ").strip()
        if "=" in line and line.split("=", 1)[0].isupper():
            keys.add(line.split("=", 1)[0])
    expected = set(Settings.model_fields) | set(RawDemoSettings.model_fields)
    assert keys == expected
