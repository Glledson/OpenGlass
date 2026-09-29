"""Testes dos parsers do Huawei VRP: `ping`, `traceroute`, `bgp_prefix` e `bgp_community`.

Toda fixture é saída real de um NetEngine 8000 (VRP 8.200), não amostra
escrita à mão: o formato do VRP tem detalhes que só aparecem no equipamento —
`Request time out` por sonda no ping, endereço repetido por sonda quando há
caminho alternativo no tracert, e o bloco de cada caminho do BGP aberto pelo
`From:`. As saídas do IOS continuam nos testes de `test_parsers.py`,
`test_traceroute.py` e `test_bgp_prefix.py`; aqui o que se checa é que o
dispatch escolhe o ramo certo e que nenhum dos dois NOS se contaminou.

O guard de drift no fim do arquivo amarra as fixtures ao equipamento: sem ele,
um formato do VRP que mudasse passaria despercebido até alguém rodar contra um
VRP novo e receber `parsed: null`.
"""

import json
from pathlib import Path

import pytest

from openglass.parsers import PARSER_NAMES, parse
from openglass.parsers.base import ParserError
from openglass.parsers.dispatch import (
    _VRP_BGP_COMMUNITY_RE,
    _VRP_BGP_PREFIX_RE,
    _VRP_PING_RE,
    _VRP_TRACERT_RE,
)
from openglass.parsers.vrp_bgp_prefix import _ENTRY_RE, _NOT_IN_TABLE_RE
from openglass.parsers.vrp_bgp_prefix import parse_bgp_prefix_vrp
from openglass.parsers.vrp_ping import parse_ping_vrp
from openglass.parsers.vrp_traceroute import parse_traceroute_vrp

FIXTURES = Path(__file__).parent / "fixtures"
JS_FIXTURES = Path(__file__).parent / "js" / "fixtures"
PING = (FIXTURES / "huawei_vrp_ping.txt").read_text()
PING_LOSS = (FIXTURES / "huawei_vrp_ping_loss.txt").read_text()
TRACERT = (FIXTURES / "huawei_vrp_traceroute.txt").read_text()
TRACERT_STAR = (FIXTURES / "huawei_vrp_traceroute_star.txt").read_text()
ROUTE = (FIXTURES / "huawei_vrp_bgp_route.txt").read_text()
ROUTE_MULTI = (FIXTURES / "huawei_vrp_bgp_route_multi.txt").read_text()
ROUTE_AGGR = (FIXTURES / "huawei_vrp_bgp_route_aggr.txt").read_text()
NOT_FOUND = (FIXTURES / "huawei_vrp_bgp_route_notfound.txt").read_text()
COMMUNITY = (FIXTURES / "huawei_vrp_bgp_community.txt").read_text()
COMMUNITY_EMPTY = (FIXTURES / "huawei_vrp_bgp_community_empty.txt").read_text()

ALL_VRP = [
    PING, PING_LOSS, TRACERT, TRACERT_STAR, ROUTE, ROUTE_MULTI, ROUTE_AGGR, NOT_FOUND
]


class TestRegistration:
    def test_vrp_commands_declare_parsers(self) -> None:
        from openglass.nodes import load_profile

        profile = load_profile("huawei_vrp")
        assert profile.commands["ping"].parser == "ping"
        assert profile.commands["traceroute"].parser == "traceroute"
        assert profile.commands["bgp-route"].parser == "bgp_prefix"
        for name in ("ping", "traceroute", "bgp_prefix"):
            assert name in PARSER_NAMES


