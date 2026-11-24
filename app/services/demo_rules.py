"""Pure demo-mode rules (spec 003a): setting parsing, URL normalization, cap decisions.

No I/O here: the settings loader, the seeding step and the write flows call these.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from pydantic import HttpUrl, TypeAdapter, ValidationError

from app.schemas.services import ServiceCreate

_TRUE = frozenset({"true", "1", "yes", "on"})
_FALSE = frozenset({"false", "0", "no", "off"})
_DIGITS = re.compile(r"[0-9]+")
_URL = TypeAdapter(HttpUrl)


def parse_demo_flag(raw: str | None) -> bool:
    """`DEMO_MODE`: unset means off; only the listed spellings, case-insensitive (RF-1)."""
    if raw is None:
        return False
    value = raw.lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    raise ValueError("must be one of true, false, 1, 0, yes, no, on, off")


def parse_strict_int(raw: str | None, *, default: int, low: int, high: int) -> int:
    """Decimal digits only (no sign, decimal point, separators or spaces), within [low, high]."""
    if raw is None:
        return default
    if not _DIGITS.fullmatch(raw):
        raise ValueError("must be a whole number written with decimal digits only")
    value = int(raw)
    if not low <= value <= high:
        raise ValueError(f"must be between {low} and {high}")
    return value


def parse_url(raw: str) -> HttpUrl | None:
    """Parse with the API's own URL rules; None when the API would reject it."""
    try:
        return _URL.validate_python(raw)
    except ValidationError:
        return None


def normalized_url(raw: str) -> str | None:
    """The text the API stores for a URL, or None if it can't be parsed (Definitions)."""
    url = parse_url(raw)
    return None if url is None else str(url)


def cap_host(raw_url: str) -> str | None:
    """Host used for the per-host cap: lower-case, punycode, no brackets, port or trailing dot."""
    url = parse_url(raw_url)
    if url is None or url.host is None:
        return None
    host = url.host
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    return host.lower().rstrip(".")


def global_cap_reached(visitor_count: int, cap: int) -> bool:
    return visitor_count >= cap


def per_host_cap_reached(visitor_urls: Iterable[str], host: str, cap: int) -> bool:
    count = sum(1 for url in visitor_urls if cap_host(url) == host)
    return count >= cap


# ---------------------------------------------------------------------------
# Seed list (RF-3, RF-4)
# ---------------------------------------------------------------------------

SEED_VARIABLE = "DEMO_SEED_SERVICES"
MAX_SEEDS = 20
_SEED_KEYS = frozenset({"name", "url", "expected_status", "check_interval"})
_INT_KEYS = ("expected_status", "check_interval")


@dataclass(frozen=True)
class SeedDefinition:
    name: str
    url: str  # normalized, as the API stores it
    expected_status: int
    check_interval: int


def _seed_label(index: int, entry: object) -> str:
    label = f"definition #{index}"
    name = entry.get("name") if isinstance(entry, dict) else None
    if isinstance(name, str) and 1 <= len(name) <= 100:
        label += f" ({name})"
    return label


def _entry_problems(entry: object) -> list[str]:
    """Strict JSON types first, then the API's own field rules (never echoing values)."""
    if not isinstance(entry, dict):
        return ["not a JSON object"]
    problems = [f"unknown key {key!r}" for key in sorted(set(entry) - _SEED_KEYS)]
    for key in ("name", "url"):
        if key not in entry:
            problems.append(f"{key} is required")
        elif not isinstance(entry[key], str):
            problems.append(f"{key} must be a string")
    for key in _INT_KEYS:
        if key in entry and type(entry[key]) is not int:  # rejects true/false, floats, strings
            problems.append(f"{key} must be a JSON integer")
    if problems:
        return problems
    parsed = parse_url(entry["url"])
    if parsed is not None and (parsed.username or parsed.password):
        return ["url must not contain user information"]
    try:
        ServiceCreate.model_validate(entry)
    except ValidationError as exc:
        return [f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in exc.errors()]
    return []


def parse_seed_list(raw: str | None) -> tuple[list[SeedDefinition], list[str]]:
    """Parse `DEMO_SEED_SERVICES`; collect every error instead of stopping at the first."""

    def whole(problem: str) -> tuple[list[SeedDefinition], list[str]]:
        return [], [f"{SEED_VARIABLE}: {problem}"]

    if raw is None:
        return whole("it is empty (1 to 20 definitions are required)")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return whole("not valid JSON")
    if not isinstance(data, list):
        return whole("not a JSON array")
    if not data:
        return whole("it is empty (1 to 20 definitions are required)")
    if len(data) > MAX_SEEDS:
        return whole(f"more than {MAX_SEEDS} definitions ({len(data)})")

    seeds: list[SeedDefinition] = []
    errors: list[str] = []
    first_index: dict[str, int] = {}
    for index, entry in enumerate(data, start=1):
        label = _seed_label(index, entry)
        problems = _entry_problems(entry)
        if problems:
            errors.append(f"{SEED_VARIABLE}: {label}: {'; '.join(problems)}")
            continue
        model = ServiceCreate.model_validate(entry)
        url = str(model.url)
        if url in first_index:
            errors.append(f"{SEED_VARIABLE}: {label}: same URL as definition #{first_index[url]}")
            continue
        first_index[url] = index
        seeds.append(SeedDefinition(model.name, url, model.expected_status, model.check_interval))
    return (seeds, errors) if not errors else ([], errors)


# ---------------------------------------------------------------------------
# Seed planning (RF-5, RF-6)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StoredService:
    id: int
    url: str
    origin: str


@dataclass(frozen=True)
class SeedPlan:
    creates: list[SeedDefinition] = field(default_factory=list)
    adoptions: list[tuple[int, SeedDefinition]] = field(default_factory=list)
    demotions: list[int] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def plan_seeding(stored: Sequence[StoredService], seeds: Sequence[SeedDefinition]) -> SeedPlan:
    """Decide which seeds to create, which stored services to adopt and which to demote."""
    plan = SeedPlan()
    by_url: dict[str, list[int]] = {}
    unparsable: set[int] = set()
    for row in sorted(stored, key=lambda r: r.id):
        url = normalized_url(row.url)
        if url is None:
            unparsable.add(row.id)
            plan.warnings.append(f"service {row.id}: stored URL cannot be parsed; it is ignored")
            continue
        by_url.setdefault(url, []).append(row.id)

    adopted: set[int] = set()
    for seed in seeds:
        matches = by_url.get(seed.url, [])
        if not matches:
            plan.creates.append(seed)
            continue
        chosen, *others = matches
        plan.adoptions.append((chosen, seed))
        adopted.add(chosen)
        if others:
            ids = ", ".join(str(i) for i in others)
            plan.warnings.append(
                f"seed {seed.name!r}: services {ids} also match its URL; "
                f"service {chosen} (lowest id) is used and the others keep their origin"
            )

    seed_urls = {seed.url for seed in seeds}
    for row in sorted(stored, key=lambda r: r.id):
        if row.origin != "seeded" or row.id in adopted:
            continue
        if row.id in unparsable or normalized_url(row.url) not in seed_urls:
            plan.demotions.append(row.id)
    return plan
