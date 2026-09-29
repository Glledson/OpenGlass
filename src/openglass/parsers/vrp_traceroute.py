"""Parser do output de `tracert` do Huawei VRP (VRP 8.x / NetEngine).

A saída real do lab (NetEngine 8000, VRP 8.200):

    traceroute to 8.8.8.8(8.8.8.8), max hops: 30, packet length: 40, press CTRL_C to break
     1 192.0.2.225 4 ms  2 ms
     2 10.8.24.73 4 ms  3 ms
     3 142.250.47.86 30 ms  40 ms
     4 209.85.244.163 33 ms  192.178.84.13 34 ms
     5 142.250.238.249 32 ms  192.178.253.75 36 ms
     6 8.8.8.8 33 ms  40 ms

E quando o salto não responde:

     8  *  *

Duas diferenças estruturais em relação ao IOS, e as duas caem na mesma linha:

1. **Um endereço por sonda, não por salto.** O IOS imprime o IP uma vez e depois
   N tempos (`10.0.0.1 [AS 1] 1 msec 2 msec`); o VRP pode repetir o endereço
   quando cada sonda saiu por um caminho diferente — o salto 4 acima tem
   `209.85.244.163` na primeira e `192.178.84.13` na segunda. Ler o primeiro
   endereço da linha, como o IOS faz, perderia o caminho alternativo e o
   operador veria um next hop que não é o do segundo pacote.
2. **Sem anotação de AS.** O VRP não decora o salto com o AS, então `asn` fica
   nulo — é informação que o equipamento não dá, não uma lacuna do parser.

Por isso o salto sai com `addresses` (a lista real, na ordem das sondas) e cada
sonda carrega o seu `ip`. `ip` continua sendo o primeiro endereço, para a card
que já existia ter o que mostrar sem conhecer o VRP.
"""

import re

from openglass.parsers.base import ParserError

_DEST_RE = re.compile(
    r"traceroute to\s+(?P<destination>\S+?)\(\S+\)\s*,", re.IGNORECASE
)
_HOP_RE = re.compile(r"^\s*(?P<hop>\d+)\s+(?P<body>\S.*?)\s*$")
# o VRP escreve "4 ms" com espaço; colado vira um token só, senão "4" e "ms"
# viram dois tokens e o tempo se perde
_TIME_RE = re.compile(r"^(?P<ms>\d+)ms$", re.IGNORECASE)
_GLUE_TIME_RE = re.compile(r"(\d+)\s+ms\b", re.IGNORECASE)
# exige ponto ou dois-pontos: sem isso um número solto passaria por endereço
_ADDRESS_RE = re.compile(r"^(?=.*[.:])[0-9A-Fa-f:.]+$")
_MAX_HOPS_RE = re.compile(r"max hops:\s*(?P<hops>\d+)", re.IGNORECASE)


def _parse_body(body: str) -> list[dict]:
    """Corpo da linha do salto → uma sonda por token de tempo.

    O corpo é uma sequência de tokens alternando endereço (opcional) e tempo.
    O endereço pertence à sonda seguinte: é o caminho por onde aquele pacote
    saiu, não uma propriedade do salto.
    """
    glued = _GLUE_TIME_RE.sub(r"\1ms", body)
    probes: list[dict] = []
    pending: str | None = None
    for token in glued.split():
        if token == "*":
            probes.append({"type": "timeout", "ip": None})
            pending = None
        elif _TIME_RE.match(token):
            probes.append(
                {"type": "reply", "ip": pending, "ms": int(_TIME_RE.match(token).group("ms"))}
            )
            pending = None
        elif _ADDRESS_RE.match(token):
            # endereço solto no meio da linha: o VRP repete o IP quando a sonda
            # saiu por outro caminho, e o token é o next hop daquela sonda
            pending = token
        else:
            # o que o VRP chama de erro: `M`, `!X`, mensagem de protocolo
            probes.append({"type": "error", "ip": pending, "code": token})
            pending = None
    return probes


def parse_traceroute_vrp(output: str, context: dict | None = None) -> dict:
    """Estrutura o output de um tracert do VRP no contrato do parser do IOS."""
    del context  # o traceroute não usa parâmetros de contexto
    text = output.replace("\r", "")

    hops: list[dict] = []
    for line in text.splitlines():
        match = _HOP_RE.match(line)
        if match is None:
            continue
        probes = _parse_body(match.group("body"))
        if not probes:
            continue
        answered = [probe["ms"] for probe in probes if probe["type"] == "reply"]
        # `addresses` na ordem em que apareceram, sem repetir: o próximo hop do
        # salto é o primeiro que respondeu, e os outros são alternativas
        seen: list[str] = []
        for probe in probes:
            if probe["ip"] and probe["ip"] not in seen:
                seen.append(probe["ip"])
        hops.append(
            {
                "hop": int(match.group("hop")),
                "ip": seen[0] if seen else None,
                "addresses": seen,
                # o VRP não decora o salto com AS
                "asn": None,
                # cada sonda carrega o próprio next hop: no VRP o caminho
                # alternativo aparece na MESMA linha do salto, e descartar o
                # endereço por sonda esconderia um caminho que existiu
                "times": probes,
                "avg_ms": round(sum(answered) / len(answered), 1) if answered else None,
            }
        )

    dest_match = _DEST_RE.search(text)
    if dest_match is None and not hops:
        raise ParserError("Saída não reconhecida como traceroute do Huawei VRP")

    destination = dest_match.group("destination") if dest_match else None
    answered_last = hops[-1] if hops else None
    reachable = bool(answered_last and answered_last["ip"] == destination)

    if not hops:
        return {
            "type": "traceroute",
            "nos": "huawei_vrp",
            "status": "failed",
            "destination": destination,
            "reachable": False,
            "hops": [],
        }

    # caminho que responde até o fim = sucesso; respostas com salto final mudo =
    # incompleto (é o caso comum: o destino filtra TTL)
    last_responded = [hop for hop in hops if hop["ip"]]
    status = "success" if reachable else ("partial" if last_responded else "failed")
    return {
        "type": "traceroute",
        "nos": "huawei_vrp",
        "status": status,
        "destination": destination,
        "reachable": reachable,
        "max_hops": (
            int(_MAX_HOPS_RE.search(text).group("hops")) if _MAX_HOPS_RE.search(text) else None
        ),
        "hops": hops,
    }
