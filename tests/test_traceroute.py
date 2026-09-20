import pytest

from openglass.parsers import PARSER_NAMES, parse
from openglass.parsers.base import ParserError
from openglass.parsers.traceroute import parse_traceroute

REAL = (
    "edge.jnet#traceroute 1.1.1.1 source 45.5.40.255 \n"
    "Type escape sequence to abort.\n"
    "Tracing the route to 1.1.1.1\n"
    "VRF info: (vrf in name/id, vrf out name/id)\n"
    "  1 10.8.2.241 [AS 265269] 40 msec 19 msec 6 msec\n"
    "  2 10.32.0.158 [AS 265269] 13 msec 14 msec 14 msec\n"
    "  3 45.68.72.137 [AS 265269] 14 msec 15 msec 14 msec\n"
    "  4 1.1.1.1 [AS 13335] 14 msec 14 msec 14 msec\n"
    "edge.jnet#\n"
)

MIXED = (
    "Tracing the route to 10.99.0.1\n"
    "  1 10.0.0.1 5 msec 6 msec 5 msec\n"          # sem AS
    "  2 10.0.0.2 [AS 65000] * * 8 msec\n"         # timeouts
    "  3 10.99.0.1 [AS 65000] * !H 12 msec\n"      # erro !H
)

UNREACHABLE = (
    "Type escape sequence to abort.\n"
    "Tracing the route to 200.160.2.0\n"
    "% Network unreachable\n"
)

BLOCKED = (
    "Tracing the route to 10.99.0.1\n"
    "  1 10.0.0.9 5 msec 6 msec 5 msec\n"
    "  2 10.0.0.8 * * *\n"
)

NO_IP_HOP = (
    "Tracing the route to 10.5.0.1\n"
    "  1 10.0.0.1 5 msec 5 msec 6 msec\n"
    "  2 * * *\n"
    "  3 10.5.0.1 8 msec 9 msec 8 msec\n"
)


class TestTracerouteParser:
    def test_real_router_output(self) -> None:
        result = parse_traceroute(REAL)
        assert result["type"] == "traceroute"
        assert result["status"] == "success"
        assert result["destination"] == "1.1.1.1"
        assert result["reachable"] is True
        assert len(result["hops"]) == 4

    def test_hop_details(self) -> None:
        first = parse_traceroute(REAL)["hops"][0]
        assert first["hop"] == 1
        assert first["ip"] == "10.8.2.241"
        assert first["asn"] == 265269
        assert first["times"] == [
            {"type": "reply", "ms": 40},
            {"type": "reply", "ms": 19},
            {"type": "reply", "ms": 6},
        ]
        assert first["avg_ms"] == 21.7

    def test_last_hop_reached(self) -> None:
        last = parse_traceroute(REAL)["hops"][-1]
        assert last["ip"] == "1.1.1.1"
        assert last["asn"] == 13335

    def test_timeout_and_error_probes(self) -> None:
        hops = parse_traceroute(MIXED)["hops"]
        assert hops[0]["asn"] is None
        assert hops[1]["times"] == [
            {"type": "timeout"},
            {"type": "timeout"},
            {"type": "reply", "ms": 8},
        ]
        assert hops[2]["times"][1] == {"type": "error", "code": "!H"}
        assert hops[1]["avg_ms"] == 8.0

    def test_unreachable_destination(self) -> None:
        result = parse_traceroute(UNREACHABLE)
        assert result["status"] == "unreachable"
        assert result["destination"] == "200.160.2.0"
        assert result["hops"] == []

    def test_partial_when_destination_not_reached(self) -> None:
        result = parse_traceroute(BLOCKED)
        assert result["status"] == "partial"
        assert result["reachable"] is False
        assert result["hops"][-1]["avg_ms"] is None

    def test_hop_without_ip_all_timeouts(self) -> None:
        hops = parse_traceroute(NO_IP_HOP)["hops"]
        assert hops[1]["ip"] is None
        assert hops[1]["times"] == [
            {"type": "timeout"},
            {"type": "timeout"},
            {"type": "timeout"},
        ]
        assert hops[1]["avg_ms"] is None
        assert hops[2]["ip"] == "10.5.0.1"

    def test_invalid_output_raises(self) -> None:
        with pytest.raises(ParserError):
            parse_traceroute("show ip route\n")


class TestRegistry:
    def test_traceroute_registered(self) -> None:
        assert "traceroute" in PARSER_NAMES
        result = parse("traceroute", REAL)
        assert result["status"] == "success"
        assert result["destination"] == "1.1.1.1"