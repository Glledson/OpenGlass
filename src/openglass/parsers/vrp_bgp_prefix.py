"""Parser do output de `display bgp routing-table <prefixo> <máscara>` do VRP.

A saída real do lab (NetEngine 8000, VRP 8.200), com dois caminhos para o mesmo
prefixo:

     BGP local router ID : 10.0.0.1
     Local AS number : 644960
     Paths:   2 available, 1 best, 1 select, 0 best-external, 0 add-path
     BGP routing table entry information of 8.8.8.0/24:
     From: 192.0.2.225 (10.0.0.251)
     Route Duration: 11d02h59m34s
     Direct Out-interface: Eth-Trunk0.2042
     Original nexthop: 192.0.2.225
     Qos information : 0x0
     Community: <65001:1000>, <65001:1100>, <65001:1400>
     AS-path 644970 269194 15169, origin igp, localpref 150, pref-val 0, valid,
     external, best, select, pre 20, validation not-found
     Not advertised to any peer yet

O VRP imprime **um bloco por caminho**, e é o `From:` que abre cada bloco — a
mesma ideia do `next hop from ...` do IOS, que também ancora os blocos.

O que o VRP tem e o IOS não:

- `Direct Out-interface` e `Route Duration`: a interface de saída e há quanto
  tempo a rota está na tabela. Nenhum dos dois existe no detalhe do IOS.
- `pref-val` (valor de preferência, quanto maior melhor) e `pre N` (a regra
  aplicada). **Não** é a métrica do IOS: sobrescrever `metric` com o pref-val
  faria a card comparar coisas que não se comparam, então são campos próprios.
- `not preferred for router ID` / `not preferred for AS-Path`: por que este
  caminho perdeu. No IOS essa informação também não aparece no detalhe, e é
  exatamente a que o operador quer quando há dois caminhos.
- `Aggregator: AS 13335, Aggregator ID ..., Atomic-aggregate`, no formato do
  VRP, mapeado no mesmo `aggregations` que o IOS já usa.

O que o VRP não tem: `version` e `table default`. São `None`, e a card já
trata campo ausente sem quebrar.
"""

import re

from openglass.parsers.base import ParserError

_ENTRY_RE = re.compile(
    r"BGP routing table entry information of\s+(?P<prefix>\S+):", re.IGNORECASE
)
_PATHS_RE = re.compile(
    r"Paths:\s*(?P<available>\d+)\s+available\s*,\s*(?P<best>\d+)\s+best\s*,\s*"
    r"(?P<select>\d+)\s+select",
    re.IGNORECASE,
)
_FROM_RE = re.compile(
    r"^From:\s+(?P<next_hop>\S+)\s*(?:\((?P<originator>[^)]*)\))?\s*$", re.IGNORECASE
)
_AS_PATH_RE = re.compile(r"^AS-path\s+(?P<as_path>.*)$", re.IGNORECASE)
_ORIGIN_RE = re.compile(r"origin\s+(?P<origin>\w+)", re.IGNORECASE)
_LOCALPREF_RE = re.compile(r"localpref\s+(?P<value>\d+)", re.IGNORECASE)
_PREF_VAL_RE = re.compile(r"pref-val\s+(?P<value>\d+)", re.IGNORECASE)
_PREF_RULE_RE = re.compile(r"\bpre\s+(?P<rule>\d+)", re.IGNORECASE)
_DURATION_RE = re.compile(r"^Route Duration:\s*(?P<duration>\S+)\s*$", re.IGNORECASE)
_OUT_IF_RE = re.compile(
    r"^Direct Out-interface:\s*(?P<interface>\S+)\s*$", re.IGNORECASE
)
_ORIG_NH_RE = re.compile(r"^Original nexthop:\s*(?P<next_hop>\S+)\s*$", re.IGNORECASE)
_COMMUNITY_RE = re.compile(r"^Community:\s*(?P<communities>.+)$", re.IGNORECASE)
_AGG_RE = re.compile(
    r"Aggregator:\s*AS\s*(?P<asn>\d+)\s*,\s*Aggregator ID\s*(?P<router>\S+?)(?:,|$)",
    re.IGNORECASE,
)
_NOT_PREFERRED_RE = re.compile(r"not preferred for\s+(?P<reason>[A-Za-z][\w \-]*)", re.IGNORECASE)
_NOT_ADVERTISED_RE = re.compile(r"Not advertised to any peer", re.IGNORECASE)
# mensagem real do VRP para prefixo ausente: `Info: The network does not exist.`
# (verificado em `display bgp routing-table 45.0.0.0 24`, que não está na
# tabela). O texto é curto e genérico de propósito: a engine manda o comando e
# o VRP responde só isso, sem repetir o prefixo — por isso o prefixo vem do
# contexto, e não da saída.
_NOT_IN_TABLE_RE = re.compile(r"Info:\s*The network does not exist", re.IGNORECASE)
# campos que não são flag: saem da lista antes de virar chip
_FIELD_RE = re.compile(
    r"\b(?:origin|localpref|pref-val|pre|validation|atomic-aggregate)\b", re.IGNORECASE
)


