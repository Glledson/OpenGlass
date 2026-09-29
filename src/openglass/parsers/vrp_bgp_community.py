"""Parser da busca por Community no Huawei VRP.

**Huawei VRP** — `display bgp routing-table community 65001:40100` (real, em
VRP8):

         Network            NextHop      MED        LocPrf    PrefVal Community

     *>     198.51.100.0/23    0.0.0.0        0          10000000   0      <65001:1000>, <65001:10301>, <65001:10400>, <65001:20100>, <65001:40100>
     *>     198.51.100.0/24    0.0.0.0        0          10000000   0      <65001:1000>, <65001:40100>
     *>     198.51.101.0/24    0.0.0.0        0          10000000   0      <65001:1000>, <65001:40100>

O IOS e o VRP respondem à mesma pergunta de formas incompatíveis, então este
módulo devolve a **mesma forma de payload** de `bgp_community` para que a card
seja uma só. O que muda é de onde vem o agrupamento:

- No IOS, a saída repete o AS path e **não** tem coluna de community. Quem
  anuncia com a community é deduzido do último AS do caminho.
- No VRP, a saída **tem** coluna de community (com todas as communities de cada
  prefixo, não só a consultada) e **não** tem coluna de AS path. Não existe AS
  de origem para ler: as rotas que o filtro traz aqui são as da RIB, e uma
  entrada sem caminho foi originada localmente. Por isso tudo cai no grupo
  `local`, que a card já sabia renderizar.

O ganho real do VRP fica em `entry["communities"]`: a saída traz a lista
inteira de communities de cada prefixo, então a card mostra o contexto de quem
segurou o prefixo junto com a community que motivou a busca.

Uma community que ninguém usa responde `Total Number of Routes: 0` e não vem
tabela nenhuma. Isso é resposta, não erro: `read_community_table` devolve lista
vazia e a card mostra o estado vazio.
"""

from openglass.parsers.bgp_table import (
    MAX_ORIGINS,
    MAX_PREFIXES_PER_ORIGIN,
    read_community_table,
)

# Teto de next hops por grupo, igual ao do IOS: o JSON não pode ser o gargalo.
MAX_NEXT_HOPS = 10


def parse_bgp_community_vrp(output: str, context: dict | None = None) -> dict:
    """Estrutura a saída de uma busca por community no VRP.

    `context` traz o parâmetro validado do comando (`community`), usado para
    mostrar na interface o que foi pesquisado.
    """
    context = context or {}
    routes, declared = read_community_table(output, "community")

    # Sem coluna de AS path não há origem para ler: tudo que o filtro devolveu
    # foi originado no próprio roteador (next hop 0.0.0.0 confirma).
    grouped: dict[str, dict] = {}
    unique_prefixes: set[str] = set()
    for route in routes:
        origin_as = "local"
        unique_prefixes.add(route["prefix"])
        bucket = grouped.get(origin_as)
        if bucket is None:
            bucket = {
                "origin_as": origin_as,
                "is_local": route["is_local"],
                "next_hops": [],
                "next_hops_truncated": False,
                "entries": [],
                "best_paths": 0,
                "paths": 0,
                "prefixes": [],
                "prefixes_truncated": False,
                "_index": {},
                "_hops": set(),
            }
            grouped[origin_as] = bucket
        bucket["paths"] += 1
        bucket["best_paths"] += int(route["best"])
        if route["next_hop"] not in bucket["_hops"]:
            bucket["_hops"].add(route["next_hop"])
            if len(bucket["next_hops"]) < MAX_NEXT_HOPS:
                bucket["next_hops"].append(route["next_hop"])
            else:
                bucket["next_hops_truncated"] = True
        index = bucket["_index"].get(route["prefix"])
        if index is None:
            if len(bucket["prefixes"]) < MAX_PREFIXES_PER_ORIGIN:
                bucket["prefixes"].append(route["prefix"])
                bucket["entries"].append(
                    {
                        "prefix": route["prefix"],
                        "next_hop": route["next_hop"],
                        "best": route["best"],
                        "communities": route["communities"],
                    }
                )
                bucket["_index"][route["prefix"]] = len(bucket["entries"]) - 1
            else:
                bucket["prefixes_truncated"] = True
        elif route["best"] and not bucket["entries"][index]["best"]:
            bucket["entries"][index]["best"] = True
            bucket["entries"][index]["next_hop"] = route["next_hop"]

    for bucket in grouped.values():
        bucket.pop("_index")
        bucket.pop("_hops")

    origins = sorted(grouped.values(), key=lambda o: (-o["paths"], o["origin_as"]))
    capped_origins = len(origins) > MAX_ORIGINS
    if capped_origins:
        origins = origins[:MAX_ORIGINS]
    capped_prefixes = any(o["prefixes_truncated"] for o in origins)

    return {
        "type": "bgp_community",
        "nos": "huawei_vrp",
        "community": context.get("community") or "",
        "total_paths": len(routes),
        "total_prefixes": len(unique_prefixes),
        "declared_total": declared,
        "origin_count": len(grouped),
        "best_paths": sum(1 for route in routes if route["best"]),
        "origins": origins,
        "capped_origins": capped_origins,
        "capped_prefixes": capped_prefixes,
    }
