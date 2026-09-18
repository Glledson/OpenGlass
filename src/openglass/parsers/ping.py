"""Parser do output de `ping` do Cisco IOS/IOS-XE.

Converte a saída textual em dados estruturados para exibição didática no
front/CLI. Exemplo de entrada:

    Type escape sequence to abort.
    Sending 5, 100-byte ICMP Echos to 1.1.1.1, timeout is 2 seconds:
    Packet sent with a source address of 45.5.40.255
    !!!!!
    Success rate is 100 percent (5/5), round-trip min/avg/max = 50/60/99 ms

Saída fora do padrão levanta `ParserError` (a engine mantém o output cru).
"""

import re

from openglass.parsers.base import ParserError

# Símbolos de resposta do IOS: ! reply, . timeout, U unreachable, etc.
_PROBE_RESULTS: dict[str, tuple[str, str]] = {
    "!": ("reply", "Resposta"),
    ".": ("timeout", "Tempo esgotado"),
    "U": ("unreachable", "Destino inalcançável"),
    "Q": ("source_quench", "Source quench"),
    "M": ("unfragmentable", "Não fragmentável"),
    "A": ("admin_prohibited", "Proibido administrativamente"),
    "?": ("unknown", "Desconhecido"),
}
_PROBE_CHARS = frozenset(_PROBE_RESULTS)

_SENDING_RE = re.compile(
    r"Sending\s+(?P<count>\d+),\s+(?P<size>\d+)-byte ICMP Echos to\s+"
    r"(?P<target>[^\s,]+)\s*,\s+timeout is\s+(?P<timeout>\d+)\s+seconds",
    re.IGNORECASE,
)
_SOURCE_RE = re.compile(
    r"source address of\s+(?P<source>[0-9A-Fa-f:.]+)",
    re.IGNORECASE,
)
_SUCCESS_RE = re.compile(
    r"Success rate is\s+(?P<percent>\d+)\s+percent\s+"
    r"\((?P<received>\d+)/(?P<sent>\d+)\)"
    r"(?:,\s*round-trip min/avg/max\s*=\s*"
    r"(?P<min>\d+)/(?P<avg>\d+)/(?P<max>\d+)\s*ms)?",
    re.IGNORECASE,
)


def _extract_probes(lines: list[str]) -> list[str]:
    probes: list[str] = []
    for line in lines:
        token = line.strip()
        if token and set(token) <= _PROBE_CHARS:
            probes.extend(token)
    return probes


def _status_for(percent: int) -> str:
    if percent >= 100:
        return "success"
    if percent > 0:
        return "partial"
    return "failed"


def parse_ping(output: str, context: dict | None = None) -> dict:
    """Estrutura o output de um ping simples (não-estendido)."""
    del context  # o ping não usa parâmetros de contexto
    text = output.replace("\r", "")
    lines = text.splitlines()

    sending = _SENDING_RE.search(text)
    success = _SUCCESS_RE.search(text)
    probes = _extract_probes(lines)

    if not sending and not success and not probes:
        raise ParserError("Saída não reconhecida como ping do Cisco IOS")

    source_match = _SOURCE_RE.search(text)
    source = source_match.group("source") if source_match else None

    target = sending.group("target") if sending else None
    size = int(sending.group("size")) if sending else None
    timeout_s = int(sending.group("timeout")) if sending else None
    sent = int(sending.group("count")) if sending else None

    received: int | None = None
    percent: int | None = None
    rtt_ms: dict[str, int] | None = None

    if success:
        received = int(success.group("received"))
        sent = int(success.group("sent"))
        percent = int(success.group("percent"))
        if success.group("min") is not None:
            rtt_ms = {
                "min": int(success.group("min")),
                "avg": int(success.group("avg")),
                "max": int(success.group("max")),
            }

    if received is None and probes:
        received = sum(1 for symbol in probes if symbol == "!")
        if sent is None:
            sent = len(probes)
        percent = round(received * 100 / sent) if sent else 0

    if percent is None:
        percent = 0
    if sent is None:
        sent = len(probes)
    if received is None:
        received = 0

    return {
        "type": "ping",
        "status": _status_for(percent),
        "target": target,
        "source": source,
        "sent": sent,
        "received": received,
        "loss_percent": 100 - percent,
        "success_percent": percent,
        "size_bytes": size,
        "timeout_s": timeout_s,
        "rtt_ms": rtt_ms,
        "probes": [
            {
                "symbol": symbol,
                "result": _PROBE_RESULTS[symbol][0],
                "label": _PROBE_RESULTS[symbol][1],
            }
            for symbol in probes
        ],
    }