def _split_as_path(text: str) -> list[str]:
    """`644970 269194 15169` → lista de ASNs, tirando o sufixo de origem."""
    return [tok for tok in text.split() if tok.isdigit()]


def _parse_flags(text: str) -> list[str]:
    """O VRP embarrega atributos e flags na mesma linha separada por vírgula.

    `localpref 150, pre 20, validation not-found, best, select` → flags. O
    motivo de não preferência sai em campo próprio: é a informação que explica
    por que o caminho perdeu, e como chip solto ela some na lista.
    """
    flags: list[str] = []
    for chunk in text.split(","):
        item = chunk.strip()
        if not item:
            continue
        if _FIELD_RE.search(item) or _NOT_PREFERRED_RE.search(item):
            continue
        flags.append(item)
    return flags


def _parse_path(index: int, block: list[str]) -> dict | None:
    """Monta um caminho a partir do bloco que começa no `From:`."""
    from_line = next((line for line in block if _FROM_RE.match(line.strip())), None)
    if from_line is None:
        return None
    from_match = _FROM_RE.match(from_line.strip())

    as_line = next((line for line in block if _AS_PATH_RE.match(line.strip())), None)
    as_path_text = ""
    flags: list[str] = []
    origin = None
    localpref = None
    pref_val = None
    pref_rule = None
    not_preferred = None
    if as_line is not None:
        rest = _AS_PATH_RE.match(as_line.strip()).group("as_path")
        # o VRP não separa o AS path dos atributos por vírgula: corta no
        # primeiro `,` ou `origin` que vem logo após os números
        partes = re.split(r",\s*|\s+origin\s+", rest, maxsplit=1)
        as_path_text = partes[0].strip()
        # as flags saem do que vem DEPOIS do AS path: a lista de ASNs é
        # separada por espaço e não por vírgula, então o primeiro pedaço do
        # split é o caminho, não uma flag
        tail = partes[1] if len(partes) > 1 else ""
        origin_m = _ORIGIN_RE.search(rest)
        if origin_m:
            origin = origin_m.group("origin").upper()
        localpref_m = _LOCALPREF_RE.search(rest)
        if localpref_m:
            localpref = int(localpref_m.group("value"))
        pref_val_m = _PREF_VAL_RE.search(rest)
        if pref_val_m:
            pref_val = int(pref_val_m.group("value"))
        pref_rule_m = _PREF_RULE_RE.search(rest)
        if pref_rule_m:
            pref_rule = int(pref_rule_m.group("rule"))
        flags = _parse_flags(tail)
        np_m = _NOT_PREFERRED_RE.search(rest)
        if np_m:
            not_preferred = np_m.group("reason").strip()

    aggregations = [
        {"asn": int(m.group("asn")), "router": m.group("router")}
        for line in block
        for m in _AGG_RE.finditer(line)
    ]

    duration_m = _DURATION_RE.match(next((l.strip() for l in block if _DURATION_RE.match(l.strip())), ""))
    out_if_m = _OUT_IF_RE.match(next((l.strip() for l in block if _OUT_IF_RE.match(l.strip())), ""))
    orig_nh_m = _ORIG_NH_RE.match(next((l.strip() for l in block if _ORIG_NH_RE.match(l.strip())), ""))
    community_m = _COMMUNITY_RE.match(
        next((l.strip() for l in block if _COMMUNITY_RE.match(l.strip())), "")
    )
    communities = (
        [c.strip() for c in re.findall(r"<([^>]+)>", community_m.group("communities"))]
        if community_m
        else []
    )

    return {
        "index": index,
        "as_path": _split_as_path(as_path_text),
        "as_path_text": as_path_text,
        "aggregations": aggregations,
        "next_hop": from_match.group("next_hop"),
        "from": from_match.group("next_hop"),
        "originator": from_match.group("originator"),
        "origin": origin,
        "localpref": localpref,
        # pref-val e a regra de preferência do VRP: não são a métrica do IOS
        "pref_val": pref_val,
        "pref_rule": pref_rule,
        "metric": None,
        "weight": None,
        "out_interface": out_if_m.group("interface") if out_if_m else None,
        "original_next_hop": orig_nh_m.group("next_hop") if orig_nh_m else None,
        "duration": duration_m.group("duration") if duration_m else None,
        "communities": communities,
        "not_preferred": not_preferred,
        "flags": flags,
        "best": any(flag.lower() == "best" for flag in flags),
        "is_local": False,
        "pathid": None,
    }


