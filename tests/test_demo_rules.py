"""Pure demo-mode rules (spec 003a): parsing, normalization, cap decisions, seed planning."""

import pytest

from app.services.demo_rules import (
    cap_host,
    global_cap_reached,
    normalized_url,
    parse_demo_flag,
    parse_strict_int,
    per_host_cap_reached,
)

# ---------------------------------------------------------------------------
# parse_demo_flag (RF-1)
# ---------------------------------------------------------------------------


class TestParseDemoFlag:
    def test_unset_means_off(self) -> None:
        assert parse_demo_flag(None) is False

    @pytest.mark.parametrize("raw", ["true", "TRUE", "True", "1", "yes", "YES", "on", "On"])
    def test_on_spellings(self, raw: str) -> None:
        assert parse_demo_flag(raw) is True

    @pytest.mark.parametrize("raw", ["false", "FALSE", "0", "no", "No", "off", "OFF"])
    def test_off_spellings(self, raw: str) -> None:
        assert parse_demo_flag(raw) is False

    @pytest.mark.parametrize(
        "raw", ["", " ", "t", "y", "n", "2", "maybe", " true", "true ", "enabled"]
    )
    def test_invalid_values_raise(self, raw: str) -> None:
        with pytest.raises(ValueError):
            parse_demo_flag(raw)


# ---------------------------------------------------------------------------
# parse_strict_int (RF-3)
# ---------------------------------------------------------------------------


class TestParseStrictInt:
    def test_unset_gives_default(self) -> None:
        assert parse_strict_int(None, default=20, low=1, high=1000) == 20

    @pytest.mark.parametrize(
        ("raw", "expected"), [("1", 1), ("20", 20), ("1000", 1000), ("007", 7)]
    )
    def test_decimal_digits_accepted(self, raw: str, expected: int) -> None:
        assert parse_strict_int(raw, default=20, low=1, high=1000) == expected

    @pytest.mark.parametrize(
        "raw", ["+20", "-1", "20.0", " 20", "20 ", "1_000", "2e1", "", "abc", "٢٠"]
    )
    def test_other_spellings_rejected(self, raw: str) -> None:
        with pytest.raises(ValueError):
            parse_strict_int(raw, default=20, low=1, high=1000)

    @pytest.mark.parametrize("raw", ["0", "1001"])
    def test_out_of_range_rejected(self, raw: str) -> None:
        with pytest.raises(ValueError):
            parse_strict_int(raw, default=20, low=1, high=1000)


# ---------------------------------------------------------------------------
# normalized_url and cap_host (Definitions, RF-14, RF-15)
# ---------------------------------------------------------------------------


class TestNormalizedUrl:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("http://Example.COM", "http://example.com/"),
            ("HTTP://example.com", "http://example.com/"),
            ("https://example.com:443/a", "https://example.com/a"),
            ("https://bücher.de/p", "https://xn--bcher-kva.de/p"),
        ],
    )
    def test_same_form_as_the_api(self, raw: str, expected: str) -> None:
        assert normalized_url(raw) == expected

    def test_unparsable_gives_none(self) -> None:
        assert normalized_url("not a url") is None
        assert normalized_url("ftp://example.com/") is None


class TestCapHost:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("http://Example.COM/x", "example.com"),
            ("http://example.com./x", "example.com"),
            ("http://example.com:8080/", "example.com"),
            ("https://bücher.de/", "xn--bcher-kva.de"),
            ("http://[2001:DB8::1]:8080/", "2001:db8::1"),
            ("http://93.184.216.34/", "93.184.216.34"),
            ("http://www.example.com/", "www.example.com"),
        ],
    )
    def test_derivation(self, raw: str, expected: str) -> None:
        assert cap_host(raw) == expected

    def test_unparsable_gives_none(self) -> None:
        assert cap_host("::not-a-url::") is None


# ---------------------------------------------------------------------------
# Cap decisions (RF-15)
# ---------------------------------------------------------------------------


class TestCapDecisions:
    def test_global_cap_boundary(self) -> None:
        assert global_cap_reached(19, 20) is False
        assert global_cap_reached(20, 20) is True
        assert global_cap_reached(25, 20) is True  # cap lowered by the owner

    def test_per_host_cap_counts_cap_hosts(self) -> None:
        urls = ["http://example.com/a", "http://EXAMPLE.com./b", "http://example.com:8080/c"]
        assert per_host_cap_reached(urls, "example.com", 3) is True
        assert per_host_cap_reached(urls[:2], "example.com", 3) is False
        assert per_host_cap_reached(urls, "other.com", 3) is False

    def test_per_host_cap_skips_unparsable_urls(self) -> None:
        urls = ["http://example.com/a", "garbage", "http://example.com/b"]
        assert per_host_cap_reached(urls, "example.com", 3) is False
        assert per_host_cap_reached(urls, "example.com", 2) is True
