"""Parser do output de `traceroute` do Cisco IOS/IOS-XE.

Converte a saída textual em dados estruturados (saltos, AS, tempos de cada
sonda). Exemplo de entrada:

    Type escape sequence to abort.
    Tracing the route to 1.1.1.1
    VRF info: (vrf in name/id, vrf out name/id)
      1 10.8.2.241 [AS 265269] 40 msec 19 msec 6 msec
      2 10.32.0.158 [AS 265269] 13 msec 14 msec 14 msec
      3 45.68.72.137 [AS 265269] 14 msec 15 msec 14 msec
      4 1.1.1.1 [AS 13335] 14 msec 14 msec 14 msec

Saída fora do padrão levanta `ParserError` (a engine mantém o output cru).
Destino inalcançável retorna `status: "unreachable"` em vez de erro.
"""

import re

from openglass.parsers.base import ParserError

_DEST_RE = re.compile(
    r"Tracing the route to\s+(?P<destination>\S+)", re.IGNORECASE
)
_HOP_RE = re.compile(
    r"^\s*(?P<hop>\d+)\s+(?P<ip>\S+)\s*(?:\[AS\s+(?P<asn>\d+)\])?\s*"
    r"(?P<times>.*)$"
)
# sonda: <ms> msec | * (timeout) | !<code> (unreachable/outro resultado)
_PROBE_RE = re.compile(r"(?P<ms>\d+)\s+msec|(?P<star>\*)|(?P<code>![A-Z]*)", re.IGNORECASE)
_UNREACHABLE_RES = [
    re.compile(r"%\s*Network unreachable", re.IGNORECASE),
    re.compile(r"%\s*Destination host unreachable", re.IGNORECASE),
    re.compile(r"%\s*Address is in a bad decimal format", re.IGNORECASE),
]


def _parse_times(text: str) -> list[dict]:
    probes: list[dict] = []
    for match in _PROBE_RE.finditer(text):
        if match.group("ms") is not None:
            probes.append({"type": "reply", "ms": int(match.group("ms"))})
        elif match.group("star") is not None:
            probes.append({"type": "timeout"})
        else:
            probes.append({"type": "error", "code": match.group("code")})
    return probes


def parse_traceroute(output: str, context: dict | None = None) -> dict:
    """Estrutura o output de um traceroute do Cisco IOS."""
    del context  # o traceroute não usa parâmetros de contexto
    text = output.replace("\r", "")
    lines = text.splitlines()
    destination_match = _DEST_RE.search(text)

    hops: list[dict] = []
    for line in lines:
        match = _HOP_RE.match(line)
        if match is None:
            continue
        ip = match.group("ip")
        if ip == "*":
            # linha "  3  *  *  * ": IP desconhecido (sondas com timeout)
            ip = None
            times_text = "* " + match.group("times")
        else:
            times_text = match.group("times")
        times = _parse_times(times_text)
        if not times:
            continue
        ms_values = [probe["ms"] for probe in times if probe["type"] == "reply"]
        hops.append(
            {
                "hop": int(match.group("hop")),
                "ip": ip,
                "asn": int(match.group("asn")) if match.group("asn") else None,
                "times": times,
                "avg_ms": round(sum(ms_values) / len(ms_values), 1) if ms_values else None,
            }
        )

    if destination_match is None and not hops:
        raise ParserError("Saída não reconhecida como traceroute do Cisco IOS")

    unreachable = any(res.search(text) for res in _UNREACHABLE_RES)

    destination = destination_match.group("destination") if destination_match else None
    reachable = bool(hops and hops[-1]["ip"] == destination)

    if unreachable:
        return {
            "type": "traceroute",
            "status": "unreachable",
            "destination": destination,
            "message": "Destino inalcançável",
            "hops": hops,
        }

    status = "success" if reachable else ("partial" if hops else "failed")
    return {
        "type": "traceroute",
        "status": status,
        "destination": destination,
        "reachable": reachable,
        "hops": hops,
    }