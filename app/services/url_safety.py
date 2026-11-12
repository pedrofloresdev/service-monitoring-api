"""URL safety: never send a check to a non-public address (spec 002).

Pure classification of addresses and hosts. The blocked range table mirrors the spec
exactly; `ipaddress.is_global` is not used because it disagrees with the spec
(embedded-IPv4 forms, NAT64, the "whole block" rule).
"""

from enum import Enum
from ipaddress import IPv4Address, IPv4Network, IPv6Address, IPv6Network, ip_address

IPAddress = IPv4Address | IPv6Address

# Fixed texts (spec 002 RF-5, RF-7, RF-8, RF-9).
NOT_ALLOWED_MESSAGE = "Destination not allowed: the URL host is not a public address"
UNSAFE_LITERAL_MESSAGE = "unsafe destination: host is not a public address"
UNSAFE_RESOLVED_MESSAGE = "unsafe destination: host resolves to a non-public address"
RESOLUTION_FAILED_PREFIX = "resolution failed:"

BLOCKED_IPV4: tuple[IPv4Network, ...] = tuple(
    IPv4Network(block)
    for block in (
        "0.0.0.0/8",
        "10.0.0.0/8",
        "100.64.0.0/10",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "172.16.0.0/12",
        "192.0.0.0/24",
        "192.0.2.0/24",
        "192.88.99.0/24",
        "192.168.0.0/16",
        "198.18.0.0/15",
        "198.51.100.0/24",
        "203.0.113.0/24",
        "224.0.0.0/4",
        "240.0.0.0/4",
    )
)

# IPv6 outside global unicast is blocked; these are the blocked blocks inside it.
GLOBAL_UNICAST_V6 = IPv6Network("2000::/3")
BLOCKED_IPV6_IN_GLOBAL: tuple[IPv6Network, ...] = tuple(
    IPv6Network(block)
    for block in ("2001::/23", "2001:db8::/32", "2002::/16", "3fff::/20", "5f00::/16")
)

# Embedded-IPv4 forms, judged by the IPv4 in their last 32 bits.
IPV4_MAPPED = IPv6Network("::ffff:0:0/96")
IPV4_COMPATIBLE = IPv6Network("::/96")
NAT64_WELL_KNOWN = IPv6Network("64:ff9b::/96")
_NOT_COMPATIBLE = (IPv6Address("::"), IPv6Address("::1"))


class HostClass(Enum):
    RESERVED = "reserved"
    UNSAFE_LITERAL = "unsafe_literal"
    PUBLIC_LITERAL = "public_literal"
    HOSTNAME = "hostname"


def embedded_ipv4(address: IPv6Address) -> IPv4Address | None:
    """The IPv4 inside an IPv4-mapped, IPv4-compatible or well-known NAT64 address."""
    in_compatible = address in IPV4_COMPATIBLE and address not in _NOT_COMPATIBLE
    if address in IPV4_MAPPED or in_compatible or address in NAT64_WELL_KNOWN:
        return IPv4Address(int(address) & 0xFFFFFFFF)
    return None


def is_public_address(address: IPAddress) -> bool:
    if isinstance(address, IPv4Address):
        return not any(address in block for block in BLOCKED_IPV4)
    embedded = embedded_ipv4(address)
    if embedded is not None:
        return is_public_address(embedded)
    if address not in GLOBAL_UNICAST_V6:
        return False
    return not any(address in block for block in BLOCKED_IPV6_IN_GLOBAL)


def normalize_host(host: str) -> str:
    """Strip IPv6 brackets and lower-case. The URL parser has already normalized the rest."""
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    return host.lower()


def is_reserved_name(host: str) -> bool:
    name = host.lower().rstrip(".")
    return name == "localhost" or name.endswith(".localhost")


def parse_literal(host: str) -> IPAddress | None:
    try:
        return ip_address(host)
    except ValueError:
        return None


def classify_host(host: str) -> HostClass:
    normalized = normalize_host(host)
    if is_reserved_name(normalized):
        return HostClass.RESERVED
    literal = parse_literal(normalized)
    if literal is None:
        return HostClass.HOSTNAME
    return HostClass.PUBLIC_LITERAL if is_public_address(literal) else HostClass.UNSAFE_LITERAL