def _split_blocks(lines: list[str]) -> list[list[str]]:
    """Quebra a saída em blocos de caminho, ancorados no `From:`."""
    blocks: list[list[str]] = []
    current: list[str] | None = None
    for line in lines:
        if _FROM_RE.match(line.strip()):
            if current is not None:
                blocks.append(current)
            current = [line]
        elif current is not None:
            current.append(line)
    if current is not None:
        blocks.append(current)
    return blocks


def parse_bgp_prefix_vrp(output: str, context: dict | None = None) -> dict:
    """Estrutura o detalhe de um prefixo BGP do VRP no contrato do IOS."""
    text = output.replace("\r", "")
    requested = (context or {}).get("prefix")

    if _NOT_IN_TABLE_RE.search(text):
        return {
            "type": "bgp_prefix",
            "nos": "huawei_vrp",
            "status": "not_found",
            "prefix": requested,
            "message": "Prefixo não está na tabela BGP",
        }

    entry = _ENTRY_RE.search(text)
    paths_match = _PATHS_RE.search(text)
    if entry is None or paths_match is None:
        raise ParserError("Saída não reconhecida como detalhe de prefixo BGP do VRP")

    # o `From:` abre o bloco; as linhas de cabeçalho antes do primeiro `From:`
    # não pertencem a caminho nenhum
    blocks = _split_blocks(text.splitlines())
    paths = [path for path in (_parse_path(i + 1, b) for i, b in enumerate(blocks)) if path]
    if not paths:
        raise ParserError("Nenhum caminho reconhecido na saída de prefixo BGP do VRP")

    best_index = None
    for path in paths:
        if path["best"]:
            best_index = path["index"]
            break

    return {
        "type": "bgp_prefix",
        "nos": "huawei_vrp",
        "status": "ok",
        "prefix": entry.group("prefix"),
        # o VRP não versiona a entrada nem nomeia a tabela: o IOS diz
        # "version 100803950" e "table default", o VRP não diz nada disso
        "version": None,
        "available": int(paths_match.group("available")),
        "best_index": best_index,
        "table": None,
        "select": int(paths_match.group("select")),
        "router_id": next(
            (m.group(1) for m in [re.search(r"BGP local router ID\s*:\s*(\S+)", text)] if m),
            None,
        ),
        "local_as": next(
            (m.group(1) for m in [re.search(r"Local AS number\s*:\s*(\d+)", text)] if m),
            None,
        ),
        "advertised": not _NOT_ADVERTISED_RE.search(text),
        "update_groups": [],
        "paths": paths,
    }
