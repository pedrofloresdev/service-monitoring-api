"""Unit tests for URL safety logic (spec 002)."""

import ipaddress
import socket
import time
from collections.abc import Callable
from typing import Any

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.services import url_safety
from app.services.url_safety import (
    HostClass,
    Resolution,
    classify_host,
    embedded_ipv4,
    is_public_address,
    is_reserved_name,
    normalize_host,
    parse_literal,
    resolve_host,  # the real function: conftest's fake_resolver patches the module attribute only
)
from tests.conftest import PUBLIC_TEST_ADDRESS, FakeResolver

# ---------------------------------------------------------------------------
# Blocked range table (spec 002, Definitions)
# ---------------------------------------------------------------------------

# One address inside every IPv4 row of the table.
BLOCKED_IPV4 = [
    "0.0.0.0",
    "0.255.255.255",
    "10.0.0.1",
    "10.255.255.255",
    "100.64.0.1",
    "100.127.255.255",
    "127.0.0.1",
    "127.255.255.254",
    "169.254.169.254",
    "172.16.0.1",
    "172.31.255.255",
    "192.0.0.1",
    "192.0.0.9",  # registry marks it globally reachable; the whole /24 is blocked anyway
    "192.0.2.1",
    "192.88.99.1",
    "192.168.1.1",
    "198.18.0.1",
    "198.19.255.255",
    "198.51.100.1",
    "203.0.113.1",
    "224.0.0.1",
    "239.255.255.255",
    "240.0.0.1",
    "255.255.255.255",
]

# Public neighbours just outside the blocks, plus well-known public addresses.
PUBLIC_IPV4 = [
    "1.1.1.1",
    "8.8.8.8",
    "9.255.255.255",
    "11.0.0.0",
    "93.184.216.34",
    "100.63.255.255",
    "100.128.0.0",
    "126.255.255.255",
    "128.0.0.0",
    "169.253.255.255",
    "169.255.0.0",
    "172.15.255.255",
    "172.32.0.0",
    "192.0.1.0",
    "192.0.3.0",
    "192.88.98.255",
    "192.88.100.0",
    "192.167.255.255",
    "192.169.0.0",
    "198.17.255.255",
    "198.20.0.0",
    "198.51.99.255",
    "203.0.112.255",
    "223.255.255.255",
]

BLOCKED_IPV6 = [
    "::",
    "::1",
    "64:ff9b:1::1",  # local-use NAT64, outside 2000::/3
    "100::1",
    "fc00::1",
    "fd12:3456::1",
    "fe80::1",
    "fec0::1",
    "ff02::1",
    "4000::1",  # outside 2000::/3
    "2001::1",  # Teredo
    "2001:1ff:ffff::1",  # end of 2001::/23
    "2001:db8::1",
    "2002:808:808::1",  # 6to4 is blocked even when it embeds a public IPv4
    "2002:a00:1::1",
    "3fff::1",
    "3fff:fff::1",
    "5f00::1",
]

PUBLIC_IPV6 = [
    "2001:200::1",  # just after 2001::/23
    "2001:4860:4860::8888",
    "2606:4700:4700::1111",
    "2a00:1450:4001::1",
    "3ffe::1",
    "3fff:1000::1",  # just after 3fff::/20
]

# Embedded-IPv4 forms are judged by their embedded IPv4.
EMBEDDED = [
    ("::ffff:127.0.0.1", False),
    ("::ffff:10.0.0.1", False),
    ("::ffff:8.8.8.8", True),
    ("::7f00:1", False),  # IPv4-compatible 127.0.0.1
    ("::a9fe:a9fe", False),  # IPv4-compatible 169.254.169.254
    ("::808:808", True),  # IPv4-compatible 8.8.8.8
    ("::2", False),  # IPv4-compatible 0.0.0.2
    ("64:ff9b::a00:1", False),  # NAT64 of 10.0.0.1
    ("64:ff9b::7f00:1", False),  # NAT64 of 127.0.0.1
    ("64:ff9b::808:808", True),  # NAT64 of 8.8.8.8 (DNS64)
]


@pytest.mark.parametrize("address", BLOCKED_IPV4 + BLOCKED_IPV6)
def test_blocked_addresses_are_not_public(address: str) -> None:
    assert is_public_address(ipaddress.ip_address(address)) is False


@pytest.mark.parametrize("address", PUBLIC_IPV4 + PUBLIC_IPV6)
def test_addresses_outside_the_table_are_public(address: str) -> None:
    assert is_public_address(ipaddress.ip_address(address)) is True


