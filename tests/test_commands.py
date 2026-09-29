import pytest

from openglass.commands import (
    CommandNotAllowedError,
    build_command,
    list_commands,
)
from openglass.config import settings
from openglass.inventory import Device
from openglass.nodes import NodeError, clear_profile_cache, load_profile
from openglass.security import ReservedAsnError, ReservedRangeError, SecurityError

DEST = {"ip": "8.8.8.8"}


def _device(source4="192.0.2.10", source6="2001:db8::1", vrfs=True, asn=None) -> Device:
    return Device(
        name="r1",
        address="192.0.2.1",
        nos="cisco_ios",
        asn=asn,
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
            "bgp-as-path",
            "bgp-community",
            "ping",
            "traceroute",
        }


class TestBuildCommand:
    def test_simple_command_with_param(self) -> None:
        spec, command = build_command("cisco_ios", "bgp-route", {"prefix": "8.8.8.0/24"})
        assert command == "show bgp ipv4 unicast 8.8.8.0/24 | exclude pathid:|Epoch"
        assert spec.timeout == 120

    def test_command_timeout_from_profile(self) -> None:
        spec, _ = build_command("cisco_ios", "traceroute", {"ip": "1.1.1.1"})
        assert spec.effective_timeout() == 90

    def test_ping_without_source_from_vrf(self) -> None:
        _, command = build_command("cisco_ios", "ping", DEST)
        assert command == "ping 8.8.8.8 repeat 5"

    def test_ping_uses_ipv4_source_from_vrf(self) -> None:
        _, command = build_command("cisco_ios", "ping", DEST, device=_device())
        assert command == "ping 8.8.8.8 repeat 5 source 192.0.2.10"

    def test_ping_ipv6_uses_ipv6_source(self) -> None:
        device = _device()
        _, command = build_command(
            "cisco_ios", "ping", {"ip": "2001:4860:4860::8888"}, device=device
        )
        assert command == (
            "ping ipv6 2001:4860:4860::8888 repeat 5 source 2001:db8::1"
        )

    def test_ping_without_configured_source(self) -> None:
        _, command = build_command("cisco_ios", "ping", DEST, device=_device(source4=None, source6=None))
        assert command == "ping 8.8.8.8 repeat 5"

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

    @pytest.mark.parametrize(
        "label,params",
        [
            # registro IPv4 de uso especial (RFC 6890)
            ("ping", {"ip": "0.0.0.0"}),
            ("traceroute", {"ip": "192.0.0.9"}),
            ("bgp-route", {"prefix": "192.0.0.0/24"}),
            # benchmarking: o /15 inteiro, inclusive subnet mais largo que ele
            ("ping", {"ip": "198.18.0.1"}),
            ("bgp-route", {"prefix": "198.18.0.0/24"}),
            ("bgp-route", {"prefix": "198.19.0.0/16"}),
            # reservado (240/4) e limited broadcast
            ("ping", {"ip": "240.0.0.1"}),
            ("ping", {"ip": "255.255.255.255"}),
            # 6to4 relay anycast, alocacao terminada em 2015-03 (RFC 7526)
            ("ping", {"ip": "192.88.99.1"}),
        ],
    )
    def test_special_purpose_range_rejected(
        self, label: str, params: dict[str, str]
    ) -> None:
        with pytest.raises(ReservedRangeError):
            build_command("cisco_ios", label, params, device=_device())

    @pytest.mark.parametrize(
        "value",
        [
            # Globally Reachable = TRUE no registro: anycast público real, e
            # consultar anycast é o uso legítimo de um looking glass.
            "192.31.196.1",
            "192.52.193.1",
            "192.175.48.1",
            # bordas fora das faixas reservadas
            "198.20.0.1",
            "198.17.255.254",
            "9.9.9.9",
        ],
    )
    def test_globally_reachable_special_use_still_allowed(self, value: str) -> None:
        assert build_command("cisco_ios", "ping", {"ip": value}, device=_device())

    @pytest.mark.parametrize("value", ["198.17.0.0/16", "198.20.0.0/16", "8.8.8.0/24"])
    def test_prefix_outside_benchmarking_allowed(self, value: str) -> None:
        assert build_command("cisco_ios", "bgp-route", {"prefix": value})

    def test_limited_broadcast_reports_specific_range(self) -> None:
        # 255.255.255.255/32 precisa ser consultado antes de 240.0.0.0/4 para
        # o alerta citar a faixa específica, não o /4 que a contém.
        with pytest.raises(ReservedRangeError) as info:
            build_command("cisco_ios", "ping", {"ip": "255.255.255.255"}, device=_device())
        assert info.value.network == "255.255.255.255/32"
        assert info.value.rfc == "RFC 8190"

    def test_unknown_nos_profile_rejected(self) -> None:
        with pytest.raises(Exception):
            build_command("mikrotik", "ping", DEST)