class TestDispatch:
    """A decisão de formato não pode vazar para o outro NOS."""

    def test_each_vrp_fixture_matches_exactly_its_signature(self) -> None:
        # a assinatura que decide o ramo tem de estar na saída do comando
        # correspondente e em nenhuma outra: se a do ping casasse com a do
        # bgp-route, a consulta cairia no parser errado
        esperado = [
            (PING, _VRP_PING_RE),
            (PING_LOSS, _VRP_PING_RE),
            (TRACERT, _VRP_TRACERT_RE),
            (TRACERT_STAR, _VRP_TRACERT_RE),
            (ROUTE, _VRP_BGP_PREFIX_RE),
            (ROUTE_MULTI, _VRP_BGP_PREFIX_RE),
            (ROUTE_AGGR, _VRP_BGP_PREFIX_RE),
        ]
        assinaturas = [_VRP_PING_RE, _VRP_TRACERT_RE, _VRP_BGP_PREFIX_RE]
        for raw, assinatura in esperado:
            for outra in assinaturas:
                casou = outra.search(raw) is not None
                assert casou == (outra is assinatura), outra.pattern

    def test_ios_ping_is_not_read_as_vrp(self) -> None:
        # 5 packets transmitted é do IOS; o VRP diz "packet(s)". Sem o
        # parêntese a assinatura seria ambígua e o ping do IOS cairia no ramo
        # errado, perdendo o bloco `!!!!!`
        ios = (
            "Type escape sequence to abort.\n"
            "Sending 5, 100-byte ICMP Echos to 8.8.8.8, timeout is 2 seconds:\n"
            "!!!!!\n"
            "Success rate is 100 percent (5/5)\n"
        )
        assert _VRP_PING_RE.search(ios) is None
        assert parse("ping", ios)["sent"] == 5

    def test_ios_traceroute_is_not_read_as_vrp(self) -> None:
        # o IOS anuncia `Tracing the route to X` e decora o salto com `[AS N]`;
        # o VRP anuncia `traceroute to X(X), max hops:`. Uma assinatura que
        # aceitasse os dois prefixos leria o IOS com o parser do VRP
        ios = (
            "edge.jnet#traceroute 1.1.1.1 source 45.5.40.255 \n"
            "Type escape sequence to abort.\n"
            "Tracing the route to 1.1.1.1\n"
            "VRF info: (vrf in name/id, vrf out name/id)\n"
            "  1 10.0.0.241 [AS 265269] 40 msec 19 msec 6 msec\n"
        )
        assert _VRP_TRACERT_RE.search(ios) is None
        parsed = parse("traceroute", ios)
        assert parsed["hops"][0]["asn"] == 265269
        assert parsed["hops"][0]["ip"] == "10.0.0.241"

    def test_ios_bgp_prefix_is_not_read_as_vrp(self) -> None:
        # o IOS abre o caminho com `from <nh> (<router id>)`; o VRP abre com
        # `From: <nh> (<router id>)`. A distinção é o cabeçalho da entrada:
        # "BGP routing table entry for ..., version" contra "entry information
        # of". Sem isso os dois blocos se confundem
        ios = (
            "BGP routing table entry for 8.8.8.0/24, version 100803950\n"
            "Paths: (2 available, best #1, table default)\n"
            "  Not advertised to any peer\n"
            "  Refresh Epoch 1\n"
            "  263009 15169\n"
            "    10.200.210.129 from 10.200.210.129 (170.84.55.250)\n"
            "      Origin IGP, localpref 150, valid, external, best\n"
        )
        assert _VRP_BGP_PREFIX_RE.search(ios) is None
        parsed = parse("bgp_prefix", ios)
        assert parsed["version"] == 100803950
        assert parsed["table"] == "default"
        assert parsed["paths"][0]["next_hop"] == "10.200.210.129"

    def test_ios_bgp_not_found_is_not_read_as_vrp(self) -> None:
        # os dois NOS avisam prefixo ausente, cada um com seu texto: `% Network
        # not in table` no IOS, `Info: The network does not exist.` no VRP. Uma
        # assinatura que aceitasse os dois daria a um a resposta do outro
        ios = "% Network not in table\n"
        assert _VRP_BGP_PREFIX_RE.search(ios) is None
        parsed = parse("bgp_prefix", ios, {"prefix": "9.9.9.0/24"})
        assert parsed["status"] == "not_found"
        assert parsed["prefix"] == "9.9.9.0/24"

    def test_vrp_not_found_goes_through_dispatch(self) -> None:
        # a resposta de prefixo ausente não tem cabeçalho de entrada, então é a
        # assinatura que tem de levá-la ao parser do VRP. Sem isso a consulta
        # vazia cairia no IOS e viraria texto cru na tela
        parsed = parse("bgp_prefix", NOT_FOUND, {"prefix": "45.0.0.0/24"})
        assert parsed["nos"] == "huawei_vrp"
        assert parsed["status"] == "not_found"
        assert parsed["prefix"] == "45.0.0.0/24"

    def test_dispatch_routes_each_vrp_fixture(self) -> None:
        esperado = {
            "ping": [PING, PING_LOSS],
            "traceroute": [TRACERT, TRACERT_STAR],
            "bgp_prefix": [ROUTE, ROUTE_MULTI, ROUTE_AGGR],
        }
        for nome, raws in esperado.items():
            for raw in raws:
                parsed = parse(nome, raw)
                assert parsed["nos"] == "huawei_vrp"
                assert parsed["type"] == nome


