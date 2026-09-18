import pytest

from openglass.commands import (
    CommandNotAllowedError,
    build_command,
    list_commands,
)
from openglass.config import settings
from openglass.inventory import Device
from openglass.security import SecurityError

DEST = {"ip": "8.8.8.8"}


def _device(source4="45.5.40.255", source6="2000:2000:1::1", vrfs=True) -> Device:
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
            "show-ip-route",
            "show-bgp-summary",
            "show-bgp-prefix",
            "ping",
            "traceroute",
        }


class TestBuildCommand:
    def test_simple_command_no_device(self) -> None:
        spec, command = build_command("cisco_ios", "show-ip-route")
        assert command == "show ip route"
        assert spec.timeout == 300

    def test_command_timeout_from_profile(self) -> None:
        spec, _ = build_command("cisco_ios", "traceroute", {"destination": "1.1.1.1"})
        assert spec.effective_timeout() == 90

    def test_ping_without_source_from_vrf(self) -> None:
        _, command = build_command("cisco_ios", "ping", DEST)
        assert command == "ping 8.8.8.8"

    def test_ping_uses_ipv4_source_from_vrf(self) -> None:
        _, command = build_command("cisco_ios", "ping", DEST, device=_device())
        assert command == "ping 8.8.8.8 source 45.5.40.255"

    def test_ping_ipv6_uses_ipv6_source(self) -> None:
        device = _device()
        _, command = build_command(
            "cisco_ios", "ping", {"ip": "2001:4860:4860::8888"}, device=device
        )
        assert command == "ping 2001:4860:4860::8888 source 2000:2000:1::1"

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
            build_command("cisco_ios", "show-bgp-prefix", {"prefix": "8.8.8.0/24 & reboot"})

    def test_unknown_nos_profile_rejected(self) -> None:
        with pytest.raises(Exception):
            build_command("mikrotik", "ping", DEST)


class TestRun:
    def test_run_sends_command_with_source(self) -> None:
        class FakeSession:
            device = _device()
            captured: list[str] = []

            def run_command(self, command: str, timeout: float) -> str:
                self.captured.append(command)
                return "!! resultado cru"

        from openglass.commands import run as run_command

        session = FakeSession()
        output, spec, command = run_command(session, "ping", DEST)
        assert session.captured == ["ping 8.8.8.8 source 45.5.40.255"]
        assert output == "!! resultado cru"
        assert command == "ping 8.8.8.8 source 45.5.40.255"
        assert spec.parser is None  # gancho da fase de parsing