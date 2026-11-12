"""The default test run must not use the network (spec 002 RNF-7).

The autouse `network_guard` fixture in conftest.py blocks DNS lookups and connections
to anything but loopback addresses. These tests prove the guard is active.
"""

import socket

import pytest

from tests.conftest import NetworkAccessBlocked


def test_dns_lookup_of_a_hostname_is_blocked() -> None:
    with pytest.raises(NetworkAccessBlocked):
        socket.getaddrinfo("example.com", 80)


def test_connect_to_a_public_address_is_blocked() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        with pytest.raises(NetworkAccessBlocked):
            sock.connect(("198.51.100.1", 80))


def test_connect_ex_to_a_public_address_is_blocked() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        with pytest.raises(NetworkAccessBlocked):
            sock.connect_ex(("198.51.100.1", 80))


def test_loopback_literal_lookup_and_connect_still_work() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]

        assert socket.getaddrinfo("127.0.0.1", port)
        with socket.create_connection(("127.0.0.1", port), timeout=2):
            pass
