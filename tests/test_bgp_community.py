"""Testes do parser de community.

Duas fixtures, ambas saida real de `show bgp ipv4 unicast community no-export` em
ASR1000 IOS-XE 16.09:

- `cisco_ios_community.txt`: recorte de 16 rotas / 8 prefixos / 3 ASs, para as
  asserções miúdas de agrupamento.
- `cisco_ios_community_full.txt`: a captura inteira, 68 rotas / 32 prefixos /
  12 ASs. É a que alimenta o JSON dos testes de JavaScript, então qualquer
  mudança no formato do parser aparece aqui antes de virar card quebrada.
"""

import json
from pathlib import Path

import pytest

from openglass.parsers import PARSER_NAMES, parse
from openglass.parsers.base import ParserError
from openglass.parsers.bgp_community import MAX_NEXT_HOPS, MAX_ORIGINS
from openglass.parsers.bgp_community import parse_bgp_community
from openglass.parsers.bgp_table import MAX_PREFIXES_PER_ORIGIN

FIXTURES = Path(__file__).parent / "fixtures"
IOS = (FIXTURES / "cisco_ios_community.txt").read_text()
IOS_FULL = (FIXTURES / "cisco_ios_community_full.txt").read_text()
PAYLOAD_JS = Path(__file__).parent / "js" / "fixtures" / "community_no-export.json"

HEADER = "     Network          Next Hop            Metric LocPrf Weight Path"
# linha real, para casos sintéticos com o alinhamento preservado
LINE = " *>   45.6.136.0/24    187.16.217.33                 200      0 266136 i"


def rows(count: int, path: str = "266136 i", next_hop: str = "187.16.217.33") -> str:
    """Rotas sintéticas com prefixo e next hop de largura fixa.

    Largura variável desalinharia a linha em relação ao cabeçalho, e o corte por
    coluna do Path pegaria a coluna errada: o teste passaria a medir o gerador
    em vez do parser.
    """
    out = []
    for i in range(count):
        # prefixo e next hop com a MESMA largura dos originais (13 chars cada),
        # senão a coluna Path sai de posição e o corte por coluna pega o campo
        # errado — o teste mediria o gerador, não o parser
        prefix = f"{i // 100 % 10}.{i // 10 % 10}.{i % 10}.0/24".ljust(13)
        hop = (next_hop if next_hop != "VARIADO"
               else f"10.8.{i // 100 % 10}.{i % 100:03d}")
        out.append(
            LINE.replace("45.6.136.0/24", prefix).replace("266136 i", path)
            .replace("187.16.217.33", hop)
        )
    return "\n".join(out)


def output(body: str) -> str:
    return f"{HEADER}\n{body}\n"


def origin(parsed: dict, asn: str) -> dict:
    return next(o for o in parsed["origins"] if o["origin_as"] == asn)


class TestFullCapture:
    """A captura inteira do equipamento: é dela que sai a tela."""

    def test_headline_numbers(self) -> None:
        parsed = parse_bgp_community(IOS_FULL, {"community": "no-export"})
        assert parsed["total_paths"] == 68
        assert parsed["total_prefixes"] == 32
        assert parsed["origin_count"] == 12
        assert parsed["best_paths"] == 23
        assert parsed["capped_origins"] is False
        assert parsed["capped_prefixes"] is False

    def test_more_paths_than_prefixes(self) -> None:
        # 68 rotas para 32 prefixos: e o additional-path fazendo a diferenca
        parsed = parse_bgp_community(IOS_FULL, {"community": "no-export"})
        assert parsed["total_paths"] > parsed["total_prefixes"]

    def test_every_origin_has_entries(self) -> None:
        parsed = parse_bgp_community(IOS_FULL, {"community": "no-export"})
        for o in parsed["origins"]:
            assert o["entries"], f"AS {o['origin_as']} veio sem prefixo nenhum"
            assert o["paths"] >= len(o["entries"])


class TestJsPayloadFixture:
    """O JSON dos testes de JS não pode divergir do parser.

    Os testes de JavaScript carregam `tests/js/fixtures/community_no-export.json`
    porque JS não roda o parser. Se o formato mudar e o arquivo ficar para trás,
    a card testada deixa de ser a card real — e o teste passa mentindo. Aqui a
    defasagem vira falha de CI.
    """

    def test_matches_parser_output(self) -> None:
        if not PAYLOAD_JS.exists():  # pragma: no cover - fixture versionada
            pytest.skip("fixture de JS ausente")
        esperado = parse_bgp_community(IOS_FULL, {"community": "no-export"})
        assert json.loads(PAYLOAD_JS.read_text()) == esperado