class TestPingVrp:
    def test_success(self) -> None:
        p = parse_ping_vrp(PING)
        assert p["status"] == "success"
        assert p["target"] == "8.8.8.8"
        assert (p["sent"], p["received"]) == (5, 5)
        assert p["loss_percent"] == 0.0
        assert p["success_percent"] == 100.0
        assert p["size_bytes"] == 56
        assert p["rtt_ms"] == {"min": 38, "avg": 38, "max": 38}

    def test_each_reply_carries_ttl_and_time(self) -> None:
        # o IOS só imprime `!!!!!`; o VRP traz o TTL e o tempo de cada sonda, e
        # é isso que distingue uma resposta de um timeout no log bruto
        p = parse_ping_vrp(PING)
        assert len(p["probes"]) == 5
        for probe in p["probes"]:
            assert probe["result"] == "reply"
            assert probe["symbol"] == "!"
            assert probe["ttl"] == 111
            assert 0 < probe["time_ms"] < 1000

    def test_probe_time_comes_from_its_own_line(self) -> None:
        # a captura real teve 38 ms nas cinco sondas, então ela não distingue
        # "tempo da linha" de "tempo do resumo". Aqui os valores divergem de
        # propósito: o tempo da sonda tem de vir da linha dela, e não ser
        # repetido a partir do min/avg/max
        raw = (
            "PING 8.8.8.8: 56  data bytes, press CTRL_C to break\n"
            "  Reply from 8.8.8.8: bytes=56 Sequence=1 ttl=111 time=11 ms\n"
            "  Reply from 8.8.8.8: bytes=56 Sequence=2 ttl=120 time=22 ms\n"
            "  Reply from 8.8.8.8: bytes=56 Sequence=3 ttl=130 time=33 ms\n"
            "  round-trip min/avg/max = 11/22/33 ms\n"
        )
        p = parse_ping_vrp(raw)
        assert [probe["time_ms"] for probe in p["probes"]] == [11, 22, 33]
        assert [probe["ttl"] for probe in p["probes"]] == [111, 120, 130]

    def test_total_loss(self) -> None:
        p = parse_ping_vrp(PING_LOSS)
        assert p["status"] == "failed"
        assert (p["sent"], p["received"]) == (5, 0)
        assert p["loss_percent"] == 100.0
        assert p["rtt_ms"] is None
        assert all(probe["result"] == "timeout" for probe in p["probes"])
        assert len(p["probes"]) == 5

    def test_timeout_probes_have_no_ttl(self) -> None:
        # um timeout não tem TTL nem tempo: inventar os dois mostraria ao
        # operador um número que o equipamento não mediu
        p = parse_ping_vrp(PING_LOSS)
        for probe in p["probes"]:
            assert "ttl" not in probe
            assert "time_ms" not in probe

    def test_source_is_not_fabricated(self) -> None:
        # o `-a` do comando define a origem mas o VRP não a devolve na saída
        p = parse_ping_vrp(PING)
        assert p["source"] is None
        assert p["timeout_s"] is None

    def test_unrecognised_output_raises(self) -> None:
        with pytest.raises(ParserError):
            parse_ping_vrp("Interface GigabitEthernet0/0/1 is up\n")

    def test_partial_loss_computed_when_summary_absent(self) -> None:
        # saída sem o bloco de estatísticas: a perda sai dos próprios probes
        raw = (
            "PING 8.8.8.8: 56  data bytes, press CTRL_C to break\n"
            "  Reply from 8.8.8.8: bytes=56 Sequence=1 ttl=111 time=38 ms\n"
            "  Request time out\n"
            "  Reply from 8.8.8.8: bytes=56 Sequence=3 ttl=111 time=40 ms\n"
        )
        p = parse_ping_vrp(raw)
        assert p["status"] == "partial"
        assert (p["sent"], p["received"]) == (3, 2)
        assert p["loss_percent"] == pytest.approx(33.33, abs=0.01)


