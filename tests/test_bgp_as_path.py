"""Testes do parser de AS Path.

As fixtures em `tests/fixtures/` são recortes reais de equipamento:

- `cisco_ios_as_path.txt`: `show bgp ipv4 unicast quote-regexp "(_13335_)"` em
  ASR1000 IOS-XE 16.09 (69 linhas: preâmbulo + 60 rotas).
- `cisco_ios_as_path_additional.txt`: janela com caminhos adicionais (coluna
  Network vazia) e rotas suprimidas (código `s`) sem next hop repetido.
- `cisco_ios_as_path_narrow.txt`: região onde o IOS estreita a coluna Network e
  imprime prefixos sem máscara — prova de que a coluna Path não pode ser
  assumida fixa a partir do next hop.
- `huawei_vrp_as_path.txt`: `display bgp routing-table regular-expression
  _64512_` em NetEngine 8000 VRP 8.200.

Os casos sintéticos reescrevem o campo Path de uma linha real, preservando o
alinhamento de colunas do equipamento.
"""

import re
from pathlib import Path

import pytest

from openglass.parsers import PARSER_NAMES, parse
from openglass.parsers.base import ParserError
from openglass.parsers.bgp_as_path import (
    MAX_CHAINS,
    MAX_PREFIXES_PER_CHAIN,
    parse_bgp_as_path,
)

FIXTURES = Path(__file__).parent / "fixtures"

# AS, AS set ou confederation set: nada mais pode entrar no caminho.
AS_HOP_RE = re.compile(r"^(?:\d+|\([^)]*\)|\{[^}]*\})$")
IOS = (FIXTURES / "cisco_ios_as_path.txt").read_text()
IOS_ADDITIONAL = (FIXTURES / "cisco_ios_as_path_additional.txt").read_text()
IOS_NARROW = (FIXTURES / "cisco_ios_as_path_narrow.txt").read_text()
HUAWEI = (FIXTURES / "huawei_vrp_as_path.txt").read_text()

# Linha real do IOS usada como template: colunas ficam alinhadas de verdade.
IOS_LINE = " *>   1.0.0.0/24       10.0.0.241                             0 265269 13335 i"
IOS_HEADER = "     Network          Next Hop            Metric LocPrf Weight Path"


def fixed_prefix(index: int) -> str:
    """Prefixo de largura constante (10 chars), como o IOS alinha a coluna.

    Largura variável desalinharia a linha e o corte por coluna do Path pegaria
    a coluna errada: o teste passaria a medir o fixture, não o parser.
    """
    return f"{index % 10}.{index // 10 % 10}.{index // 100 % 10}.0/24"


def ios_rows(count: int, path: str = "265269 13335 i") -> str:
    """Gera `count` rotas reais com prefixo e campo Path substituídos."""
    row = IOS_LINE.replace("265269 13335 i", path)
    return "\n".join(row.replace("1.0.0.0/24", fixed_prefix(i)) for i in range(count))


def ios_output(rows: str) -> str:
    return f"{IOS_HEADER}\n{rows}\n"


