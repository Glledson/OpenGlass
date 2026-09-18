import pytest

from openglass.parsers import PARSER_NAMES, parse
from openglass.parsers.base import ParserError
from openglass.parsers.bgp_prefix import parse_bgp_prefix

TWO_PATHS = (
    "BGP routing table entry for 8.8.8.0/24, version 100803950\n"
    "Paths: (2 available, best #1, table default)\n"
    "  Not advertised to any peer\n"
    "  Refresh Epoch 1\n"
    "  263009 15169\n"
    "    10.200.210.129 from 10.200.210.129 (170.84.55.250)\n"
    "      Origin IGP, localpref 150, valid, external, best\n"
    "      rx pathid: 0, tx pathid: 0x0\n"
    "  Refresh Epoch 1\n"
    "  265269 15169\n"
    "    10.8.2.241 from 10.8.2.241 (192.168.191.245)\n"
    "      Origin IGP, localpref 100, valid, external\n"
    "      rx pathid: 0, tx pathid: 0\n"
)

AGGREGATED = (
    "BGP routing table entry for 1.1.1.0/24, version 106199184\n"
    "Paths: (2 available, best #1, table default)\n"
    "  Not advertised to any peer\n"
    "  Refresh Epoch 1\n"
    "  263009 13335, (aggregated by 13335 172.69.93.1)\n"
    "    10.200.210.129 from 10.200.210.129 (170.84.55.250)\n"
    "      Origin IGP, localpref 150, valid, external, atomic-aggregate, best\n"
    "      unknown transitive attribute: flag 0xE0 type 0x23 length 0x4\n"
    "        value 0000 3417 \n"
    "      rx pathid: 0, tx pathid: 0x0\n"
    "  Refresh Epoch 1\n"
    "  265269 13335, (aggregated by 13335 162.158.224.1)\n"
    "    10.8.2.241 from 10.8.2.241 (192.168.191.245)\n"
    "      Origin IGP, localpref 100, valid, external, atomic-aggregate\n"
    "      rx pathid: 0, tx pathid: 0\n"
)

LOCAL = (
    "BGP routing table entry for 45.5.40.0/24, version 116595\n"
    "Paths: (1 available, best #1, table default)\n"
    "  Advertised to update-groups:\n"
    "     1          3          4          11         32        \n"
    "  Refresh Epoch 1\n"
    "  Local\n"
    "    0.0.0.0 from 0.0.0.0 (45.5.40.0)\n"
    "      Origin IGP, metric 0, localpref 100, weight 32768, valid, sourced, local, best\n"
    "      rx pathid: 0, tx pathid: 0x0\n"
)

NOT_IN_TABLE = "% Network not in table\n"


class TestBgpPrefixParser:
    def test_two_paths_summary(self) -> None:
        result = parse_bgp_prefix(TWO_PATHS, {"prefix": "8.8.8.0/24"})
        assert result["type"] == "bgp_prefix"
        assert result["status"] == "ok"
        assert result["prefix"] == "8.8.8.0/24"
        assert result["version"] == 100803950
        assert result["available"] == 2
        assert result["best_index"] == 1
        assert result["table"] == "default"
        assert result["advertised"] is False
        assert len(result["paths"]) == 2

    def test_best_path_attributes(self) -> None:
        best = parse_bgp_prefix(TWO_PATHS)["paths"][0]
        assert best["index"] == 1
        assert best["as_path"] == ["263009", "15169"]
        assert best["next_hop"] == "10.200.210.129"
        assert best["from"] == "10.200.210.129"
        assert best["originator"] == "170.84.55.250"
        assert best["origin"] == "IGP"
        assert best["localpref"] == 150
        assert best["best"] is True
        assert set(best["flags"]) == {"valid", "external", "best"}

    def test_second_path_not_best(self) -> None:
        second = parse_bgp_prefix(TWO_PATHS)["paths"][1]
        assert second["index"] == 2
        assert second["best"] is False
        assert second["localpref"] == 100
        assert second["as_path"] == ["265269", "15169"]

    def test_aggregation_and_unknown_attribute(self) -> None:
        best = parse_bgp_prefix(AGGREGATED)["paths"][0]
        assert best["as_path"] == ["263009", "13335"]
        assert best["aggregations"] == [{"asn": 13335, "router": "172.69.93.1"}]
        assert "atomic-aggregate" in best["flags"]

    def test_local_origin_and_update_groups(self) -> None:
        result = parse_bgp_prefix(LOCAL)
        assert result["advertised"] is True
        assert result["update_groups"] == ["1", "3", "4", "11", "32"]
        path = result["paths"][0]
        assert path["is_local"] is True
        assert path["as_path"] == []
        assert path["as_path_text"] == "Local"
        assert path["metric"] == 0
        assert path["weight"] == 32768
        assert path["next_hop"] == "0.0.0.0"

    def test_not_in_table_uses_context_prefix(self) -> None:
        result = parse_bgp_prefix(NOT_IN_TABLE, {"prefix": "200.160.2.0/24"})
        assert result["status"] == "not_found"
        assert result["prefix"] == "200.160.2.0/24"
        assert "paths" not in result

    def test_invalid_output_raises(self) -> None:
        with pytest.raises(ParserError):
            parse_bgp_prefix("% Invalid input detected at '^' marker.\n")


class TestRegistry:
    def test_bgp_prefix_registered(self) -> None:
        assert "bgp_prefix" in PARSER_NAMES
        assert parse("bgp_prefix", TWO_PATHS, {"prefix": "8.8.8.0/24"})["status"] == "ok"