class TestAsPathCommand:
    def test_regex_built_from_asn(self) -> None:
        _, command = build_command("cisco_ios", "bgp-as-path", {"asn": "13335"})
        assert command == 'show bgp ipv4 unicast quote-regexp "(_13335_)"'

    def test_as_path_matches_as_inside_path_not_anchor(self) -> None:
        # "_15169_" casa 15169 no meio do caminho, sem depender de âncora
        _, command = build_command("cisco_ios", "bgp-as-path", {"asn": "15169"})
        assert 'quote-regexp "(_15169_)"' in command

    @pytest.mark.parametrize(
        "value,expected",
        [
            ("3356,15169", "(_3356_|_15169_)"),
            ("3356 15169", "(_3356_|_15169_)"),
            ("3356, 15169 , 13335", "(_3356_|_15169_|_13335_)"),
            ("0", "(_0_)"),
            ("4294967295", "(_4294967295_)"),
        ],
    )
    def test_multiple_asn_becomes_alternation(
        self, value: str, expected: str
    ) -> None:
        _, command = build_command("cisco_ios", "bgp-as-path", {"asn": value})
        assert f'quote-regexp "{expected}"' in command

    def test_asn_uppercase_16bit_ok(self) -> None:
        _, command = build_command("cisco_ios", "bgp-as-path", {"asn": "65535"})
        assert 'quote-regexp "(_65535_)"' in command

    @pytest.mark.parametrize(
        "value",
        [
            "",
            "abc",
            "3356; reboot",
            "3356 | include x",
            "3356 & clear ip bgp *",
            "3356\nshow running-config",
            "_3356_",
            "^(3356)$",
            "3356..13335",
            "3356_13335",
            "1e5",
            "0x10",
            "-1",
            "99999999999",
        ],
    )
    def test_invalid_asn_rejected(self, value: str) -> None:
        with pytest.raises(SecurityError):
            build_command("cisco_ios", "bgp-as-path", {"asn": value})

    def test_asn_list_length_limited(self) -> None:
        with pytest.raises(SecurityError):
            build_command("cisco_ios", "bgp-as-path", {"asn": ",".join(["1"] * 11)})

    def test_missing_asn_rejected(self) -> None:
        with pytest.raises(SecurityError):
            build_command("cisco_ios", "bgp-as-path")

    def test_4byte_asn_at_max_accepted(self) -> None:
        _, command = build_command("cisco_ios", "bgp-as-path", {"asn": "4294967295"})
        assert 'quote-regexp "(_4294967295_)"' in command

    @pytest.mark.parametrize(
        "value",
        [
            # RFC 6996: 16 bits privados, extremos inclusivos
            "64512",
            "64513",
            "65100",
            "65534",
            # RFC 6996: 32 bits privados
            "4200000000",
            "4294967294",
            # dentro de uma lista
            "15169,64512",
            "64512,15169",
        ],
    )
    def test_private_asn_rejected(self, value: str) -> None:
        with pytest.raises(ReservedAsnError):
            build_command("cisco_ios", "bgp-as-path", {"asn": value})

    @pytest.mark.parametrize("value", ["64511", "65535", "15169", "4294967295"])
    def test_asn_just_outside_private_range_accepted(self, value: str) -> None:
        assert build_command("cisco_ios", "bgp-as-path", {"asn": value})

    def test_private_asn_error_names_range(self) -> None:
        with pytest.raises(ReservedAsnError) as info:
            build_command("cisco_ios", "bgp-as-path", {"asn": "65000"})
        err = info.value
        assert (err.rfc, err.start, err.end) == ("RFC 6996", 64512, 65534)
        assert "64512-65534" in str(err)

    def test_private_asn_blocked_on_vrp_too(self) -> None:
        # a barreira é do parâmetro, não do NOS: os dois perfis são barrados
        with pytest.raises(ReservedAsnError):
            build_command("huawei_vrp", "bgp-as-path", {"asn": "64512"})

    def test_private_asn_rejected_before_connecting(self, tmp_path, monkeypatch) -> None:
        # o erro é de validação: nenhuma sessão SSH pode ser aberta antes
        (tmp_path / "fake.yaml").write_text(
            "commands:\n"
            "  thing:\n"
            "    params:\n"
            "      asn:\n"
            "        type: asn\n"
            "    template: 'do-thing $asn'\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(settings, "nodes_dir", str(tmp_path))
        clear_profile_cache()
        try:
            with pytest.raises(ReservedAsnError):
                build_command("fake", "thing", {"asn": "65000"})
        finally:
            clear_profile_cache()


class TestAsnRegexpPerNos:
    """A gramática de AS Path muda por NOS: o IOS aceita alternância, o VRP não."""

    def test_huawei_builds_single_as_regex(self) -> None:
        _, command = build_command("huawei_vrp", "bgp-as-path", {"asn": "3356"})
        assert command == (
            "display bgp routing-table regular-expression _3356_"
        )

    def test_huawei_regex_has_no_quotes(self) -> None:
        # aspas viram caractere do padrão no VRP e a consulta volta vazia
        _, command = build_command("huawei_vrp", "bgp-as-path", {"asn": "3356"})
        assert '"' not in command

    def test_huawei_rejects_multiple_as_with_clear_message(self) -> None:
        with pytest.raises(SecurityError) as info:
            build_command("huawei_vrp", "bgp-as-path", {"asn": "3356,15169"})
        assert "apenas um AS" in str(info.value)

    def test_cisco_still_supports_alternation(self) -> None:
        _, command = build_command("cisco_ios", "bgp-as-path", {"asn": "3356,15169"})
        assert 'quote-regexp "(_3356_|_15169_)"' in command

    def test_command_without_asn_regexp_rejects_placeholder(
        self, tmp_path, monkeypatch
    ) -> None:
        (tmp_path / "fake.yaml").write_text(
            "commands:\n"
            "  thing:\n"
            "    params:\n"
            "      asn:\n"
            "        type: asn\n"
            "    template: 'do-thing $as_path_regexp'\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(settings, "nodes_dir", str(tmp_path))
        clear_profile_cache()
        try:
            with pytest.raises(SecurityError) as info:
                build_command("fake", "thing", {"asn": "3356"})
            assert "asn_regexp" in str(info.value)
        finally:
            clear_profile_cache()


class TestPrefixPlaceholders:
    def test_huawei_prefix_split_into_address_and_length(self) -> None:
        _, command = build_command(
            "huawei_vrp", "bgp-route", {"prefix": "1.1.1.0/24"}
        )
        assert command == "display bgp routing-table 1.1.1.0 24"

    def test_huawei_ipv6_prefix_split(self) -> None:
        _, command = build_command(
            "huawei_vrp", "bgp-route", {"prefix": "2001:4860::/32"}
        )
        assert command == "display bgp ipv6 routing-table 2001:4860:: 32"

    def test_host_address_becomes_full_length_prefix(self) -> None:
        _, command = build_command("huawei_vrp", "bgp-route", {"prefix": "8.8.8.8/32"})
        assert command == "display bgp routing-table 8.8.8.8 32"

    def test_cisco_still_uses_single_cidr_placeholder(self) -> None:
        _, command = build_command("cisco_ios", "bgp-route", {"prefix": "1.1.1.0/24"})
        assert command.startswith("show bgp ipv4 unicast 1.1.1.0/24")


class TestOwnAsRejected:
    def test_querying_own_as_is_refused(self) -> None:
        device = _device(asn=265269)
        with pytest.raises(SecurityError) as info:
            build_command(
                "cisco_ios", "bgp-as-path", {"asn": "265269"}, device=device
            )
        assert "próprio AS" in str(info.value)

    def test_own_as_inside_list_is_refused(self) -> None:
        device = _device(asn=265269)
        with pytest.raises(SecurityError):
            build_command(
                "cisco_ios", "bgp-as-path", {"asn": "15169,265269"}, device=device
            )

    def test_third_party_as_allowed_on_device_with_own_as(self) -> None:
        device = _device(asn=265269)
        _, command = build_command(
            "cisco_ios", "bgp-as-path", {"asn": "13335"}, device=device
        )
        assert 'quote-regexp "(_13335_)"' in command

    def test_allowed_when_device_has_no_asn(self) -> None:
        device = _device()
        assert device.asn is None
        _, command = build_command(
            "cisco_ios", "bgp-as-path", {"asn": "265269"}, device=device
        )
        assert 'quote-regexp "(_265269_)"' in command


class TestFamilyTemplates:
    @pytest.mark.parametrize(
        "params,expected",
        [
            ({"prefix": "8.8.8.0/24"}, "show bgp ipv4 unicast 8.8.8.0/24"),
            ({"prefix": "2001:4860::/32"}, "show bgp ipv6 unicast 2001:4860::/32"),
        ],
    )
    def test_variant_chosen_by_destination_family(
        self, params: dict[str, str], expected: str
    ) -> None:
        _, command = build_command("cisco_ios", "bgp-route", params)
        assert command.startswith(expected)

    def test_community_colons_do_not_imply_ipv6(self) -> None:
        # "65000:666" tem ":" mas não é endereço → não pode virar variante v6
        _, command = build_command(
            "cisco_ios", "bgp-community", {"community": "65000:666"}
        )
        assert command == "show bgp ipv4 unicast community 65000:666"

    def test_traceroute_ipv6_variant(self) -> None:
        _, command = build_command(
            "cisco_ios", "traceroute", {"ip": "2606:4700::1111"}, device=_device()
        )
        assert command == (
            "traceroute ipv6 2606:4700::1111 timeout 1 probe 2 source 2001:db8::1"
        )

    def test_missing_family_variant_rejected(self, tmp_path, monkeypatch) -> None:
        (tmp_path / "nos.yaml").write_text(
            "commands:\n"
            "  p:\n"
            "    params:\n"
            "      ip:\n"
            "        type: destination\n"
            "    template:\n"
            "      ipv4: 'ping $ip'\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(settings, "nodes_dir", str(tmp_path))
        clear_profile_cache()
        try:
            with pytest.raises(SecurityError) as info:
                build_command("nos", "p", {"ip": "2001:4860::1"})
            assert "ipv6" in str(info.value)
        finally:
            clear_profile_cache()

    def test_unknown_family_key_rejected(self, tmp_path, monkeypatch) -> None:
        (tmp_path / "nos.yaml").write_text(
            "commands:\n"
            "  p:\n"
            "    template:\n"
            "      ipv5: 'ping $ip'\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(settings, "nodes_dir", str(tmp_path))
        clear_profile_cache()
        try:
            with pytest.raises(NodeError):
                load_profile("nos")
        finally:
            clear_profile_cache()

    def test_placeholder_checked_in_every_variant(self, tmp_path, monkeypatch) -> None:
        (tmp_path / "nos.yaml").write_text(
            "commands:\n"
            "  p:\n"
            "    template:\n"
            "      ipv4: 'ping $ip'\n"
            "      ipv6: 'ping6 $nao_declarado'\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(settings, "nodes_dir", str(tmp_path))
        clear_profile_cache()
        try:
            with pytest.raises(NodeError):
                load_profile("nos")
        finally:
            clear_profile_cache()


class TestCommunityCommand:
    @pytest.mark.parametrize(
        "value,expected",
        [
            ("65000:666", "show bgp ipv4 unicast community 65000:666"),
            ("64512:100", "show bgp ipv4 unicast community 64512:100"),
            ("0:0", "show bgp ipv4 unicast community 0:0"),
            ("65535:65535", "show bgp ipv4 unicast community 65535:65535"),
            ("no-export", "show bgp ipv4 unicast community no-export"),
            ("internet", "show bgp ipv4 unicast community internet"),
            (
                "65000:666 64512:100",
                "show bgp ipv4 unicast community 65000:666 64512:100",
            ),
        ],
    )
    def test_valid_community(self, value: str, expected: str) -> None:
        _, command = build_command("cisco_ios", "bgp-community", {"community": value})
        assert command == expected

    @pytest.mark.parametrize(
        "value",
        [
            "65000:666; reboot",
            "65000:666 | show run",
            "65000:666 | include x",
            "no-export`id`",
            "65000:666\nshow run",
            "^(65000|131072):666$",
            "",
        ],
    )
    def test_invalid_community(self, value: str) -> None:
        with pytest.raises(SecurityError):
            build_command("cisco_ios", "bgp-community", {"community": value})

    @pytest.mark.parametrize("value", ["65536:666", "65000:65536", "99999:1"])
    def test_community_out_of_16bit_range_rejected(self, value: str) -> None:
        # o IOS rejeitaria com "Invalid input"; aqui falha antes, no cliente
        with pytest.raises(SecurityError) as info:
            build_command("cisco_ios", "bgp-community", {"community": value})
        assert "65535" in str(info.value)


class TestAdminTemplateTrust:
    """O template é config do admin: pipes/aspas passam, `;` não."""

    def test_pipe_and_quotes_allowed_in_template(self, tmp_path, monkeypatch) -> None:
        (tmp_path / "nos.yaml").write_text(
            "commands:\n"
            "  p:\n"
            "    params:\n"
            "      ip:\n"
            "        type: destination\n"
            '    template: \'vtysh -c "show ip route $ip" | include $ip\'\n',
            encoding="utf-8",
        )
        monkeypatch.setattr(settings, "nodes_dir", str(tmp_path))
        clear_profile_cache()
        try:
            _, command = build_command("nos", "p", {"ip": "8.8.8.8"})
            assert command == 'vtysh -c "show ip route 8.8.8.8" | include 8.8.8.8'
        finally:
            clear_profile_cache()

    def test_command_chaining_still_blocked(self, tmp_path, monkeypatch) -> None:
        (tmp_path / "nos.yaml").write_text(
            "commands:\n"
            "  p:\n"
            "    template: 'show ip route; reboot'\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(settings, "nodes_dir", str(tmp_path))
        clear_profile_cache()
        try:
            with pytest.raises(SecurityError):
                build_command("nos", "p")
        finally:
            clear_profile_cache()

    def test_param_cannot_smuggle_a_pipe(self, tmp_path, monkeypatch) -> None:
        # mesmo com template liberado, o parâmetro do usuário segue estrito
        (tmp_path / "nos.yaml").write_text(
            "commands:\n"
            "  p:\n"
            "    params:\n"
            "      ip:\n"
            "        type: destination\n"
            '    template: \'vtysh -c "show ip route $ip" | include $ip\'\n',
            encoding="utf-8",
        )
        monkeypatch.setattr(settings, "nodes_dir", str(tmp_path))
        clear_profile_cache()
        try:
            with pytest.raises(SecurityError):
                build_command("nos", "p", {"ip": "8.8.8.8 | show run"})
        finally:
            clear_profile_cache()


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
        assert session.captured == ["ping 8.8.8.8 repeat 5 source 192.0.2.10"]
        assert result.raw == self.PING_OUTPUT
        assert result.command == "ping 8.8.8.8 repeat 5 source 192.0.2.10"
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