@pytest.mark.parametrize(("address", "public"), EMBEDDED)
def test_embedded_ipv4_forms_are_judged_by_their_ipv4(address: str, public: bool) -> None:
    assert is_public_address(ipaddress.ip_address(address)) is public


@pytest.mark.parametrize(
    ("address", "expected"),
    [
        ("::ffff:8.8.8.8", "8.8.8.8"),
        ("::808:808", "8.8.8.8"),
        ("64:ff9b::808:808", "8.8.8.8"),
        ("::", None),
        ("::1", None),
        ("64:ff9b:1::808:808", None),
        ("2606:4700:4700::1111", None),
    ],
)
def test_embedded_ipv4(address: str, expected: str | None) -> None:
    result = embedded_ipv4(ipaddress.IPv6Address(address))
    assert result == (ipaddress.IPv4Address(expected) if expected else None)


# ---------------------------------------------------------------------------
# Hosts
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("[::1]", "::1"),
        ("[::FFFF:7F00:1]", "::ffff:7f00:1"),
        ("Example.COM", "example.com"),
        ("localhost.", "localhost."),
    ],
)
def test_normalize_host(host: str, expected: str) -> None:
    assert normalize_host(host) == expected


@pytest.mark.parametrize(
    "host",
    ["localhost", "LOCALHOST.", "localhost.", "localhost..", "app.localhost", "a.b.localhost."],
)
def test_reserved_names(host: str) -> None:
    assert is_reserved_name(host) is True


@pytest.mark.parametrize(
    "host", ["localhost.example.com", "mylocalhost", "localhostx", "example.com", "local"]
)
def test_names_that_are_not_reserved(host: str) -> None:
    assert is_reserved_name(host) is False


def test_parse_literal() -> None:
    assert parse_literal("127.0.0.1") == ipaddress.IPv4Address("127.0.0.1")
    assert parse_literal("::1") == ipaddress.IPv6Address("::1")
    assert parse_literal("example.com") is None
    assert parse_literal("localhost") is None


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("localhost", HostClass.RESERVED),
        ("LOCALHOST.", HostClass.RESERVED),
        ("localhost..", HostClass.RESERVED),
        ("api.localhost", HostClass.RESERVED),
        ("127.0.0.1", HostClass.UNSAFE_LITERAL),
        ("0.0.0.0", HostClass.UNSAFE_LITERAL),
        ("169.254.169.254", HostClass.UNSAFE_LITERAL),
        ("[::1]", HostClass.UNSAFE_LITERAL),
        ("[::ffff:7f00:1]", HostClass.UNSAFE_LITERAL),
        ("[64:ff9b::a00:1]", HostClass.UNSAFE_LITERAL),
        ("8.8.8.8", HostClass.PUBLIC_LITERAL),
        ("[::ffff:808:808]", HostClass.PUBLIC_LITERAL),
        ("[64:ff9b::808:808]", HostClass.PUBLIC_LITERAL),
        ("[2606:4700:4700::1111]", HostClass.PUBLIC_LITERAL),
        ("example.com", HostClass.HOSTNAME),
        ("db", HostClass.HOSTNAME),
        ("localhost.example.com", HostClass.HOSTNAME),
        ("xn--bcher-kva.de", HostClass.HOSTNAME),
    ],
)
def test_classify_host(host: str, expected: HostClass) -> None:
    assert classify_host(host) is expected


# ---------------------------------------------------------------------------
# Resolution timeout setting (spec 002 RNF-3)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["abc", 0, -1, 30.5])
def test_invalid_resolution_timeout_is_rejected(value: object) -> None:
    overrides: dict[str, Any] = {"RESOLUTION_TIMEOUT_SECONDS": value}
    with pytest.raises(ValidationError):
        Settings(**overrides)


@pytest.mark.parametrize("value", [0.1, 30, "1.5"])
def test_valid_resolution_timeout_is_accepted(value: object) -> None:
    overrides: dict[str, Any] = {"RESOLUTION_TIMEOUT_SECONDS": value}
    assert Settings(**overrides).RESOLUTION_TIMEOUT_SECONDS == float(str(value))


def test_resolution_timeout_defaults_to_two_seconds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RESOLUTION_TIMEOUT_SECONDS", raising=False)
    assert Settings(_env_file=None).RESOLUTION_TIMEOUT_SECONDS == 2.0


