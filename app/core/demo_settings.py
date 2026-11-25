"""Demo-mode settings (spec 003a RF-2, RF-3; spec 003d lock settings).

Built only while demo mode is on. Every value is read as a raw string and parsed with the
strict rules in app.services.demo_rules, and every error is reported at once.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from pydantic_settings import BaseSettings, SettingsConfigDict

from app.services.demo_rules import SeedDefinition, parse_seed_list, parse_strict_int


class RawDemoSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    DEMO_SEED_SERVICES: str | None = None
    DEMO_GLOBAL_VISITOR_CAP: str | None = None
    DEMO_PER_HOST_VISITOR_CAP: str | None = None
    DEMO_MIN_CHECK_INTERVAL_SECONDS: str | None = None
    # Spec 003d: the single-process lock.
    DEMO_LOCK_WAIT_SECONDS: str | None = None
    DEMO_LOCK_HEARTBEAT_SECONDS: str | None = None
    DEMO_LOCK_TIMEOUT_SECONDS: str | None = None
    DEMO_LOCK_KEEPALIVE_SECONDS: str | None = None
    DEMO_LOCK_NAME: str | None = None


@dataclass(frozen=True)
class DemoConfig:
    seeds: tuple[SeedDefinition, ...]
    global_cap: int
    per_host_cap: int
    min_interval: int


@dataclass(frozen=True)
class LockConfig:
    wait: int
    heartbeat: int
    timeout: int
    keepalive: int
    name: str


DEFAULT_LOCK_NAME = "service-monitoring-api-demo"
_LOCK_NAME = re.compile(r"[A-Za-z0-9_-]{1,100}")


class DemoConfigError(Exception):
    def __init__(self, errors: list[str]) -> None:
        super().__init__("Invalid demo settings:\n  - " + "\n  - ".join(errors))
        self.errors = errors


# Set once at startup while demo mode is on; None means demo mode is off.
demo_config: DemoConfig | None = None


def _number(
    raw: RawDemoSettings, errors: list[str], name: str, default: int, low: int, high: int
) -> int | None:
    try:
        return parse_strict_int(getattr(raw, name), default=default, low=low, high=high)
    except ValueError as exc:
        errors.append(f"{name}: {exc}")
        return None


def load_demo_config() -> DemoConfig:
    raw = RawDemoSettings()
    errors: list[str] = []

    def number(name: str, default: int, low: int, high: int) -> int | None:
        return _number(raw, errors, name, default, low, high)

    seeds, seed_errors = parse_seed_list(raw.DEMO_SEED_SERVICES)
    errors.extend(seed_errors)
    global_cap = number("DEMO_GLOBAL_VISITOR_CAP", 20, 1, 1000)
    per_host_cap = number("DEMO_PER_HOST_VISITOR_CAP", 3, 1, 1000)
    min_interval = number("DEMO_MIN_CHECK_INTERVAL_SECONDS", 300, 10, 86400)
    if global_cap is not None and per_host_cap is not None and per_host_cap > global_cap:
        errors.append(
            f"DEMO_PER_HOST_VISITOR_CAP ({per_host_cap}) must not be greater than "
            f"DEMO_GLOBAL_VISITOR_CAP ({global_cap})"
        )
    if errors or global_cap is None or per_host_cap is None or min_interval is None:
        raise DemoConfigError(errors)
    return DemoConfig(tuple(seeds), global_cap, per_host_cap, min_interval)


def load_lock_config() -> LockConfig:
    """Spec 003d settings; raises DemoConfigError listing every invalid one."""
    raw = RawDemoSettings()
    errors: list[str] = []
    wait = _number(raw, errors, "DEMO_LOCK_WAIT_SECONDS", 60, 0, 3600)
    heartbeat = _number(raw, errors, "DEMO_LOCK_HEARTBEAT_SECONDS", 10, 1, 300)
    timeout = _number(raw, errors, "DEMO_LOCK_TIMEOUT_SECONDS", 5, 1, 60)
    keepalive = _number(raw, errors, "DEMO_LOCK_KEEPALIVE_SECONDS", 30, 10, 600)
    name = DEFAULT_LOCK_NAME if raw.DEMO_LOCK_NAME is None else raw.DEMO_LOCK_NAME
    if not _LOCK_NAME.fullmatch(name):
        errors.append("DEMO_LOCK_NAME: must be 1 to 100 characters of letters, digits, '-' and '_'")
    if timeout is not None and heartbeat is not None and timeout >= heartbeat:
        errors.append(
            f"DEMO_LOCK_TIMEOUT_SECONDS ({timeout}) must be less than "
            f"DEMO_LOCK_HEARTBEAT_SECONDS ({heartbeat})"
        )
    if errors or wait is None or heartbeat is None or timeout is None or keepalive is None:
        raise DemoConfigError(errors)
    return LockConfig(wait, heartbeat, timeout, keepalive, name)


def validate_demo_settings() -> tuple[DemoConfig, LockConfig]:
    """Every demo setting, every error at once (spec 003d RF-4 step 2).

    Specs 003b and 003c add their loaders here.
    """
    errors: list[str] = []
    configs: list[object] = []
    for loader in (load_demo_config, load_lock_config):
        try:
            configs.append(loader())
        except DemoConfigError as exc:
            errors.extend(exc.errors)
    if errors:
        raise DemoConfigError(errors)
    demo, lock = configs
    assert isinstance(demo, DemoConfig) and isinstance(lock, LockConfig)
    return demo, lock