class TestRealOutput:
    def test_type_and_query(self) -> None:
        parsed = parse_bgp_community(IOS, {"community": "no-export"})
        assert parsed["type"] == "bgp_community"
        assert parsed["community"] == "no-export"

    def test_counts(self) -> None:
        parsed = parse_bgp_community(IOS, {"community": "no-export"})
        assert parsed["total_paths"] == 16
        assert parsed["total_prefixes"] == 8
        assert parsed["declared_total"] is None
        assert parsed["origin_count"] == 3

    def test_groups_by_origin_as(self) -> None:
        # a community nao repete na saida; o agrupamento util e por AS de origem
        parsed = parse_bgp_community(IOS, {"community": "no-export"})
        asns = [o["origin_as"] for o in parsed["origins"]]
        assert asns == ["266136", "267268", "267636"]

    def test_origin_counts_routes(self) -> None:
        parsed = parse_bgp_community(IOS, {"community": "no-export"})
        as266136 = origin(parsed, "266136")
        assert as266136["paths"] == 8
        assert len(as266136["prefixes"]) == 4

    def test_next_hop_per_origin(self) -> None:
        parsed = parse_bgp_community(IOS, {"community": "no-export"})
        assert origin(parsed, "266136")["next_hops"] == ["187.16.217.33"]
        assert origin(parsed, "267268")["next_hops"] == ["45.68.76.100"]
        # o AS 267636 chega pelo mesmo next hop do 266136
        assert origin(parsed, "267636")["next_hops"] == ["187.16.217.33"]

    def test_entries_pair_prefix_with_next_hop(self) -> None:
        parsed = parse_bgp_community(IOS, {"community": "no-export"})
        entry = origin(parsed, "266136")["entries"][0]
        assert entry["prefix"] == "45.6.136.0/24"
        assert entry["next_hop"] == "187.16.217.33"

    def test_best_flag_counts_only_best_lines(self) -> None:
        parsed = parse_bgp_community(IOS, {"community": "no-export"})
        assert origin(parsed, "267268")["best_paths"] == 3
        assert origin(parsed, "266136")["best_paths"] == 2
        assert origin(parsed, "267636")["best_paths"] == 0
        assert parsed["best_paths"] == 5

    def test_origins_sorted_by_route_count(self) -> None:
        parsed = parse_bgp_community(IOS, {"community": "no-export"})
        counts = [o["paths"] for o in parsed["origins"]]
        assert counts == sorted(counts, reverse=True)

    def test_total_matches_sum_of_origins(self) -> None:
        parsed = parse_bgp_community(IOS, {"community": "no-export"})
        assert sum(o["paths"] for o in parsed["origins"]) == parsed["total_paths"]

    def test_prefixes_unique_per_origin(self) -> None:
        parsed = parse_bgp_community(IOS, {"community": "no-export"})
        for o in parsed["origins"]:
            assert len(set(o["prefixes"])) == len(o["prefixes"])

    def test_additional_path_shares_prefix(self) -> None:
        # a linha sem coluna Network herda o prefixo e conta como rota, mas o
        # prefixo continua unico
        parsed = parse_bgp_community(IOS, {"community": "no-export"})
        as266136 = origin(parsed, "266136")
        assert as266136["paths"] > len(as266136["prefixes"])


class TestBestPath:
    """A entry precisa descrever a melhor rota do prefixo.

    Regressao: o agrupamento guardava a PRIMEIRA linha do prefixo e marcava
    best conforme ela. Numa saida com additional-path a linha `*` vem antes da
    `*>`, entao a entry ficava com next hop e flag de uma rota que nao era a
    melhor — e a card mostrava o next hop errado como se fosse o escolhido.
    """

    def test_best_line_wins_over_earlier_non_best(self) -> None:
        # 45.6.136.0/24 tem `*` antes e `*>` depois; a entry tem de ser a melhor
        parsed = parse_bgp_community(IOS, {"community": "no-export"})
        entry = origin(parsed, "266136")["entries"][0]
        assert entry["prefix"] == "45.6.136.0/24"
        assert entry["best"] is True

    def test_prefix_without_best_stays_false(self) -> None:
        # 45.6.136.0/23 so tem linhas `*`: continua marcado como nao-best
        parsed = parse_bgp_community(IOS, {"community": "no-export"})
        entry = origin(parsed, "266136")["entries"][1]
        assert entry["prefix"] == "45.6.136.0/23"
        assert entry["best"] is False

    def test_best_entry_uses_best_next_hop(self) -> None:
        # a linha nao-best tem outro next hop; a entry tem de trazer o do best.
        # next hop com a mesma largura do original, senao a coluna Path sai de
        # posicao e o AS de origem muda (ver o aviso em rows()).
        nao_best = LINE.replace("*>", " * ").replace("187.16.217.33", "188.16.217.33")
        parsed = parse_bgp_community(output(f"{nao_best}\n{LINE}"), {"community": "65000:1"})
        asn = parsed["origins"][0]
        assert asn["origin_as"] == "266136"
        assert len(asn["entries"]) == 1
        assert asn["entries"][0]["next_hop"] == "187.16.217.33"
        assert asn["entries"][0]["best"] is True

    def test_best_entries_match_best_paths_count(self) -> None:
        # o contador do cabecalho e as entradas precisam concordar, senao o
        # operador ve "23 best path" e uma tabela sem nenhum best
        parsed = parse_bgp_community(IOS, {"community": "no-export"})
        best_entries = [
            e for o in parsed["origins"] for e in o["entries"] if e["best"]
        ]
        assert len(best_entries) == parsed["best_paths"]

    def test_best_inventory_holds_on_full_capture(self) -> None:
        # a captura inteira e a que vai para a tela, entao o desacordo entre o
        # contador e as linhas nao pode aparecer em escala
        parsed = parse_bgp_community(IOS_FULL, {"community": "no-export"})
        best_entries = [
            e for o in parsed["origins"] for e in o["entries"] if e["best"]
        ]
        assert len(best_entries) == parsed["best_paths"] == 23
        for o in parsed["origins"]:
            assert len(o["entries"]) == len(o["prefixes"])
            assert len(set(e["prefix"] for e in o["entries"])) == len(o["entries"])

    def test_one_entry_per_prefix(self) -> None:
        parsed = parse_bgp_community(IOS, {"community": "no-export"})
        for o in parsed["origins"]:
            assert len(o["entries"]) == len(o["prefixes"])
            assert [e["prefix"] for e in o["entries"]] == o["prefixes"]