# ---------------------------------------------------------------------------
# Resolver with deadline (spec 002 RF-3, RF-6, RF-9)
# ---------------------------------------------------------------------------

V4 = "93.184.216.34"
V6 = "2606:4700:4700::1111"


def _entry(family: int, address: str) -> tuple[Any, ...]:
    sockaddr: tuple[Any, ...] = (address, 0) if family == socket.AF_INET else (address, 0, 0, 0)
    return (family, socket.SOCK_STREAM, 6, "", sockaddr)


def _fake_getaddrinfo(
    v4: list[str] | Exception, v6: list[str] | Exception, delay: dict[int, float] | None = None
) -> Callable[..., list[tuple[Any, ...]]]:
    def getaddrinfo(host: str, port: Any, family: int = 0, *args: Any) -> list[tuple[Any, ...]]:
        time.sleep((delay or {}).get(family, 0.0))
        answer = v4 if family == socket.AF_INET else v6
        if isinstance(answer, Exception):
            raise answer
        return [_entry(family, address) for address in answer]

    return getaddrinfo


def _addresses(*values: str) -> tuple[ipaddress.IPv4Address | ipaddress.IPv6Address, ...]:
    return tuple(ipaddress.ip_address(value) for value in values)


NONAME = socket.gaierror(socket.EAI_NONAME, "Name or service not known")


def test_resolver_returns_both_families_ipv4_first() -> None:
    result = resolve_host("example.com", 1.0, _fake_getaddrinfo([V4], [V6], {socket.AF_INET: 0.05}))
    assert result == Resolution(_addresses(V4, V6), None)


def test_resolver_keeps_partial_answer_when_one_family_fails() -> None:
    result = resolve_host("example.com", 1.0, _fake_getaddrinfo([V4], NONAME))
    assert result == Resolution(_addresses(V4), None)


def test_resolver_returns_by_the_deadline_with_the_answers_it_has() -> None:
    getaddrinfo = _fake_getaddrinfo([V4], [V6], {socket.AF_INET6: 1.0})
    start = time.monotonic()
    result = resolve_host("example.com", 0.2, getaddrinfo)
    assert time.monotonic() - start < 0.2 + 0.3
    assert result == Resolution(_addresses(V4), None)


def test_resolver_times_out_without_answers() -> None:
    getaddrinfo = _fake_getaddrinfo([V4], [V6], {socket.AF_INET: 1.0, socket.AF_INET6: 1.0})
    start = time.monotonic()
    result = resolve_host("example.com", 0.2, getaddrinfo)
    assert time.monotonic() - start < 0.2 + 0.3
    assert result == Resolution((), "timed out")


def test_resolver_reports_unknown_name() -> None:
    result = resolve_host("nope.invalid", 1.0, _fake_getaddrinfo(NONAME, NONAME))
    assert result == Resolution((), "name not found")


def test_resolver_reports_other_lookup_errors() -> None:
    failure = socket.gaierror(socket.EAI_AGAIN, "Temporary failure in name resolution")
    result = resolve_host("example.com", 1.0, _fake_getaddrinfo(failure, failure))
    assert result == Resolution((), "lookup failed")


def test_resolver_removes_duplicates_and_scope_ids() -> None:
    result = resolve_host("example.com", 1.0, _fake_getaddrinfo([V4, V4], ["fe80::1%eth0", V6]))
    assert result == Resolution(_addresses(V4, "fe80::1", V6), None)


def test_resolver_reports_unexpected_lookup_errors_without_waiting() -> None:
    failure = UnicodeError("label too long")
    start = time.monotonic()
    result = resolve_host("example.com", 1.0, _fake_getaddrinfo(failure, failure))
    assert time.monotonic() - start < 0.5
    assert result == Resolution((), "lookup failed")


def test_resolver_uses_the_socket_module_by_default() -> None:
    # Under the network guard the real lookup is blocked (and reported), so nothing comes back.
    assert resolve_host("example.com", 0.5) == Resolution((), "lookup failed")


def test_conftest_fake_resolver_replaces_the_module_resolver(fake_resolver: FakeResolver) -> None:
    fake_resolver.set("private.test", "10.0.0.1")
    assert url_safety.resolve_host("any.test", 1.0) == Resolution(
        _addresses(PUBLIC_TEST_ADDRESS), None
    )
    assert url_safety.resolve_host("private.test", 1.0) == Resolution(_addresses("10.0.0.1"), None)
    assert fake_resolver.calls == ["any.test", "private.test"]