class TestTracerouteVrp:
    def test_success(self) -> None:
        t = parse_traceroute_vrp(TRACERT)
        assert t["status"] == "success"
        assert t["destination"] == "8.8.8.8"
        assert t["reachable"] is True
        assert t["max_hops"] == 30
        assert len(t["hops"]) == 6

    def test_hop_shape(self) -> None:
        t = parse_traceroute_vrp(TRACERT)
        hop = t["hops"][0]
        assert hop["hop"] == 1
        assert hop["ip"] == "192.0.2.225"
        assert hop["addresses"] == ["192.0.2.225"]
        assert hop["avg_ms"] == 3.5
        assert [probe["ms"] for probe in hop["times"]] == [4, 3]

    def test_address_per_probe_is_kept(self) -> None:
        # o salto 4 tem um next hop diferente por sonda (EQ-BGP1 / EBR1). Ler só
        # o primeiro endereço, como o IOS permite, perderia o caminho alternativo
        t = parse_traceroute_vrp(TRACERT)
        hop4 = t["hops"][3]
        assert hop4["addresses"] == ["209.85.244.163", "192.178.84.13"]
        assert [probe["ip"] for probe in hop4["times"]] == [
            "209.85.244.163",
            "192.178.84.13",
        ]
        # `ip` continua sendo o primeiro que respondeu, para a card existente
        assert hop4["ip"] == "209.85.244.163"

    def test_addresses_have_no_asn(self) -> None:
        # o VRP não decora o salto com o AS; `None` é informação ausente, não
        # bug do parser
        t = parse_traceroute_vrp(TRACERT)
        assert all(hop["asn"] is None for hop in t["hops"])

    def test_star_timeout(self) -> None:
        t = parse_traceroute_vrp(TRACERT_STAR)
        assert t["status"] == "partial"
        assert t["reachable"] is False
        assert len(t["hops"]) == 30
        silenciosos = [hop for hop in t["hops"] if hop["ip"] is None]
        assert silenciosos, "esperava saltos com *"
        for hop in silenciosos:
            assert hop["addresses"] == []
            assert hop["avg_ms"] is None
            assert all(probe["type"] == "timeout" for probe in hop["times"])

    def test_star_probes_have_no_ms(self) -> None:
        # um `*` não tem tempo medido: o payload omite `ms` em vez de botar 0,
        # que a card renderizaria como "0 ms" e pareceria resposta instantânea
        t = parse_traceroute_vrp(TRACERT_STAR)
        for hop in t["hops"]:
            for probe in hop["times"]:
                if probe["type"] == "timeout":
                    assert "ms" not in probe
                    assert probe["ip"] is None

    def test_partial_hop_keeps_the_answers(self) -> None:
        # um salto em que só uma sonda responde carrega a resposta e o timeout
        raw = (
            "traceroute to 8.8.8.8(8.8.8.8), max hops: 30, packet length: 40\n"
            "  1 10.0.0.1 4 ms  *\n"
        )
        t = parse_traceroute_vrp(raw)
        hop = t["hops"][0]
        assert [probe["type"] for probe in hop["times"]] == ["reply", "timeout"]
        assert hop["avg_ms"] == 4.0
        assert hop["addresses"] == ["10.0.0.1"]

    def test_unrecognised_output_raises(self) -> None:
        with pytest.raises(ParserError):
            parse_traceroute_vrp("Error: command not found\n")

    def test_crlf_output(self) -> None:
        t = parse_traceroute_vrp(TRACERT.replace("\n", "\r\n"))
        assert t["status"] == "success"
        assert t["hops"][0]["ip"] == "192.0.2.225"