class TestIosFormat:
    def test_counts_routes(self) -> None:
        parsed = parse_bgp_as_path(IOS, {"asn": "13335"})
        assert parsed["type"] == "bgp_as_path"
        assert parsed["total_paths"] == 60
        assert parsed["total_prefixes"] == 60
        assert parsed["declared_total"] is None
        assert parsed["capped_chains"] is False

    def test_keeps_searched_asn(self) -> None:
        assert parse_bgp_as_path(IOS, {"asn": "13335"})["asn"] == "13335"

    @staticmethod
    def _chain_of(parsed: dict, prefix: str) -> dict:
        """Caminho dono de um prefixo (o mesmo prefixo pode ter vários)."""
        return next(c for c in parsed["chains"] if prefix in c["prefixes"])

    def test_prefix_and_next_hop(self) -> None:
        chain = self._chain_of(parse_bgp_as_path(IOS, {"asn": "13335"}), "1.0.0.0/24")
        assert chain["as_path"] == ["265269", "13335"]
        assert chain["next_hop"] == "10.0.0.241"

    def test_chain_is_ordered_by_number_of_paths(self) -> None:
        chains = parse_bgp_as_path(IOS, {"asn": "13335"})["chains"]
        assert chains[0]["paths"] >= chains[-1]["paths"]

    def test_prefix_without_mask_kept_as_printed(self) -> None:
        # nesta região o IOS imprime prefixos sem máscara (peer anunciando com
        # máscara 0/0): o parser repete o que o equipamento mostrou, sem
        # inventar um /32
        parsed = parse_bgp_as_path(IOS_NARROW, {"asn": "13335"})
        prefixes = [p for c in parsed["chains"] for p in c["prefixes"]]
        sem_mascara = [p for p in prefixes if "/" not in p]
        assert sem_mascara
        assert all(p.count(".") == 3 for p in sem_mascara)
        assert parsed["total_paths"] > 0

    def test_column_width_changes_do_not_shift_path(self) -> None:
        # a coluna Network encolhe nesta janela; o caminho continua íntegro
        for raw in (IOS, IOS_ADDITIONAL, IOS_NARROW):
            paths = {tuple(c["as_path"]) for c in parse_bgp_as_path(raw)["chains"]}
            assert paths, raw
            assert all(all(AS_HOP_RE.match(a) for a in path) for path in paths)

    def test_weight_column_does_not_leak_into_path(self) -> None:
        # a coluna Weight vale 0 e vem antes de Path: não pode virar "0" no AS
        paths = {tuple(c["as_path"]) for c in parse_bgp_as_path(IOS)["chains"]}
        assert all(asn.isdigit() and asn != "0" for path in paths for asn in path)
        assert ("265269", "13335", "137753") in paths

    def test_longest_path(self) -> None:
        parsed = parse_bgp_as_path(IOS, {"asn": "13335"})
        longest = max(len(c["as_path"]) for c in parsed["chains"])
        assert parsed["longest_path"] == longest
        assert longest > 3

    def test_chains_sorted_by_frequency(self) -> None:
        counts = [c["paths"] for c in parse_bgp_as_path(IOS)["chains"]]
        assert counts == sorted(counts, reverse=True)

    def test_total_matches_sum_of_chains(self) -> None:
        parsed = parse_bgp_as_path(IOS, {"asn": "13335"})
        assert sum(c["paths"] for c in parsed["chains"]) == parsed["total_paths"]

    def test_every_route_prefix_is_valid(self) -> None:
        parsed = parse_bgp_as_path(IOS, {"asn": "13335"})
        prefixes = [p for c in parsed["chains"] for p in c["prefixes"]]
        assert all(p.count(".") == 3 for p in prefixes)
        assert len(set(prefixes)) == parsed["total_prefixes"]


class TestHuaweiFormat:
    def test_parses_vrp_output(self) -> None:
        parsed = parse_bgp_as_path(HUAWEI, {"asn": "64512"})
        assert parsed["total_paths"] == 1
        assert parsed["total_prefixes"] == 1
        assert parsed["declared_total"] == 1

    def test_origin_glued_to_last_as(self) -> None:
        # VRP imprime "64512i": a origem vem grudada no último AS
        chain = parse_bgp_as_path(HUAWEI, {"asn": "64512"})["chains"][0]
        assert chain["as_path"] == ["644970", "7738", "65010", "64512"]
        assert chain["origin"] == "i"

    def test_prefix_and_next_hop(self) -> None:
        chain = parse_bgp_as_path(HUAWEI, {"asn": "64512"})["chains"][0]
        assert chain["prefixes"] == ["187.76.192.104/29"]
        assert chain["next_hop"] == "192.0.2.221"

    def test_aggregate_flag_is_not_a_prefix(self) -> None:
        # a linha traz a flag "N" entre o status e o prefixo
        assert "187.76.192.104/29" in HUAWEI
        prefixes = [
            p
            for c in parse_bgp_as_path(HUAWEI, {"asn": "64512"})["chains"]
            for p in c["prefixes"]
        ]
        assert prefixes == ["187.76.192.104/29"]


class TestAdditionalPaths:
    def test_suppressed_route_inherits_prefix(self) -> None:
        # linha "s"/"*" sem coluna Network e sem next hop repetido
        parsed = parse_bgp_as_path(IOS_ADDITIONAL, {"asn": "13335"})
        assert parsed["total_prefixes"] > 0
        prefixes = [p for c in parsed["chains"] for p in c["prefixes"]]
        # o prefixo 104.22.10.0/24 aparece nas duas rotas que o compartilham
        assert prefixes.count("104.22.10.0/24") == 2
        assert len(set(prefixes)) == parsed["total_prefixes"]

    def test_prefixes_unique_per_chain(self) -> None:
        # o mesmo prefixo pode ter caminhos diferentes: a unicidade é por
        # caminho, senão o contador de prefixos mentiria
        parsed = parse_bgp_as_path(IOS_ADDITIONAL, {"asn": "13335"})
        for chain in parsed["chains"]:
            assert len(set(chain["prefixes"])) == len(chain["prefixes"])

    def test_additional_paths_increase_paths_not_prefixes(self) -> None:
        parsed = parse_bgp_as_path(IOS_ADDITIONAL, {"asn": "13335"})
        assert parsed["total_paths"] > parsed["total_prefixes"]

    def test_blank_network_column_keeps_previous_prefix(self) -> None:
        raw = IOS_LINE + "\n *>                    10.0.0.241                             0 265269 13335 i\n"
        parsed = parse_bgp_as_path(ios_output(raw), {"asn": "13335"})
        assert parsed["total_paths"] == 2
        assert parsed["total_prefixes"] == 1
        assert parsed["chains"][0]["prefixes"] == ["1.0.0.0/24"]
        assert parsed["chains"][0]["paths"] == 2


