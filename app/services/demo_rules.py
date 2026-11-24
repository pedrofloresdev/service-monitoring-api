"""Pure demo-mode rules (spec 003a): setting parsing, URL normalization, cap decisions.

No I/O here: the settings loader, the seeding step and the write flows call these.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from pydantic import HttpUrl, TypeAdapter, ValidationError

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