class TestBgpPrefixVrp:
    def test_single_path(self) -> None:
        b = parse_bgp_prefix_vrp(ROUTE)
        assert b["status"] == "ok"
        assert b["prefix"] == "187.76.192.104/29"
        assert b["available"] == 1
        assert b["best_index"] == 1
        assert b["local_as"] == "644960"
        assert b["router_id"] == "10.0.0.1"
        assert len(b["paths"]) == 1

    def test_path_fields(self) -> None:
        b = parse_bgp_prefix_vrp(ROUTE)
        path = b["paths"][0]
        assert path["as_path"] == ["644970", "7738", "65010", "64512"]
        assert path["next_hop"] == "192.0.2.221"
        assert path["originator"] == "10.0.0.253"
        assert path["origin"] == "IGP"
        assert path["localpref"] == 150
        assert path["out_interface"] == "Eth-Trunk0.2031"
        assert path["duration"] == "13d08h10m26s"
        assert path["communities"] == ["65001:1000", "65001:1100", "65001:1300"]
        assert path["best"] is True

    def test_pref_val_is_not_metric(self) -> None:
        # pref-val é a preferência do VRP (maior melhor), não a métrica do IOS.
        # Sobrescrever `metric` faria a card comparar coisas incompatíveis
        b = parse_bgp_prefix_vrp(ROUTE)
        path = b["paths"][0]
        assert path["pref_val"] == 0
        assert path["pref_rule"] == 20
        assert path["metric"] is None

    def test_version_and_table_absent_in_vrp(self) -> None:
        b = parse_bgp_prefix_vrp(ROUTE)
        assert b["version"] is None
        assert b["table"] is None

    def test_two_paths_and_not_preferred_reason(self) -> None:
        b = parse_bgp_prefix_vrp(ROUTE_MULTI)
        assert b["prefix"] == "8.8.8.0/24"
        assert b["available"] == 2
        assert b["select"] == 1
        assert b["best_index"] == 1
        assert len(b["paths"]) == 2
        best, other = b["paths"]
        assert best["as_path"] == ["644970", "269194", "15169"]
        assert other["as_path"] == ["644970", "7738", "15169"]
        assert best["best"] is True
        assert other["best"] is False
        # o motivo da não preferência é a informação que o IOS não entrega
        assert other["not_preferred"] == "router ID"
        assert best["not_preferred"] is None

    def test_not_preferred_is_not_repeated_as_flag(self) -> None:
        b = parse_bgp_prefix_vrp(ROUTE_MULTI)
        other = b["paths"][1]
        assert "not preferred for router ID" not in other["flags"]
        assert other["flags"] == ["valid", "external"]

    def test_as_path_does_not_leak_into_flags(self) -> None:
        # o AS path é separado por espaço e os atributos por vírgula, então um
        # corte ingenuo joga os ASNs na lista de flags
        for raw in (ROUTE, ROUTE_MULTI, ROUTE_AGGR):
            b = parse_bgp_prefix_vrp(raw)
            for path in b["paths"]:
                assert not any(flag[0].isdigit() for flag in path["flags"]), path["flags"]
                for flag in path["flags"]:
                    assert flag not in ("origin", "localpref", "pref-val", "pre")

    def test_aggregation(self) -> None:
        b = parse_bgp_prefix_vrp(ROUTE_AGGR)
        for path in b["paths"]:
            assert path["aggregations"] == [
                {"asn": 13335, "router": "162.158.224.1"}
            ]

    def test_communities_per_path(self) -> None:
        b = parse_bgp_prefix_vrp(ROUTE_AGGR)
        assert b["paths"][0]["communities"] == ["65001:1000", "65001:1100", "65001:1400"]
        assert b["paths"][1]["communities"] == ["65001:1000", "65001:1100", "65001:1300"]

    def test_not_advertised(self) -> None:
        # o fixture traz `Not advertised to any peer yet` nos dois caminhos
        assert parse_bgp_prefix_vrp(ROUTE)["advertised"] is False

    def test_prefix_from_context_when_not_in_table(self) -> None:
        # o VRP responde só "Info: The network does not exist.", sem repetir o
        # prefixo: ele vem do contexto da consulta
        b = parse_bgp_prefix_vrp(NOT_FOUND, {"prefix": "45.0.0.0/24"})
        assert b["status"] == "not_found"
        assert b["prefix"] == "45.0.0.0/24"
        assert b["message"] == "Prefixo não está na tabela BGP"
        # o `not_found` do IOS também não traz `paths`; a card trata os dois
        # pelo status antes de olhar a lista
        assert "paths" not in b

    def test_not_found_signature_is_the_real_one(self) -> None:
        # o texto que o VRP devolve é a âncora: sem este casamento o prefixo
        # ausente cairia no ParserError e apareceria como texto cru
        assert _NOT_IN_TABLE_RE.search(NOT_FOUND) is not None
        # e o prefixo ausente não pode ser confundido com uma entrada válida
        assert _ENTRY_RE.search(NOT_FOUND) is None

    def test_unrecognised_output_raises(self) -> None:
        with pytest.raises(ParserError):
            parse_bgp_prefix_vrp("display bgp routing-table\n")

    def test_entry_without_path_raises(self) -> None:
        # cabeçalho reconhecível mas nenhum `From:` é saída truncada: melhor
        # texto cru do que um caminho inventado
        raw = (
            "BGP routing table entry information of 8.8.8.0/24:\n"
            "Paths:   2 available, 1 best, 1 select\n"
        )
        with pytest.raises(ParserError):
            parse_bgp_prefix_vrp(raw)


