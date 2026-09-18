"""Parser do output de `show ip bgp <prefixo>` do Cisco IOS/IOS-XE.

Converte o detalhe de um prefixo BGP em dados estruturados (paths, AS path,
atributos, flags). Exemplo de entrada:

    BGP routing table entry for 8.8.8.0/24, version 100803950
    Paths: (2 available, best #1, table default)
      Not advertised to any peer
      Refresh Epoch 1
      263009 15169
        10.200.210.129 from 10.200.210.129 (170.84.55.250)
          Origin IGP, localpref 150, valid, external, best
          rx pathid: 0, tx pathid: 0x0
      ...

Saída fora do padrão levanta `ParserError` (a engine mantém o output cru).
Prefixo inexistente retorna `status: "not_found"` em vez de erro.
"""

import re

from openglass.parsers.base import ParserError

_ENTRY_RE = re.compile(
    r"BGP routing table entry for\s+(?P<prefix>\S+?),\s+version\s+(?P<version>\d+)",
    re.IGNORECASE,
)
_PATHS_RE = re.compile(
    r"Paths:\s*\(\s*(?P<available>\d+)\s+available\s*,\s*"
    r"(?:best\s+#(?P<best>\d+)|no\s+best\s+path)"
    r"(?:,\s*table\s+(?P<table>[\w.:-]+))?",
    re.IGNORECASE,
)
_NOT_ADVERTISED_RE = re.compile(r"Not advertised to any peer", re.IGNORECASE)
_ADVERTISED_RE = re.compile(r"Advertised to update-groups", re.IGNORECASE)
_REFRESH_RE = re.compile(r"^\s*Refresh Epoch\b", re.IGNORECASE)
_NH_RE = re.compile(
    r"^\s*(?P<next_hop>\S+)\s+from\s+(?P<from>\S+)"
    r"(?:\s+\((?P<originator>[^)]*)\))?\s*$"
)
_ATTR_RE = re.compile(r"^\s*Origin\s+(?P<origin>\w+)\s*(?P<rest>.*)$", re.IGNORECASE)
_PATHID_RE = re.compile(
    r"rx pathid:\s*(?P<rx>\S+),\s*tx pathid:\s*(?P<tx>\S+)", re.IGNORECASE
)
_AGG_RE = re.compile(
    r"aggregated by\s+(?P<asn>\d+)\s+(?P<router>[^)\s]+)", re.IGNORECASE
)
_NUMERIC_RE = re.compile(r"^\d+$")
_AS_SET_RE = re.compile(r"^\{.*\}$")
_GROUPS_RE = re.compile(r"^\s*\d+(?:\s+\d+)*\s*$")
_NOT_IN_TABLE_RE = re.compile(r"%\s*Network not in table", re.IGNORECASE)


def _int_field(text: str, name: str) -> int | None:
    match = re.search(rf"\b{name}\s+(\d+)", text, re.IGNORECASE)
    return int(match.group(1)) if match else None


def _parse_as_path(text: str) -> list[str]:
    clean = _AGG_RE.sub("", text)
    clean = re.sub(r"\([^)]*\)", "", clean)
    tokens = clean.replace(",", " ").split()
    return [tok for tok in tokens if _NUMERIC_RE.match(tok) or _AS_SET_RE.match(tok)]


