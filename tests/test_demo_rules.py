"""Pure demo-mode rules (spec 003a): parsing, normalization, cap decisions, seed planning."""

import pytest

from app.services.demo_rules import (
    SeedDefinition,
    StoredService,
    cap_host,
    global_cap_reached,
    normalized_url,
    parse_demo_flag,
    parse_seed_list,
    parse_strict_int,
    per_host_cap_reached,
    plan_seeding,
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


# ---------------------------------------------------------------------------
# parse_seed_list (RF-3, RF-4)
# ---------------------------------------------------------------------------

SEED = '{"name": "Example", "url": "https://example.com/health"}'


class TestParseSeedList:
    def test_minimal_definition_uses_api_defaults(self) -> None:
        seeds, errors = parse_seed_list(f"[{SEED}]")
        assert errors == []
        assert seeds == [SeedDefinition("Example", "https://example.com/health", 200, 60)]

    def test_all_fields(self) -> None:
        raw = '[{"name": "A", "url": "https://Example.com", "expected_status": 204, "check_interval": 30}]'
        seeds, errors = parse_seed_list(raw)
        assert errors == []
        assert seeds == [SeedDefinition("A", "https://example.com/", 204, 30)]

    @pytest.mark.parametrize(
        ("raw", "problem"),
        [
            (None, "it is empty"),
            ("[]", "it is empty"),
            ("not json", "not valid JSON"),
            ('{"name": "x"}', "not a JSON array"),
            (
                "[" + ",".join([SEED.replace("health", f"h{i}") for i in range(21)]) + "]",
                "more than 20",
            ),
        ],
    )
    def test_whole_list_errors(self, raw: str | None, problem: str) -> None:
        seeds, errors = parse_seed_list(raw)
        assert seeds == []
        assert len(errors) == 1
        assert errors[0].startswith("DEMO_SEED_SERVICES: ")
        assert problem in errors[0]

    @pytest.mark.parametrize(
        ("entry", "problem"),
        [
            ('{"name": "A", "url": "https://a.example", "extra": 1}', "unknown key 'extra'"),
            (
                '{"name": "A", "url": "https://a.example", "expected_status": true}',
                "expected_status",
            ),
            (
                '{"name": "A", "url": "https://a.example", "expected_status": "200"}',
                "expected_status",
            ),
            ('{"name": "A", "url": "https://a.example", "check_interval": 1.0}', "check_interval"),
            ('{"name": "A", "url": "https://a.example", "check_interval": 5}', "check_interval"),
            ('{"name": "A", "url": "https://a.example", "expected_status": 99}', "expected_status"),
            ('{"url": "https://a.example"}', "name"),
            ('{"name": "", "url": "https://a.example"}', "name"),
            ('{"name": "A"}', "url"),
            ('{"name": "A", "url": "ftp://a.example"}', "url"),
            ('{"name": "A", "url": "http://localhost/"}', "url"),
            ('{"name": "A", "url": "http://10.0.0.1/"}', "url"),
            ('"just a string"', "not a JSON object"),
        ],
    )
    def test_invalid_definitions(self, entry: str, problem: str) -> None:
        seeds, errors = parse_seed_list(f"[{SEED}, {entry}]")
        assert len(errors) == 1
        assert errors[0].startswith("DEMO_SEED_SERVICES: definition #2")
        assert problem in errors[0]

    def test_error_names_valid_name(self) -> None:
        _, errors = parse_seed_list('[{"name": "Shop", "url": "ftp://x"}]')
        assert errors[0].startswith("DEMO_SEED_SERVICES: definition #1 (Shop): ")

    def test_user_information_rejected_without_echoing_it(self) -> None:
        _, errors = parse_seed_list('[{"name": "A", "url": "https://bob:s3cret@a.example/"}]')
        assert len(errors) == 1
        assert "user information" in errors[0]
        assert "bob" not in errors[0] and "s3cret" not in errors[0]

    def test_duplicate_normalized_url(self) -> None:
        raw = '[{"name": "A", "url": "https://Example.com"}, {"name": "B", "url": "https://example.com/"}]'
        _, errors = parse_seed_list(raw)
        assert errors == ["DEMO_SEED_SERVICES: definition #2 (B): same URL as definition #1"]

    def test_duplicate_names_allowed(self) -> None:
        raw = (
            '[{"name": "A", "url": "https://a.example"}, {"name": "A", "url": "https://b.example"}]'
        )
        seeds, errors = parse_seed_list(raw)
        assert errors == [] and len(seeds) == 2

    def test_all_definition_errors_are_collected(self) -> None:
        raw = '[{"name": "A", "url": "ftp://x"}, {"name": "B", "url": "https://b.example", "x": 1}]'
        _, errors = parse_seed_list(raw)
        assert len(errors) == 2


# ---------------------------------------------------------------------------
# plan_seeding (RF-5, RF-6)
# ---------------------------------------------------------------------------


def _stored(id_: int, url: str, origin: str = "ordinary", active: bool = True) -> StoredService:
    return StoredService(id_, url, origin)


def _seed(url: str, name: str = "S") -> SeedDefinition:
    return SeedDefinition(name, url, 200, 60)


class TestPlanSeeding:
    def test_missing_seed_is_created(self) -> None:
        plan = plan_seeding([], [_seed("https://a.example/")])
        assert plan.creates == [_seed("https://a.example/")]
        assert plan.adoptions == [] and plan.demotions == [] and plan.warnings == []

    @pytest.mark.parametrize("origin", ["visitor", "ordinary", "seeded"])
    def test_matching_service_is_adopted(self, origin: str) -> None:
        plan = plan_seeding(
            [_stored(7, "https://a.example/", origin)], [_seed("https://a.example/")]
        )
        assert plan.creates == []
        assert plan.adoptions == [(7, _seed("https://a.example/"))]

    def test_legacy_form_matches_by_normalized_url(self) -> None:
        plan = plan_seeding([_stored(3, "https://A.EXAMPLE")], [_seed("https://a.example/")])
        assert plan.adoptions == [(3, _seed("https://a.example/"))]

    def test_several_matches_adopt_lowest_id_and_warn(self) -> None:
        stored = [_stored(9, "https://a.example/"), _stored(4, "https://A.example")]
        plan = plan_seeding(stored, [_seed("https://a.example/")])
        assert plan.adoptions == [(4, _seed("https://a.example/"))]
        assert len(plan.warnings) == 1 and "9" in plan.warnings[0]

    def test_unparsable_stored_url_is_ignored_with_warning(self) -> None:
        plan = plan_seeding([_stored(5, "::garbage::")], [_seed("https://a.example/")])
        assert plan.creates == [_seed("https://a.example/")]
        assert len(plan.warnings) == 1 and "5" in plan.warnings[0]

    def test_removed_seed_is_demoted(self) -> None:
        stored = [
            _stored(1, "https://old.example/", "seeded"),
            _stored(2, "https://a.example/", "seeded"),
        ]
        plan = plan_seeding(stored, [_seed("https://a.example/")])
        assert plan.demotions == [1]
        assert plan.adoptions == [(2, _seed("https://a.example/"))]

    def test_unparsable_seeded_row_is_demoted(self) -> None:
        plan = plan_seeding([_stored(6, "::garbage::", "seeded")], [_seed("https://a.example/")])
        assert plan.demotions == [6]

    def test_plan_is_deterministic(self) -> None:
        stored = [_stored(i, f"https://h{i}.example/", "seeded") for i in range(5, 0, -1)]
        seeds = [_seed(f"https://h{i}.example/") for i in (2, 4)]
        assert plan_seeding(stored, seeds) == plan_seeding(list(reversed(stored)), seeds)
