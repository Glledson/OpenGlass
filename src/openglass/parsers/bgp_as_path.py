"""Parser da busca por AS Path.

Cobre os dois formatos de saída que a engine gera (verificado em equipamento):

**Cisco IOS / IOS-XE** — `show bgp ipv4 unicast quote-regexp "(_13335_)"`:

        Network          Next Hop            Metric LocPrf Weight Path
     *>   1.0.0.0/24       10.0.0.241                             0 265269 13335 i
     *>                    10.0.0.241                             0 265269 13335 i
     *    104.22.10.0/24   187.16.219.111                200      0 13335 i

**Huawei VRP** — `display bgp routing-table regular-expression _13335_`:

        Network            NextHop            MED  LocPrf PrefVal Path/Ogn
     *>   N 187.76.192.104/29  192.0.2.221        150      0      0  644970 7738 64512i

A leitura da tabela (colunas, prefixo herdado nos caminhos adicionais, AS sets,
origem do VRP grudada) está em `bgp_table`, compartilhada com `bgp_community`.
Este módulo cuida do que é específico do AS Path: **agrupar por cadeia**, porque
a pergunta de uma consulta "quem passa por este AS" é quais caminhos existem, e
não uma lista de milhares de prefixos.

Saída fora do padrão levanta `ParserError` (a engine mantém o texto cru).
"""

from openglass.parsers import bgp_table
from openglass.parsers.bgp_table import MAX_CHAINS, MAX_PREFIXES_PER_CHAIN


def parse_bgp_as_path(output: str, context: dict | None = None) -> dict:
    """Estrutura a saída de uma busca por AS Path.

    `context` traz os parâmetros validados do comando (`asn`) e é usado só para
    identificar, na apresentação, qual AS foi pesquisado.
    """
    context = context or {}
    routes, declared = bgp_table.read_table(output, "AS Path")

    grouped: dict[tuple[str, ...], dict] = {}
    unique_prefixes: set[str] = set()
    for route in routes:
        key = tuple(route["as_path"])
        unique_prefixes.add(route["prefix"])
        bucket = grouped.get(key)
        if bucket is None:
            bucket = {
                "as_path": route["as_path"],
                "origin": route["origin"],
                "is_local": route["is_local"],
                "next_hop": route["next_hop"],
                "best": route["best"],
                "paths": 0,
                "prefixes": [],
                "prefixes_truncated": False,
                "_seen": set(),
            }
            grouped[key] = bucket
        bucket["paths"] += 1
        bucket["best"] = bucket["best"] or route["best"]
        if route["prefix"] not in bucket["_seen"]:
            bucket["_seen"].add(route["prefix"])
            if len(bucket["prefixes"]) < MAX_PREFIXES_PER_CHAIN:
                bucket["prefixes"].append(route["prefix"])
            else:
                bucket["prefixes_truncated"] = True

    for bucket in grouped.values():
        bucket.pop("_seen")

    chains = sorted(grouped.values(), key=lambda c: (-c["paths"], len(c["as_path"])))
    capped_chains = len(chains) > MAX_CHAINS
    if capped_chains:
        chains = chains[:MAX_CHAINS]
    capped_prefixes = any(c["prefixes_truncated"] for c in chains)

    return {
        "type": "bgp_as_path",
        "asn": context.get("asn") or "",
        "total_paths": len(routes),
        "total_prefixes": len(unique_prefixes),
        "declared_total": declared,
        "unique_chains": len(grouped),
        "longest_path": max((len(c["as_path"]) for c in chains), default=0),
        "asn_count": len({a for route in routes for a in route["as_path"]}),
        "chains": chains,
        "capped_chains": capped_chains,
        "capped_prefixes": capped_prefixes,
    }