class TestPathEdgeCases:
    def test_as_set_in_path(self) -> None:
        raw = IOS_LINE.replace("265269 13335 i", "265269 (13335 15169) i")
        chain = parse_bgp_as_path(ios_output(raw), {"asn": "13335"})["chains"][0]
        assert chain["as_path"] == ["265269", "(13335 15169)"]

    def test_as_set_counted_as_single_hop(self) -> None:
        rows = ios_rows(1, "265269 (13335 15169) i")
        parsed = parse_bgp_as_path(ios_output(rows), {"asn": "13335"})
        assert parsed["longest_path"] == 2

    def test_local_route(self) -> None:
        raw = IOS_LINE.rsplit("0 ", 1)[0] + "0      Local\n"
        parsed = parse_bgp_as_path(ios_output(raw), {"asn": "13335"})
        local = [c for c in parsed["chains"] if c["is_local"]]
        assert local and local[0]["as_path"] == []
        assert local[0]["origin"] is None

    def test_unknown_origin(self) -> None:
        raw = IOS_LINE.replace("13335 i", "13335 ?")
        assert parse_bgp_as_path(ios_output(raw), {"asn": "13335"})["chains"][0]["origin"] == "?"

    def test_best_flag_from_status_codes(self) -> None:
        parsed = parse_bgp_as_path(IOS, {"asn": "13335"})
        best = [c for c in parsed["chains"] if c["best"]]
        assert best and best[0]["paths"] > 0

    def test_ignores_non_route_lines(self) -> None:
        parsed = parse_bgp_as_path(IOS, {"asn": "13335"})
        # o preâmbulo tem "valid" e "best" mas nenhuma vira rota
        assert parsed["total_prefixes"] == 60


class TestLimits:
    def test_caps_chains(self) -> None:
        rows = "\n".join(
            IOS_LINE.replace("265269 13335", f"265269 1{i:05d}") for i in range(MAX_CHAINS + 10)
        )
        parsed = parse_bgp_as_path(ios_output(rows), {"asn": "1"})
        assert len(parsed["chains"]) == MAX_CHAINS
        assert parsed["capped_chains"] is True
        assert parsed["unique_chains"] > MAX_CHAINS

    def test_caps_prefixes_per_chain(self) -> None:
        rows = ios_rows(MAX_PREFIXES_PER_CHAIN + 5)
        parsed = parse_bgp_as_path(ios_output(rows), {"asn": "13335"})
        assert parsed["total_prefixes"] == MAX_PREFIXES_PER_CHAIN + 5
        assert parsed["total_paths"] == MAX_PREFIXES_PER_CHAIN + 5
        assert len(parsed["chains"][0]["prefixes"]) == MAX_PREFIXES_PER_CHAIN
        assert parsed["capped_prefixes"] is True

    def test_asn_count(self) -> None:
        parsed = parse_bgp_as_path(IOS, {"asn": "13335"})
        assert parsed["asn_count"] == len({a for c in parsed["chains"] for a in c["as_path"]})


class TestErrors:
    def test_missing_header_raises(self) -> None:
        raw = IOS.replace("Network", "Ntwrk").replace("Path", "Pth")
        with pytest.raises(ParserError, match="AS Path"):
            parse_bgp_as_path(raw, {"asn": "13335"})

    def test_header_without_routes_raises(self) -> None:
        with pytest.raises(ParserError, match="sem rotas"):
            parse_bgp_as_path(f"{IOS_HEADER}\n")

    def test_huawei_zero_routes_raises(self) -> None:
        raw = HUAWEI.replace(
            "Total Number of Routes: 1", "Total Number of Routes: 0"
        )
        raw = "\n".join(l for l in raw.splitlines() if "64512i" not in l)
        with pytest.raises(ParserError, match="sem rotas"):
            parse_bgp_as_path(raw, {"asn": "64512"})


class TestRegistry:
    def test_registered(self) -> None:
        assert "bgp_as_path" in PARSER_NAMES

    def test_dispatches_through_registry(self) -> None:
        parsed = parse("bgp_as_path", IOS, {"asn": "13335"})
        assert parsed["type"] == "bgp_as_path"
        assert parsed["total_prefixes"] == 60

    def test_works_without_asn_param(self) -> None:
        assert parse_bgp_as_path(IOS)["asn"] == ""
