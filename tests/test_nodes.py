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
            "bgp-route",
            "bgp-as-path",
            "bgp-community",
            "ping",
            "traceroute",
        }
        ping = profile.commands["ping"]
        assert ping.template_for("ipv4") == "ping $ip repeat 5 source $source_address"
        assert ping.template_for("ipv6") == (
            "ping ipv6 $ip repeat 5 source $source_address"
        )
        assert ping.params["ip"].type == "destination"
        assert ping.timeout == 60
        assert ping.parser == "ping"
        assert profile.commands["bgp-route"].params["prefix"].type == "prefix"
        assert profile.commands["bgp-route"].parser == "bgp_prefix"
        assert profile.commands["bgp-community"].parser == "bgp_community"
        traceroute = profile.commands["traceroute"]
        assert traceroute.template_for("ipv4") == (
            "traceroute $ip timeout 1 probe 2 source $source_address"
        )
        assert traceroute.params["ip"].type == "destination"
        assert traceroute.parser == "traceroute"
        as_path = profile.commands["bgp-as-path"]
        assert as_path.template_for("ipv4") == (
            "show bgp ipv4 unicast quote-regexp $as_path_regexp"
        )
        assert as_path.template_for("ipv6") == (
            "show bgp ipv6 unicast quote-regexp $as_path_regexp"
        )
        assert as_path.params["asn"].type == "asn"
        assert as_path.asn_regexp == '"({asns})"'
        assert as_path.timeout == 300
        assert as_path.parser == "bgp_as_path"
        community = profile.commands["bgp-community"]
        assert community.template_for("ipv4") == (
            "show bgp ipv4 unicast community $community"
        )
        assert community.params["community"].type == "community"
        assert community.timeout == 300


class TestLoadHuaweiProfile:
    def test_loads_huawei_vrp(self) -> None:
        profile = load_profile("huawei_vrp")
        assert profile.nos == "huawei_vrp"
        # o VRP quer endereço e máscara separados, não CIDR
        assert profile.commands["bgp-route"].template_for("ipv4") == (
            "display bgp routing-table $prefix_address $prefix_length"
        )
        assert profile.commands["bgp-route"].template_for("ipv6") == (
            "display bgp ipv6 routing-table $prefix_address $prefix_length"
        )
        # regex sem aspas: no VRP a aspa vira caractere do padrão
        as_path = profile.commands["bgp-as-path"]
        assert as_path.asn_regexp == "{asn}"
        assert as_path.template_for("ipv4") == (
            "display bgp routing-table regular-expression $as_path_regexp"
        )
        assert as_path.timeout == 300
        assert profile.commands["ping"].template_for("ipv4") == (
            "ping -c 5 -a $source_address $ip"
        )
        assert profile.commands["ping"].template_for("ipv6") == (
            "ping ipv6 -c 5 -a $source_address $ip"
        )
        assert profile.commands["traceroute"].template_for("ipv4") == (
            "tracert -q 2 -f 1 -a $source_address $ip"
        )
        assert profile.commands["bgp-community"].template_for("ipv6") == (
            "display bgp ipv6 routing-table community $community"
        )

    def test_huawei_uses_only_vrp_proven_parsers(self) -> None:
        # Todo comando do perfil tem implementação validada contra saída real do
        # VRP, então o dispatch escolhe o ramo certo a partir do formato da
        # saída. O que segue vale é a outra direção: nenhum parser declarado
        # aqui pode ser um que só foi validado contra o IOS, porque uma
        # consulta do VRP nesse comando voltaria `parsed: null`.
        profile = load_profile("huawei_vrp")
        declared = {c.parser for c in profile.commands.values() if c.parser}
        assert declared == {
            "bgp_as_path",
            "bgp_prefix",
            "bgp_community",
            "ping",
            "traceroute",
        }
        # Nenhum comando do perfil VRP pode ficar sem parser: seria o caminho
        # para a consulta voltar como texto cru na tela.
        sem_parser = [
            label
            for label, c in profile.commands.items()
            if not c.parser
        ]
        assert sem_parser == []

    def test_cisco_parsers_are_ios_only(self) -> None:
        profile = load_profile("cisco_ios")
        assert profile.commands["ping"].parser == "ping"
        assert profile.commands["bgp-route"].parser == "bgp_prefix"
        assert profile.commands["bgp-community"].parser == "bgp_community"

    def test_missing_profile_raises(self) -> None:
        with pytest.raises(NodeError):
            load_profile("mikrotik")

    def test_unknown_parser_rejected(self, tmp_path, monkeypatch) -> None:
        (tmp_path / "foo.yaml").write_text(
            "commands:\n"
            "  p:\n"
            "    template: 'show ip route'\n"
            "    parser: nao-existe\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(settings, "nodes_dir", str(tmp_path))
        clear_profile_cache()
        try:
            with pytest.raises(NodeError):
                load_profile("foo")
        finally:
            clear_profile_cache()

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