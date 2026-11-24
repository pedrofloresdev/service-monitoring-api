"""Demo-mode settings (spec 003a RF-2, RF-3).

Built only while demo mode is on. Every value is read as a raw string and parsed with the
strict rules in app.services.demo_rules, and every error is reported at once.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic_settings import BaseSettings, SettingsConfigDict

from app.services.demo_rules import SeedDefinition, parse_seed_list, parse_strict_int


class RawDemoSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    DEMO_SEED_SERVICES: str | None = None
    DEMO_GLOBAL_VISITOR_CAP: str | None = None
    DEMO_PER_HOST_VISITOR_CAP: str | None = None
    DEMO_MIN_CHECK_INTERVAL_SECONDS: str | None = None


@dataclass(frozen=True)
class DemoConfig:
    seeds: tuple[SeedDefinition, ...]
    global_cap: int
    per_host_cap: int
    min_interval: int


class DemoConfigError(Exception):
    def __init__(self, errors: list[str]) -> None:
        super().__init__("Invalid demo settings:\n  - " + "\n  - ".join(errors))
        self.errors = errors


# Set once at startup while demo mode is on; None means demo mode is off.
demo_config: DemoConfig | None = None


def load_demo_config() -> DemoConfig:
    raw = RawDemoSettings()
    errors: list[str] = []

    def number(name: str, default: int, low: int, high: int) -> int | None:
        try:
            return parse_strict_int(getattr(raw, name), default=default, low=low, high=high)
        except ValueError as exc:
            errors.append(f"{name}: {exc}")
            return None

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
