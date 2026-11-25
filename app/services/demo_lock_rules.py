"""Pure rules of the project start command and the demo lock (spec 003d, plan §6)."""

from __future__ import annotations

import hashlib
import re
from typing import Any, Literal
from urllib.parse import quote

from sqlalchemy.engine import make_url

CONNECT_TIMEOUT_SECONDS = 2  # libpq's minimum (spec R16)
KEEPALIVE_PROBES = 3
APPLICATION_NAME = "service-monitoring-api-demo-lock"
DEFAULT_PORT = 8000

_DIGITS = re.compile(r"[0-9]+")

# Measured on PostgreSQL 15 / libpq 17 (plan §1). Anything else is permanent.
_UNREACHABLE_MARKERS = (
    "Connection refused",
    "timeout expired",
    "could not translate host name",
    "Name or service not known",
    "Temporary failure in name resolution",
    "No route to host",
    "Network is unreachable",
    "the database system is starting up",
    "the database system is shutting down",
    "the database system is in recovery mode",
    "sorry, too many clients already",
    "server closed the connection unexpectedly",
    "No such file or directory",
)


def _digits(raw: str) -> int | None:
    return int(raw) if _DIGITS.fullmatch(raw) else None


def parse_web_concurrency(raw: str | None) -> int:
    """`WEB_CONCURRENCY` in demo mode (RF-3): unset or empty is 1."""
    if raw is None or raw == "":
        return 1
    value = _digits(raw)
    if value is None or value < 1:
        raise ValueError("WEB_CONCURRENCY: must be a whole number of at least 1")
    return value


def parse_port(raw: str | None) -> int:
    """`PORT` (RF-1): unset or empty is 8000."""
    if raw is None or raw == "":
        return DEFAULT_PORT
    value = _digits(raw)
    if value is None or not 1 <= value <= 65535:
        raise ValueError("PORT: must be a whole number from 1 to 65535")
    return value


def lock_key(name: str, database: str) -> int:
    """Signed 64-bit advisory-lock key from the lock name and the database name (RF-5)."""
    digest = hashlib.sha256(f"{name}\0{database}".encode()).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


def split_key(key: int) -> tuple[int, int]:
    """The (`classid`, `objid`) columns of `pg_locks` for a bigint advisory lock."""
    return (key >> 32) & 0xFFFFFFFF, key & 0xFFFFFFFF


def lock_database(database_url: str) -> str:
    """The database the lock is taken in; libpq defaults to the user name."""
    url = make_url(database_url)
    return url.database or url.username or ""


def keepalive_settings(k: int) -> tuple[int, int, int]:
    """Server-side probing so a dead client is noticed in under k seconds (RF-6)."""
    return max(1, k // 3), max(1, k // 9), KEEPALIVE_PROBES


def merge_options(existing: str | None, ours: dict[str, int]) -> str:
    """Append our `-c` settings after the URL's own, so ours take precedence."""
    parts = [existing] if existing else []
    parts.extend(f"-c {name}={value}" for name, value in ours.items())
    return " ".join(parts)


def connect_kwargs(database_url: str, keepalive_k: int) -> dict[str, Any]:
    """psycopg2 keyword arguments: the URL's parts and query, then the lock options (R14)."""
    url = make_url(database_url)
    kwargs: dict[str, Any] = {}
    for key, value in (
        ("host", url.host),
        ("port", url.port),
        ("user", url.username),
        ("password", url.password),
        ("dbname", url.database),
    ):
        if value is not None:
            kwargs[key] = value
    for name, query_value in url.query.items():
        kwargs[name] = query_value if isinstance(query_value, str) else query_value[-1]
    idle, interval, count = keepalive_settings(keepalive_k)
    kwargs["connect_timeout"] = CONNECT_TIMEOUT_SECONDS
    kwargs["application_name"] = APPLICATION_NAME
    kwargs["options"] = merge_options(
        kwargs.get("options"),
        {
            "tcp_keepalives_idle": idle,
            "tcp_keepalives_interval": interval,
            "tcp_keepalives_count": count,
        },
    )
    return kwargs


def classify_connect_error(message: str) -> Literal["unreachable", "permanent"]:
    """Retry while the database can't be reached; refuse at once otherwise (RF-5)."""
    if any(marker in message for marker in _UNREACHABLE_MARKERS):
        return "unreachable"
    return "permanent"


def public_reason(message: str, password: str | None) -> str:
    """The first line of a connection error, with the password removed (RF-5, R12)."""
    lines = message.strip().splitlines()
    reason = lines[0].strip() if lines else ""
    if password:
        for form in {password, quote(password, safe="")}:
            reason = reason.replace(form, "***")
    return reason
