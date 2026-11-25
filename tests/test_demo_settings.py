"""Demo-mode settings (spec 003a RF-1 to RF-3; spec 003d lock settings and RF-4 step 2)."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.core.demo_settings import (
    DemoConfig,
    DemoConfigError,
    LockConfig,
    load_demo_config,
    load_lock_config,
    validate_demo_settings,
)
from app.services.demo_rules import SeedDefinition

REPO_ROOT = Path(__file__).resolve().parent.parent
SEED = '[{"name": "Example", "url": "https://example.com/health"}]'
DEMO_VARS = (
    "DEMO_MODE",
    "DEMO_SEED_SERVICES",
    "DEMO_GLOBAL_VISITOR_CAP",
    "DEMO_PER_HOST_VISITOR_CAP",
    "DEMO_MIN_CHECK_INTERVAL_SECONDS",
    "DEMO_LOCK_WAIT_SECONDS",
    "DEMO_LOCK_HEARTBEAT_SECONDS",
    "DEMO_LOCK_TIMEOUT_SECONDS",
    "DEMO_LOCK_KEEPALIVE_SECONDS",
    "DEMO_LOCK_NAME",
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
                "DEMO_LOCK_WAIT_SECONDS": "-1",
                "DEMO_LOCK_TIMEOUT_SECONDS": "99",
                "DEMO_LOCK_NAME": "bad name!",
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


class TestLockSettings:
    def test_defaults(self) -> None:
        assert load_lock_config() == LockConfig(
            wait=60, heartbeat=10, timeout=5, keepalive=30, name="service-monitoring-api-demo"
        )

    def test_custom_values(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for name, value in {
            "DEMO_LOCK_WAIT_SECONDS": "0",
            "DEMO_LOCK_HEARTBEAT_SECONDS": "2",
            "DEMO_LOCK_TIMEOUT_SECONDS": "1",
            "DEMO_LOCK_KEEPALIVE_SECONDS": "600",
            "DEMO_LOCK_NAME": "Staging_demo-2",
        }.items():
            monkeypatch.setenv(name, value)
        assert load_lock_config() == LockConfig(0, 2, 1, 600, "Staging_demo-2")

    @pytest.mark.parametrize(
        ("name", "raw"),
        [
            ("DEMO_LOCK_WAIT_SECONDS", "3601"),
            ("DEMO_LOCK_WAIT_SECONDS", "+5"),
            ("DEMO_LOCK_HEARTBEAT_SECONDS", "0"),
            ("DEMO_LOCK_HEARTBEAT_SECONDS", "301"),
            ("DEMO_LOCK_TIMEOUT_SECONDS", "0"),
            ("DEMO_LOCK_TIMEOUT_SECONDS", "61"),
            ("DEMO_LOCK_KEEPALIVE_SECONDS", "9"),
            ("DEMO_LOCK_KEEPALIVE_SECONDS", "601"),
            ("DEMO_LOCK_KEEPALIVE_SECONDS", "30.0"),
        ],
    )
    def test_out_of_range_or_not_digits(
        self, monkeypatch: pytest.MonkeyPatch, name: str, raw: str
    ) -> None:
        monkeypatch.setenv(name, raw)
        if name == "DEMO_LOCK_TIMEOUT_SECONDS":
            monkeypatch.setenv("DEMO_LOCK_HEARTBEAT_SECONDS", "300")
        with pytest.raises(DemoConfigError) as exc:
            load_lock_config()
        assert len(exc.value.errors) == 1 and exc.value.errors[0].startswith(f"{name}: ")

    @pytest.mark.parametrize("raw", ["", "a" * 101, "has space", "dot.name", "bücher"])
    def test_invalid_lock_name(self, monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
        monkeypatch.setenv("DEMO_LOCK_NAME", raw)
        with pytest.raises(DemoConfigError) as exc:
            load_lock_config()
        assert exc.value.errors == [
            "DEMO_LOCK_NAME: must be 1 to 100 characters of letters, digits, '-' and '_'"
        ]

    def test_lock_name_of_100_characters_is_valid(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DEMO_LOCK_NAME", "a" * 100)
        assert load_lock_config().name == "a" * 100

    @pytest.mark.parametrize("timeout", ["10", "11"])
    def test_timeout_must_be_below_heartbeat(
        self, monkeypatch: pytest.MonkeyPatch, timeout: str
    ) -> None:
        monkeypatch.setenv("DEMO_LOCK_TIMEOUT_SECONDS", timeout)
        with pytest.raises(DemoConfigError) as exc:
            load_lock_config()
        assert exc.value.errors == [
            f"DEMO_LOCK_TIMEOUT_SECONDS ({timeout}) must be less than "
            "DEMO_LOCK_HEARTBEAT_SECONDS (10)"
        ]


class TestValidateDemoSettings:
    def test_returns_both_configs(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DEMO_SEED_SERVICES", SEED)
        demo, lock = validate_demo_settings()
        assert demo.global_cap == 20 and lock.name == "service-monitoring-api-demo"

    def test_reports_003a_and_003d_errors_together(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DEMO_GLOBAL_VISITOR_CAP", "0")
        monkeypatch.setenv("DEMO_LOCK_WAIT_SECONDS", "x")
        monkeypatch.setenv("DEMO_LOCK_NAME", "bad name")
        with pytest.raises(DemoConfigError) as exc:
            validate_demo_settings()
        prefixes = [error.split(":")[0] for error in exc.value.errors]
        assert prefixes == [
            "DEMO_SEED_SERVICES",
            "DEMO_GLOBAL_VISITOR_CAP",
            "DEMO_LOCK_WAIT_SECONDS",
            "DEMO_LOCK_NAME",
        ]
        assert str(exc.value).startswith("Invalid demo settings:")


def test_env_example_lists_exactly_the_settings_read() -> None:
    from app.core.demo_settings import RawDemoSettings

    keys = set()
    for line in (REPO_ROOT / ".env.example").read_text().splitlines():
        line = line.lstrip("# ").strip()
        if "=" in line and line.split("=", 1)[0].isupper():
            keys.add(line.split("=", 1)[0])
    expected = set(Settings.model_fields) | set(RawDemoSettings.model_fields)
    assert keys == expected
