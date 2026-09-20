import pytest

from openglass.security import (
    ReservedRangeError,
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
    @pytest.mark.parametrize("value", ["8.8.8.8", "1.1.1.1", "2001:4860:4860::8888"])
    def test_valid_public(self, value: str) -> None:
        assert validate_ip(value) == value

    @pytest.mark.parametrize(
        "value",
        [
            "10.1.2.3",                 # RFC 1918
            "172.16.0.1",               # RFC 1918
            "192.168.255.254",          # RFC 1918
            "100.64.0.1",               # RFC 6598
            "192.0.2.1",                # RFC 5737
            "198.51.100.1",             # RFC 5737
            "203.0.113.1",              # RFC 5737
            "169.254.1.1",              # RFC 3927
            "127.0.0.1",                # RFC 1122
            "::1",                      # loopback IPv6
            "fe80::1",                  # link-local IPv6
            "fc00::1",                  # ULA IPv6
            "2001:db8::1",              # documentação IPv6
        ],
    )
    def test_reserved_rejected(self, value: str) -> None:
        with pytest.raises(ReservedRangeError):
            validate_ip(value)

    def test_reserved_error_carries_metadata(self) -> None:
        with pytest.raises(ReservedRangeError) as info:
            validate_ip("192.168.1.5")
        exc = info.value
        assert exc.rfc == "RFC 1918"
        assert exc.network == "192.168.0.0/16"
        assert exc.purpose == "Privado"
        assert "faixa reservada" in str(exc)

    def test_ipv4_mapped_ipv6_treated_as_ipv4(self) -> None:
        with pytest.raises(ReservedRangeError) as info:
            validate_ip("::ffff:192.168.1.5")
        assert info.value.network == "192.168.0.0/16"

    @pytest.mark.parametrize("value", ["8.8.8.8/24", "foo", "999.1.1.1", "a.b"])
    def test_invalid(self, value: str) -> None:
        with pytest.raises(SecurityError):
            validate_ip(value)


class TestPrefix:
    @pytest.mark.parametrize(
        "value,expected",
        [
            ("8.8.8.0/24", "8.8.8.0/24"),
            ("8.8.8.8", "8.8.8.8"),
            ("2001:4860:4860::/32", "2001:4860::/32"),
        ],
    )
    def test_valid_public(self, value: str, expected: str) -> None:
        assert validate_prefix(value) == expected

    @pytest.mark.parametrize(
        "value",
        [
            "10.0.0.0/8",         # exato
            "192.168.1.0/24",     # subrede dentro da faixa
            "172.16.0.0/20",      # subrede dentro da faixa
            "100.64.0.0/10",      # CGNAT
            "203.0.113.0/24",     # documentação
            "2001:db8::/32",      # documentação IPv6
            "fc00::/7",           # ULA IPv6
            "fe80::/10",          # link-local IPv6
        ],
    )
    def test_reserved_rejected(self, value: str) -> None:
        with pytest.raises(ReservedRangeError):
            validate_prefix(value)

    def test_ip_literal_reserved_prefix(self) -> None:
        with pytest.raises(ReservedRangeError):
            validate_prefix("10.0.0.1")

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
        ["10.0.0.1", "192.168.1.1", "172.31.255.1", "100.64.1.1",
         "192.0.2.1", "198.51.100.1", "203.0.113.1", "169.254.1.1", "127.0.0.1",
         "::1", "fc00::1", "fe80::1", "2001:db8::1"],
    )
    def test_reserved_ip_rejected(self, value: str) -> None:
        with pytest.raises(ReservedRangeError):
            validate_destination(value)

    @pytest.mark.parametrize(
        "value",
        ["8.8.8.8; reboot", "foo/bar", "foo bar", "x y z", "a" * 300, "$(rm -rf)"],
    )
    def test_invalid(self, value: str) -> None:
        with pytest.raises(SecurityError):
            validate_destination(value)