# --- âncora de formato -----------------------------------------------------
# Se o VRP mudar a saída, o parser deixa de casar e a consulta volta `parsed:
# null` na tela. Cada linha é o comando que produziu a fixture, com a
# `source_address` que a engine preenche a partir do device — ela não vem do
# usuário e muda de device para device, então a âncora é o par comando+destino.
VRP_CAPTURE = {
    "huawei_vrp_ping.txt": "ping -c 5 -a 198.51.100.33 8.8.8.8",
    "huawei_vrp_ping_loss.txt": "ping -c 5 -a 198.51.100.33 192.88.148.1",
    "huawei_vrp_traceroute.txt": "tracert -q 2 -f 1 -a 198.51.100.33 8.8.8.8",
    "huawei_vrp_traceroute_star.txt": "tracert -q 2 -f 1 -a 198.51.100.33 9.9.9.9",
    "huawei_vrp_bgp_route.txt": "display bgp routing-table 187.76.192.104 29",
    "huawei_vrp_bgp_route_multi.txt": "display bgp routing-table 8.8.8.0 24",
    "huawei_vrp_bgp_route_aggr.txt": "display bgp routing-table 1.1.1.0 24",
    "huawei_vrp_bgp_route_notfound.txt": "display bgp routing-table 45.0.0.0 24",
    "huawei_vrp_bgp_community.txt": "display bgp routing-table community 65001:40100",
    "huawei_vrp_bgp_community_empty.txt": "display bgp routing-table community 65000:1",
}


class TestJsPayloadFixture:
    """Os JSONs dos testes de JS não podem divergir do parser.

    JavaScript não roda o parser, então os testes de card carregam o payload
    já estruturado. Se o parser mudar e o arquivo ficar para trás, a card
    testada deixa de ser a card real e o teste passa mentindo — a defasagem
    precisa virar falha de CI.
    """

    def test_matches_parser_output(self) -> None:
        casos = [
            ("ping", PING, None, "vrp_ping.json"),
            ("ping", PING_LOSS, None, "vrp_ping_loss.json"),
            ("traceroute", TRACERT, None, "vrp_traceroute.json"),
            ("traceroute", TRACERT_STAR, None, "vrp_traceroute_star.json"),
            ("bgp_prefix", ROUTE_MULTI, {"prefix": "8.8.8.0/24"}, "vrp_bgp_prefix.json"),
            ("bgp_prefix", ROUTE_AGGR, {"prefix": "1.1.1.0/24"}, "vrp_bgp_prefix_aggr.json"),
            (
                "bgp_prefix",
                NOT_FOUND,
                {"prefix": "45.0.0.0/24"},
                "vrp_bgp_prefix_notfound.json",
            ),
            (
                "bgp_community",
                COMMUNITY,
                {"community": "65001:40100"},
                "vrp_bgp_community.json",
            ),
            (
                "bgp_community",
                COMMUNITY_EMPTY,
                {"community": "65000:1"},
                "vrp_bgp_community_empty.json",
            ),
        ]
        for nome, raw, ctx, arquivo in casos:
            caminho = JS_FIXTURES / arquivo
            if not caminho.exists():  # pragma: no cover - fixture versionada
                pytest.skip(f"fixture de JS ausente: {arquivo}")
            esperado = parse(nome, raw, ctx)
            assert json.loads(caminho.read_text(encoding="utf-8")) == esperado, arquivo

    def test_no_stray_js_fixtures(self) -> None:
        # uma fixture de JS sem par em Python não tem contra o que divergir:
        # o card testada pararia de refletir o equipamento
        no_disco = {p.name for p in JS_FIXTURES.glob("vrp_*.json")}
        assert no_disco == {
            "vrp_ping.json",
            "vrp_ping_loss.json",
            "vrp_traceroute.json",
            "vrp_traceroute_star.json",
            "vrp_bgp_prefix.json",
            "vrp_bgp_prefix_aggr.json",
            "vrp_bgp_prefix_notfound.json",
            "vrp_bgp_community.json",
            "vrp_bgp_community_empty.json",
        }


