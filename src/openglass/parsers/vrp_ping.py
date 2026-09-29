"""Parser do output de `ping` do Huawei VRP (VRP 8.x / NetEngine).

A saída real do lab (NetEngine 8000, VRP 8.200):

    PING 8.8.8.8: 56  data bytes, press CTRL_C to break
      Reply from 8.8.8.8: bytes=56 Sequence=1 ttl=111 time=38 ms
      Reply from 8.8.8.8: bytes=56 Sequence=2 ttl=111 time=38 ms

      --- 8.8.8.8 ping statistics ---
        5 packet(s) transmitted
        5 packet(s) received
        0.00% packet loss
        round-trip min/avg/max = 38/38/39 ms

O VRP é mais verboso que o IOS em duas coisas que interessam: cada resposta traz
`ttl` e `time` individuais (o IOS só imprime `!!!!!`), e a perda sai como
`Request time out`, uma linha por sonda, em vez de um `.`. Tudo isso cabe no
mesmo contrato do parser do IOS, então a mesma card renderiza os dois NOSes.

A ausência de perda não é presumida: `Request time out` vira sonda com
resultado `timeout`, e o resumo das estatísticas é a fonte da verdade. Uma
resposta `unreach` do VRP (que não apareceu em nenhum destino testado aqui) não
é adivinhada — nesse caso o parser levanta `ParserError` e a engine mantém o
texto cru, que é o comportamento certo para um formato que ninguém viu ainda.
"""

import re

from openglass.parsers.base import ParserError

_HEADER_RE = re.compile(
    r"PING\s+(?P<target>[0-9A-Fa-f:.]+):\s+(?P<size>\d+)\s+data bytes",
    re.IGNORECASE,
)
_REPLY_RE = re.compile(
    r"Reply from\s+(?P<from>[0-9A-Fa-f:.]+):\s*bytes=(?P<size>\d+)\s+"
    r"Sequence=(?P<seq>\d+)\s+ttl=(?P<ttl>\d+)\s+time=(?P<ms>\d+)\s*ms",
    re.IGNORECASE,
)
_TIMEOUT_RE = re.compile(r"Request time out", re.IGNORECASE)
_SENT_RE = re.compile(r"(?P<count>\d+)\s+packet\(s\)\s+transmitted", re.IGNORECASE)
_RECEIVED_RE = re.compile(r"(?P<count>\d+)\s+packet\(s\)\s+received", re.IGNORECASE)
_LOSS_RE = re.compile(r"(?P<percent>[\d.]+)%\s+packet loss", re.IGNORECASE)
_RTT_RE = re.compile(
    r"round-trip min/avg/max\s*=\s*(?P<min>\d+)/(?P<avg>\d+)/(?P<max>\d+)\s*ms",
    re.IGNORECASE,
)


def _status_for(percent: float) -> str:
    if percent >= 100:
        return "success"
    if percent > 0:
        return "partial"
    return "failed"


def _extract_probes(lines: list[str]) -> list[dict]:
    """Uma linha por sonda: `Reply from ...` vira reply, `Request time out`
    vira timeout. A ordem das linhas é a ordem das sondas."""
    probes: list[dict] = []
    for line in lines:
        reply = _REPLY_RE.search(line)
        if reply is not None:
            probes.append(
                {
                    "symbol": "!",
                    "result": "reply",
                    "label": "Resposta",
                    "ttl": int(reply.group("ttl")),
                    "time_ms": int(reply.group("ms")),
                }
            )
        elif _TIMEOUT_RE.search(line):
            probes.append(
                {
                    "symbol": ".",
                    "result": "timeout",
                    "label": "Tempo esgotado",
                }
            )
    return probes


def parse_ping_vrp(output: str, context: dict | None = None) -> dict:
    """Estrutura o output de um ping do VRP no contrato do parser do IOS."""
    del context  # o ping não usa parâmetros de contexto
    text = output.replace("\r", "")

    header = _HEADER_RE.search(text)
    sent_m = _SENT_RE.search(text)
    received_m = _RECEIVED_RE.search(text)
    loss_m = _LOSS_RE.search(text)
    rtt_m = _RTT_RE.search(text)
    probes = _extract_probes(text.splitlines())

    if header is None and sent_m is None and not probes:
        raise ParserError("Saída não reconhecida como ping do Huawei VRP")

    sent = int(sent_m.group("count")) if sent_m else len(probes)
    received = int(received_m.group("count")) if received_m else sum(
        1 for probe in probes if probe["result"] == "reply"
    )

    if loss_m is not None:
        loss_percent = float(loss_m.group("percent"))
    else:
        loss_percent = round((sent - received) * 100 / sent, 2) if sent else 100.0
    success_percent = round(100 - loss_percent, 2)

    return {
        "type": "ping",
        "nos": "huawei_vrp",
        "status": _status_for(success_percent),
        "target": header.group("target") if header else None,
        # o VRP não imprime o endereço de origem no ping: o `-a` do comando é
        # preenchido pela engine e não volta na saída
        "source": None,
        "sent": sent,
        "received": received,
        "loss_percent": loss_percent,
        "success_percent": success_percent,
        "size_bytes": int(header.group("size")) if header else None,
        # o tempo limite é um default do VRP (2s) e não sai na saída; inventar
        # um valor aqui seria mostrar ao operador um número que ele não mediu
        "timeout_s": None,
        "rtt_ms": (
            {
                "min": int(rtt_m.group("min")),
                "avg": int(rtt_m.group("avg")),
                "max": int(rtt_m.group("max")),
            }
            if rtt_m
            else None
        ),
        "probes": probes,
    }
