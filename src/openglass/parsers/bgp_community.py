"""Parser da busca por Community.

**Cisco IOS / IOS-XE** — `show bgp ipv4 unicast community no-export` (real, em
ASR1000 IOS-XE 16.09):

        Network          Next Hop            Metric LocPrf Weight Path
     *    45.6.136.0/24    187.16.217.33                 200      0 266136 i
     *>                    187.16.217.33                 200      0 266136 i

A leitura da tabela é a mesma do `bgp_as_path` e vem de `bgp_table`.

O que muda aqui é a pergunta. Filtrar por community é responder **quem está
anunciando com essa community** — o filtro já devolveu os prefixos, e a saída
não repete a community em nenhuma coluna. Então o agrupamento útil é por **AS
de origem** (o último AS do caminho), e não por cadeia: é a lista de quem
vaza a community, com o next hop de cada um.

É o que a community significa na prática. Uma consulta por `no-export`
que volta prefixos de 12 ASs distintos está dizendo que 12 upregulation estão
usando a community, e a pergunta seguinte é sempre "quantos prefixos e por qual
caminho".

O `huawei_vrp` **não declara** este parser: o VRP do lab não tem nenhuma
community na tabela (verificado — `no-export`, `65000:1` e `65000:666` voltam
com `Total Number of Routes: 0`, e a configuração só tem `advertise-community`,
sem community atribuída). Sem saída real de community do VRP, um parser aqui
seria chute; a saída fica em texto cru até existir dado para validar.
"""

from openglass.parsers import bgp_table
from openglass.parsers.bgp_table import MAX_ORIGINS, MAX_PREFIXES_PER_ORIGIN

# Teto de payload: a resposta da API não pode ser o gargalo. Sinalizado quando
# o que for cortado.
MAX_NEXT_HOPS = 10


def parse_bgp_community(output: str, context: dict | None = None) -> dict:
    """Estrutura a saída de uma busca por community, agrupada por AS de origem.

    `context` traz o parâmetro validado do comando (`community`), usado para
    mostrar na interface o que foi pesquisado.
    """
    context = context or {}
    routes, declared = bgp_table.read_table(output, "community")

    # AS de origem = último AS do caminho (a borda que anuncia o prefixo).
    # Rota local não tem origem: entra num grupo à parte.
    grouped: dict[str, dict] = {}
    unique_prefixes: set[str] = set()
    for route in routes:
        origin_as = route["as_path"][-1] if route["as_path"] else "local"
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
        # par prefixo/next hop: a tabela da card mostra a rota como o
        # equipamento anunciou, não só o prefixo solto.
        #
        # O mesmo prefixo pode ter várias linhas (uma por next hop) e o melhor
        # caminho nem sempre é a primeira: no equipamento real, o `*>` do
        # 45.6.136.0/24 vem na segunda linha. Deduplicar pela primeira
        # ocorrência perderia o `best` de 23 das 32 rotas, então o flag é
        # acumulado e a linha guardada é a do melhor caminho quando existe.
        index = bucket["_index"].get(route["prefix"])
        if index is None:
            if len(bucket["prefixes"]) < MAX_PREFIXES_PER_ORIGIN:
                bucket["prefixes"].append(route["prefix"])
                bucket["entries"].append(
                    {
                        "prefix": route["prefix"],
                        "next_hop": route["next_hop"],
                        "best": route["best"],
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

    declared_community = context.get("community") or ""
    return {
        "type": "bgp_community",
        "community": declared_community,
        "total_paths": len(routes),
        "total_prefixes": len(unique_prefixes),
        "declared_total": declared,
        "origin_count": len(grouped),
        "best_paths": sum(1 for route in routes if route["best"]),
        "origins": origins,
        "capped_origins": capped_origins,
        "capped_prefixes": capped_prefixes,
    }