class TestFixtureProvenance:
    def test_every_vrp_fixture_is_registered(self) -> None:
        # impede a fixture capturada e a âncora de divergirem entre si
        no_disco = {p.name for p in FIXTURES.glob("huawei_vrp_*.txt")}
        # `as_path` é de outro teste (o parser já existia para o VRP)
        assert no_disco - {"huawei_vrp_as_path.txt"} == set(VRP_CAPTURE)


class TestVrpBgpCommunity:
    """Busca por community no VRP (`display bgp routing-table community`).

    Differe do IOS em dois pontos medidos no equipamento: a tabela tem coluna
    `Community` (o IOS não tem) e não tem coluna de AS path (o IOS tem). Ler
    essa saída com o leitor do IOS falha, porque ele exige `Path` no cabeçalho.
    """

    def test_reads_prefixes_from_real_output(self) -> None:
        p = parse("bgp_community", COMMUNITY, {"community": "65001:40100"})
        assert p["type"] == "bgp_community"
        assert p["nos"] == "huawei_vrp"
        assert p["community"] == "65001:40100"
        assert p["total_paths"] == 3
        assert p["total_prefixes"] == 3
        assert p["declared_total"] == 3
        assert p["best_paths"] == 3

    def test_group_is_local_because_there_is_no_as_path(self) -> None:
        # Sem coluna de AS path não há origem para ler; o next hop 0.0.0.0
        # confirma que foram originadas no próprio roteador.
        p = parse("bgp_community", COMMUNITY, {"community": "65001:40100"})
        assert p["origin_count"] == 1
        origin = p["origins"][0]
        assert origin["origin_as"] == "local"
        assert origin["is_local"] is True
        assert origin["next_hops"] == ["0.0.0.0"]

    def test_keeps_every_community_of_each_prefix(self) -> None:
        # a coluna `Community` traz a lista inteira de cada prefixo, não só a
        # consultada — é o contexto de quem segura o prefixo
        p = parse("bgp_community", COMMUNITY, {"community": "65001:40100"})
        by_prefix = {
            e["prefix"]: e for o in p["origins"] for e in o["entries"]
        }
        assert by_prefix["198.51.100.0/23"]["communities"] == [
            "65001:1000",
            "65001:10301",
            "65001:10400",
            "65001:20100",
            "65001:40100",
        ]
        assert by_prefix["198.51.100.0/24"]["communities"] == [
            "65001:1000",
            "65001:40100",
        ]

    def test_unused_community_is_empty_state_not_error(self) -> None:
        # `Total Number of Routes: 0` é resposta legítima: a card mostra o
        # estado vazio em vez de a consulta virar texto cru
        p = parse("bgp_community", COMMUNITY_EMPTY, {"community": "65000:1"})
        assert p["total_paths"] == 0
        assert p["total_prefixes"] == 0
        assert p["declared_total"] == 0
        assert p["origins"] == []
        assert p["origin_count"] == 0

    def test_ios_output_still_goes_to_ios_parser(self) -> None:
        raw = (FIXTURES / "cisco_ios_community_full.txt").read_text()
        p = parse("bgp_community", raw, {"community": "no-export"})
        # o parser do IOS não marca `nos`, e agrupa por AS de origem de verdade
        assert "nos" not in p
        assert p["origin_count"] > 1
        assert p["origins"][0]["origin_as"] != "local"

    def test_ios_entry_has_no_communities_key(self) -> None:
        # o IOS não manda a lista, então a coluna não pode aparecer na card dele
        raw = (FIXTURES / "cisco_ios_community_full.txt").read_text()
        p = parse("bgp_community", raw, {"community": "no-export"})
        for origin in p["origins"]:
            for entry in origin["entries"]:
                assert "communities" not in entry

    def test_signature_ignores_ios_table(self) -> None:
        raw = (FIXTURES / "cisco_ios_community_full.txt").read_text()
        assert _VRP_BGP_COMMUNITY_RE.search(raw) is None

    def test_signature_accepts_vrp_table_and_empty(self) -> None:
        assert _VRP_BGP_COMMUNITY_RE.search(COMMUNITY) is not None
        assert _VRP_BGP_COMMUNITY_RE.search(COMMUNITY_EMPTY) is not None
