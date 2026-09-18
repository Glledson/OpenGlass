import pytest

from openglass.parsers import PARSER_NAMES, get_parser, parse
from openglass.parsers.base import ParserError
from openglass.parsers.ping import parse_ping

SUCCESS = (
    "Type escape sequence to abort.\n"
    "Sending 5, 100-byte ICMP Echos to 1.1.1.1, timeout is 2 seconds:\n"
    "Packet sent with a source address of 45.5.40.255 \n"
    "!!!!!\n"
    "Success rate is 100 percent (5/5), round-trip min/avg/max = 50/60/99 ms\n"
)

PARTIAL = (
    "Sending 5, 100-byte ICMP Echos to 8.8.8.8, timeout is 2 seconds:\n"
    "!!!!.\n"
    "Success rate is 80 percent (4/5), round-trip min/avg/max = 10/12/15 ms\n"
)

FAILED = (
    "Sending 5, 100-byte ICMP Echos to 8.8.8.8, timeout is 2 seconds:\n"
    ".....\n"
    "Success rate is 0 percent (0/5)\n"
)

UNREACHABLE = (
    "Sending 5, 100-byte ICMP Echos to 10.0.0.9, timeout is 2 seconds:\n"
    "UUUUU\n"
    "Success rate is 0 percent (0/5)\n"
)


class TestPingParser:
    def test_success(self) -> None:
        result = parse_ping(SUCCESS)
        assert result["type"] == "ping"
        assert result["status"] == "success"
        assert result["target"] == "1.1.1.1"
        assert result["source"] == "45.5.40.255"
        assert result["sent"] == 5
        assert result["received"] == 5
        assert result["loss_percent"] == 0
        assert result["success_percent"] == 100
        assert result["size_bytes"] == 100
        assert result["timeout_s"] == 2
        assert result["rtt_ms"] == {"min": 50, "avg": 60, "max": 99}
        assert len(result["probes"]) == 5
        assert {probe["result"] for probe in result["probes"]} == {"reply"}

    def test_partial(self) -> None:
        result = parse_ping(PARTIAL)
        assert result["status"] == "partial"
        assert result["received"] == 4
        assert result["loss_percent"] == 20
        assert result["rtt_ms"] == {"min": 10, "avg": 12, "max": 15}
        assert result["probes"][-1]["result"] == "timeout"

    def test_failed_without_rtt(self) -> None:
        result = parse_ping(FAILED)
        assert result["status"] == "failed"
        assert result["received"] == 0
        assert result["loss_percent"] == 100
        assert result["rtt_ms"] is None

    def test_unreachable_probes(self) -> None:
        result = parse_ping(UNREACHABLE)
        assert result["status"] == "failed"
        assert result["probes"][0]["result"] == "unreachable"

    def test_ipv6_target_and_source(self) -> None:
        output = (
            "Sending 5, 100-byte ICMP Echos to 2001:4860:4860::8888, "
            "timeout is 2 seconds:\n"
            "Packet sent with a source address of 2001:db8::1\n"
            "!!!!!\n"
            "Success rate is 100 percent (5/5), round-trip min/avg/max = 1/2/3 ms\n"
        )
        result = parse_ping(output)
        assert result["target"] == "2001:4860:4860::8888"
        assert result["source"] == "2001:db8::1"

    def test_source_optional(self) -> None:
        output = SUCCESS.replace("Packet sent with a source address of 45.5.40.255 \n", "")
        assert parse_ping(output)["source"] is None

    def test_probes_split_across_lines(self) -> None:
        output = (
            "Sending 20, 100-byte ICMP Echos to 8.8.8.8, timeout is 2 seconds:\n"
            "!!!!!\n!!!!!\n!!!!!\n!!!!!\n"
            "Success rate is 100 percent (20/20), round-trip min/avg/max = 1/2/3 ms\n"
        )
        assert len(parse_ping(output)["probes"]) == 20

    def test_derives_from_probes_without_success_line(self) -> None:
        output = (
            "Sending 5, 100-byte ICMP Echos to 8.8.8.8, timeout is 2 seconds:\n"
            "!!!!!\n"
        )
        result = parse_ping(output)
        assert result["success_percent"] == 100
        assert result["received"] == 5

    def test_invalid_output_raises(self) -> None:
        with pytest.raises(ParserError):
            parse_ping("% Invalid input detected\n")


class TestRegistry:
    def test_ping_registered(self) -> None:
        assert "ping" in PARSER_NAMES
        assert parse("ping", SUCCESS)["target"] == "1.1.1.1"

    def test_unknown_parser_raises(self) -> None:
        with pytest.raises(ParserError):
            get_parser("nao-existe")
