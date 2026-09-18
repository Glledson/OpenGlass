import pytest

from openglass.security import (
    SecurityError,
    sanitize_parameter,
    validate_destination,
    validate_ip,
    validate_prefix,
)


class TestSanitize:
    def test_trims_whitespace(self) -> None:
        assert sanitize_parameter("  8.8.8.8  ") == "8.8.8.8"

    def test_empty_rejected(self) -> None:
        with pytest.raises(SecurityError):
            sanitize_parameter("   ")
        with pytest.raises(SecurityError):
            sanitize_parameter(None)

    @pytest.mark.parametrize(
        "payload",
        [
            "8.8.8.8; show running-config",
            "8.8.8.8 & show run",
            "8.8.8.8 | show ip route",
            "8.8.8.8 > /tmp/x",
            "8.8.8.8`id`",
            "8.8.8.8$(reboot)",
            "8.8.8.8\nshow run",
            "8.8.8.8\rshow run",
            "'show running-config'",
            '"show run"',
        ],
    )
    def test_forbids_injection(self, payload: str) -> None:
        with pytest.raises(SecurityError):
            sanitize_parameter(payload)


class TestIp:
    @pytest.mark.parametrize("value", ["8.8.8.8", "10.0.0.1", "2001:db8::1"])
    def test_valid(self, value: str) -> None:
        assert validate_ip(value) == value

    @pytest.mark.parametrize("value", ["8.8.8.8/24", "foo", "999.1.1.1", "a.b"])
    def test_invalid(self, value: str) -> None:
        with pytest.raises(SecurityError):
            validate_ip(value)


class TestPrefix:
    @pytest.mark.parametrize(
        "value,expected",
        [("8.8.8.0/24", "8.8.8.0/24"), ("8.8.8.8", "8.8.8.8"), ("2001:db8::/32", "2001:db8::/32")],
    )
    def test_valid(self, value: str, expected: str) -> None:
        assert validate_prefix(value) == expected

    @pytest.mark.parametrize("value", ["8.8.8.0/33", "foo", "8.8.8.8; anything"])
    def test_invalid(self, value: str) -> None:
        with pytest.raises(SecurityError):
            validate_prefix(value)


class TestDestination:
    @pytest.mark.parametrize("value", ["8.8.8.8", "google.com", "2001:4860:4860::8888"])
    def test_valid(self, value: str) -> None:
        assert validate_destination(value) == value

    @pytest.mark.parametrize(
        "value",
        ["8.8.8.8; reboot", "foo/bar", "foo bar", "x y z", "a" * 300, "$(rm -rf)"],
    )
    def test_invalid(self, value: str) -> None:
        with pytest.raises(SecurityError):
            validate_destination(value)