def _parse_path(index: int, lines: list[str]) -> dict | None:
    """Monta um path a partir das linhas de um bloco."""
    attr_line = next((line for line in lines if _ATTR_RE.match(line)), None)
    if attr_line is None:
        return None

    nh_match = next((_NH_RE.match(line) for line in lines if _NH_RE.match(line)), None)
    pathid_match = next(
        (_PATHID_RE.search(line) for line in lines if _PATHID_RE.search(line)), None
    )

    if nh_match is None:
        # path local/origem: "0.0.0.0 from 0.0.0.0" ainda casa; sem "from" é inválido
        return None

    as_path_text = "Local"
    for i, line in enumerate(lines):
        if _NH_RE.match(line):
            for candidate in reversed(lines[:i]):
                token = candidate.strip()
                if not token or _REFRESH_RE.match(candidate) or _ATTR_RE.match(candidate):
                    continue
                if _PATHID_RE.search(candidate):
                    continue
                as_path_text = token
                break
            break

    attr = _ATTR_RE.match(attr_line)
    rest = attr.group("rest")
    localpref = _int_field(rest, "localpref")
    metric = _int_field(rest, "metric")
    weight = _int_field(rest, "weight")
    cleaned = re.sub(
        r",?\s*(?:localpref|metric|weight)\s+\d+", "", rest, flags=re.IGNORECASE
    )
    flags = [tok.strip() for tok in cleaned.split(",") if tok.strip()]

    aggregations = [
        {"asn": int(m.group("asn")), "router": m.group("router")}
        for m in _AGG_RE.finditer(as_path_text)
    ]

    return {
        "index": index,
        "as_path": _parse_as_path(as_path_text),
        "as_path_text": as_path_text,
        "aggregations": aggregations,
        "next_hop": nh_match.group("next_hop"),
        "from": nh_match.group("from"),
        "originator": nh_match.group("originator"),
        "origin": attr.group("origin").upper(),
        "localpref": localpref,
        "metric": metric,
        "weight": weight,
        "flags": flags,
        "best": any(flag.lower() == "best" for flag in flags),
        "is_local": as_path_text.lower() == "local"
        or any(flag.lower() == "local" for flag in flags),
        "pathid": (
            {"rx": pathid_match.group("rx"), "tx": pathid_match.group("tx")}
            if pathid_match
            else None
        ),
    }


def _split_path_blocks(lines: list[str]) -> list[list[str]]:
    """Separa as linhas dos paths, tratando o cabeçalho de advertisement."""
    blocks: list[list[str]] = []
    current: list[str] = []
    in_groups = False

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if _REFRESH_RE.match(line):
            if current:
                blocks.append(current)
                current = []
            in_groups = False
            continue
        if _NOT_ADVERTISED_RE.search(line):
            continue
        if _ADVERTISED_RE.search(line):
            in_groups = True
            continue
        if in_groups:
            if _GROUPS_RE.match(line):
                continue
            in_groups = False
        current.append(line)

    if current:
        blocks.append(current)
    return blocks


def parse_bgp_prefix(output: str, context: dict | None = None) -> dict:
    """Estrutura o detalhe de um prefixo BGP."""
    text = output.replace("\r", "")
    requested = (context or {}).get("prefix")

    if _NOT_IN_TABLE_RE.search(text):
        return {
            "type": "bgp_prefix",
            "status": "not_found",
            "prefix": requested,
            "message": "Prefixo não está na tabela BGP",
        }

    entry = _ENTRY_RE.search(text)
    paths_match = _PATHS_RE.search(text)
    if not entry or not paths_match:
        raise ParserError("Saída não reconhecida como detalhe de prefixo BGP")

    lines = text.splitlines()
    paths_start = next(
        (i for i, line in enumerate(lines) if _PATHS_RE.search(line)), len(lines)
    )
    after = lines[paths_start + 1 :]

    advertised: bool | None = None
    update_groups: list[str] = []
    for i, line in enumerate(after):
        if _NOT_ADVERTISED_RE.search(line):
            advertised = False
        elif _ADVERTISED_RE.search(line):
            advertised = True
            for candidate in after[i + 1 :]:
                if _GROUPS_RE.match(candidate):
                    update_groups.extend(re.findall(r"\d+", candidate))
                else:
                    break

    paths: list[dict] = []
    for block in _split_path_blocks(after):
        path = _parse_path(len(paths) + 1, block)
        if path is not None:
            paths.append(path)

    if not paths:
        raise ParserError("Nenhum path reconhecido na saída de prefixo BGP")

    best_index = int(paths_match.group("best")) if paths_match.group("best") else None
    return {
        "type": "bgp_prefix",
        "status": "ok",
        "prefix": entry.group("prefix"),
        "version": int(entry.group("version")),
        "available": int(paths_match.group("available")),
        "best_index": best_index,
        "table": paths_match.group("table"),
        "advertised": advertised,
        "update_groups": update_groups,
        "paths": paths,
    }
