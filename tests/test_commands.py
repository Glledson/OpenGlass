import pytest

from openglass.commands import (
    CommandNotAllowedError,
    build_command,
    list_commands,
)
from openglass.config import settings
from openglass.inventory import Device
from openglass.security import ReservedRangeError, SecurityError

DEST = {"ip": "8.8.8.8"}


def _device(source4="192.0.2.10", source6="2001:db8::1", vrfs=True) -> Device:
    return Device(
        name="r1",
        address="192.0.2.1",
        nos="cisco_ios",
        credential={"username": "admin", "password": "secret"},
        vrfs=[
            {
                "name": "global",
                "default": True,
                "ipv4": {"source_address": source4},
                "ipv6": {"source_address": source6},
            }
        ]
        if vrfs
        else None,
    )


class TestRegistry:
    def test_whitelist_from_profile(self) -> None:
        assert set(list_commands("cisco_ios")) == {
            "bgp-route",
            "ping",
            "traceroute",
        }


class TestBuildCommand:
    def test_simple_command_with_param(self) -> None:
        spec, command = build_command("cisco_ios", "bgp-route", {"prefix": "8.8.8.0/24"})
        assert command == "show ip bgp 8.8.8.0/24"
        assert spec.timeout == 120

    def test_command_timeout_from_profile(self) -> None:
        spec, _ = build_command("cisco_ios", "traceroute", {"ip": "1.1.1.1"})
        assert spec.effective_timeout() == 90

    def test_ping_without_source_from_vrf(self) -> None:
        _, command = build_command("cisco_ios", "ping", DEST)
        assert command == "ping 8.8.8.8"

    def test_ping_uses_ipv4_source_from_vrf(self) -> None:
        _, command = build_command("cisco_ios", "ping", DEST, device=_device())
        assert command == "ping 8.8.8.8 source 192.0.2.10"

    def test_ping_ipv6_uses_ipv6_source(self) -> None:
        device = _device()
        _, command = build_command(
            "cisco_ios", "ping", {"ip": "2001:4860:4860::8888"}, device=device
        )
        assert command == "ping 2001:4860:4860::8888 source 2001:db8::1"

    def test_ping_without_configured_source(self) -> None:
        _, command = build_command("cisco_ios", "ping", DEST, device=_device(source4=None, source6=None))
        assert command == "ping 8.8.8.8"

    def test_unknown_command_rejected(self) -> None:
        with pytest.raises(CommandNotAllowedError):
            build_command("cisco_ios", "show running-config")

    def test_arbitrary_raw_command_rejected(self) -> None:
        with pytest.raises(CommandNotAllowedError):
            build_command("cisco_ios", "enable")

    def test_missing_param_rejected(self) -> None:
        with pytest.raises(SecurityError):
            build_command("cisco_ios", "ping")

    def test_injected_param_rejected(self) -> None:
        with pytest.raises(SecurityError):
            build_command("cisco_ios", "ping", {"ip": "8.8.8.8; show running-config"}, device=_device())
        with pytest.raises(SecurityError):
            build_command("cisco_ios", "bgp-route", {"prefix": "8.8.8.0/24 & reboot"})

    @pytest.mark.parametrize(
        "label,params",
        [
            ("ping", {"ip": "192.168.1.1"}),
            ("traceroute", {"ip": "10.0.0.1"}),
            ("bgp-route", {"prefix": "172.16.0.0/12"}),
            ("bgp-route", {"prefix": "10.0.0.0/8"}),
        ],
    )
    def test_reserved_range_rejected(self, label: str, params: dict[str, str]) -> None:
        with pytest.raises(ReservedRangeError):
            build_command("cisco_ios", label, params, device=_device())

    def test_unknown_nos_profile_rejected(self) -> None:
        with pytest.raises(Exception):
            build_command("mikrotik", "ping", DEST)


class TestRun:
    PING_OUTPUT = (
        "Sending 5, 100-byte ICMP Echos to 8.8.8.8, timeout is 2 seconds:\n"
        "Packet sent with a source address of 192.0.2.10\n"
        "!!!!!\n"
        "Success rate is 100 percent (5/5), round-trip min/avg/max = 1/2/3 ms\n"
    )

    def test_run_sends_command_with_source_and_parses(self) -> None:
        class FakeSession:
            device = _device()
            captured: list[str] = []

            def run_command(self, command: str, timeout: float) -> str:
                self.captured.append(command)
                return TestRun.PING_OUTPUT

        from openglass.commands import run as run_command

        session = FakeSession()
        result = run_command(session, "ping", DEST)
        assert session.captured == ["ping 8.8.8.8 source 192.0.2.10"]
        assert result.raw == self.PING_OUTPUT
        assert result.command == "ping 8.8.8.8 source 192.0.2.10"
        assert result.description
        assert result.parser == "ping"
        assert result.parsed is not None
        assert result.parsed["status"] == "success"
        assert result.parsed["target"] == "8.8.8.8"

    def test_run_keeps_raw_when_parser_fails(self) -> None:
        class FakeSession:
            device = _device()

            def run_command(self, command: str, timeout: float) -> str:
                return "!! saida fora do formato de ping"

        from openglass.commands import run as run_command

        result = run_command(FakeSession(), "ping", DEST)
        assert result.raw == "!! saida fora do formato de ping"
        assert result.parsed is None

    def test_command_without_parser_has_no_parsed(self, tmp_path, monkeypatch) -> None:
        (tmp_path / "nos.yaml").write_text(
            "commands:\n"
            "  show-something:\n"
            "    template: 'show something'\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(settings, "nodes_dir", str(tmp_path))

        from openglass.nodes import clear_profile_cache

        clear_profile_cache()
        try:

            class FakeSession:
                device = Device(
                    name="r1",
                    address="192.0.2.1",
                    nos="nos",
                    credential={"username": "admin", "password": "secret"},
                )

                def run_command(self, command: str, timeout: float) -> str:
                    return "algum output cru"

            from openglass.commands import run as run_command

            result = run_command(FakeSession(), "show-something")
            assert result.raw == "algum output cru"
            assert result.parser is None
            assert result.parsed is None
        finally:
            clear_profile_cache()