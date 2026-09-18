import pytest

from openglass.nodes import (
    NodeCommand,
    NodeError,
    clear_profile_cache,
    load_profile,
)
from openglass.config import settings


class TestLoadProfile:
    def test_loads_cisco_ios(self) -> None:
        profile = load_profile("cisco_ios")
        assert set(profile.commands) == {
            "show-ip-route",
            "show-bgp-summary",
            "show-bgp-prefix",
            "ping",
            "traceroute",
        }
        ping = profile.commands["ping"]
        assert ping.template == "ping $ip source $source_address"
        assert ping.params["ip"].type == "destination"
        assert ping.timeout == 60
        assert profile.commands["show-bgp-prefix"].params["prefix"].type == "prefix"

    def test_missing_profile_raises(self) -> None:
        with pytest.raises(NodeError):
            load_profile("mikrotik")

    def test_effective_timeout_default(self) -> None:
        command = NodeCommand(template="show ip route")
        assert command.effective_timeout() == settings.default_command_timeout

    def test_unknown_placeholder_rejected(self, tmp_path, monkeypatch) -> None:
        (tmp_path / "foo.yaml").write_text(
            "commands:\n"
            "  x:\n"
            "    template: 'show $bar'\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(settings, "nodes_dir", str(tmp_path))
        clear_profile_cache()
        try:
            with pytest.raises(NodeError):
                load_profile("foo")
        finally:
            clear_profile_cache()

    def test_missing_params_for_declared_placeholder_not_allowed(self, tmp_path, monkeypatch) -> None:
        # placeholder $ip declarado sem params → também rejeitado (não é auto)
        (tmp_path / "foo.yaml").write_text(
            "commands:\n"
            "  p:\n"
            "    template: 'ping $ip'\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(settings, "nodes_dir", str(tmp_path))
        clear_profile_cache()
        try:
            with pytest.raises(NodeError):
                load_profile("foo")
        finally:
            clear_profile_cache()