class TestLocalRoutes:
    def test_local_route_gets_own_group(self) -> None:
        line = LINE.rsplit("0 ", 1)[0] + "0      Local\n"
        parsed = parse_bgp_community(output(line), {"community": "65000:1"})
        local = parsed["origins"][0]
        assert local["origin_as"] == "local"
        assert local["is_local"] is True


class TestLimits:
    def test_caps_origins(self) -> None:
        body = "\n".join(
            LINE.replace("266136", f"6{i:05d}") for i in range(MAX_ORIGINS + 5)
        )
        parsed = parse_bgp_community(output(body), {"community": "65000:1"})
        assert len(parsed["origins"]) == MAX_ORIGINS
        assert parsed["origin_count"] > MAX_ORIGINS
        assert parsed["capped_origins"] is True

    def test_caps_prefixes_per_origin(self) -> None:
        parsed = parse_bgp_community(
            output(rows(MAX_PREFIXES_PER_ORIGIN + 5)), {"community": "65000:1"}
        )
        asn = parsed["origins"][0]
        assert len(asn["prefixes"]) == MAX_PREFIXES_PER_ORIGIN
        assert len(asn["entries"]) == MAX_PREFIXES_PER_ORIGIN
        assert asn["prefixes_truncated"] is True

    def test_caps_next_hops(self) -> None:
        body = rows(MAX_NEXT_HOPS + 3, next_hop="VARIADO")
        parsed = parse_bgp_community(output(body), {"community": "65000:1"})
        asn = parsed["origins"][0]
        assert len(asn["next_hops"]) == MAX_NEXT_HOPS
        assert asn["next_hops_truncated"] is True

    def test_next_hop_deduplicated(self) -> None:
        parsed = parse_bgp_community(output(rows(5)), {"community": "65000:1"})
        assert parsed["origins"][0]["next_hops"] == ["187.16.217.33"]


class TestErrors:
    def test_missing_header_raises(self) -> None:
        raw = IOS.replace("Network", "Ntwrk")
        with pytest.raises(ParserError, match="community"):
            parse_bgp_community(raw, {"community": "no-export"})

    def test_empty_result_raises(self) -> None:
        # community sem match: o IOS nao imprime tabela nenhuma
        with pytest.raises(ParserError, match="não reconhecida"):
            parse_bgp_community(
                "BGP table version is 1\n", {"community": "65000:999"}
            )

    def test_huawei_zero_routes_raises(self) -> None:
        # o VRP imprime o total e nenhuma tabela quando nao casa
        with pytest.raises(ParserError, match="não reconhecida"):
            parse_bgp_community(
                "\n Total Number of Routes: 0\n", {"community": "65000:1"}
            )

    def test_header_without_routes_raises(self) -> None:
        with pytest.raises(ParserError, match="sem rotas"):
            parse_bgp_community(f"{HEADER}\n", {"community": "65000:1"})


class TestRegistry:
    def test_registered(self) -> None:
        assert "bgp_community" in PARSER_NAMES

    def test_dispatches_through_registry(self) -> None:
        parsed = parse("bgp_community", IOS, {"community": "no-export"})
        assert parsed["type"] == "bgp_community"
        assert parsed["origin_count"] == 3

    def test_works_without_context(self) -> None:
        assert parse_bgp_community(IOS)["community"] == ""
