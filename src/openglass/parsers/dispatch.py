"""Escolhe o parser certo pelo formato da saída, não pelo NOS declarado.

`ping`, `traceroute` e `bgp_prefix` têm uma versão para o Cisco IOS e outra para
o Huawei VRP. As saídas são incompatíveis — o VRP escreve `4 ms` e uma linha
por sonda no ping, decora endereço por sonda no tracert e abre bloco por
caminho no detalhe BGP — então não dá para ler as duas com o mesmo código.

A decisão é por assinatura de texto, não pelo `nos` do device: o que o
equipamento devolve é a evidência. Um profile que declarasse o NOS errado ainda
renderiza certo, e a mesma saída cai sempre no mesmo ramo, que é o que faz o
teste valer alguma coisa.

Cada ramo de VRP levanta `ParserError` quando não reconhece o formato, então o
fallback é o mesmo dos dois lados: a engine mantém o texto cru. Na dúvida entre
os dois, tenta-se o IOS, que é o NOS cuja saída já tem teste de regressão.

Os imports são feitos direto do submódulo, com alias: aqui o nome do módulo e
o da função se cruzam, e um `from ... import ping` seguido de `def ping` faria a
função sombrear o módulo em tempo de execução.
"""

import re

from openglass.parsers.bgp_prefix import parse_bgp_prefix as _ios_bgp_prefix
from openglass.parsers.bgp_community import parse_bgp_community as _ios_bgp_community
from openglass.parsers.ping import parse_ping as _ios_ping
from openglass.parsers.traceroute import parse_traceroute as _ios_traceroute
from openglass.parsers.vrp_bgp_community import parse_bgp_community_vrp
from openglass.parsers.vrp_bgp_prefix import parse_bgp_prefix_vrp
from openglass.parsers.vrp_ping import parse_ping_vrp
from openglass.parsers.vrp_traceroute import parse_traceroute_vrp

# Assinaturas do VRP. Cada uma tem de ser impossível numa saída do IOS: o IOS
# não escreve "packet(s) transmitted", não anuncia o destino como "traceroute to
# X(X), max hops:" e, para prefixo ausente, responde `% Network not in table`
# (o VRP responde `Info: The network does not exist.`).
#
# A segunda alternativa do BGP existe porque o prefixo ausente não tem cabeçalho
# de entrada: sem ela, uma consulta que volta vazia cairia no parser do IOS e
# apareceria como texto cru em vez do aviso de "não encontrado".
_VRP_PING_RE = re.compile(r"\bpacket\(s\)\s+transmitted", re.IGNORECASE)
_VRP_TRACERT_RE = re.compile(
    r"^\s*traceroute to\s+\S+\(\S+\)\s*,", re.IGNORECASE | re.MULTILINE
)
_VRP_BGP_PREFIX_RE = re.compile(
    r"BGP routing table entry information of|Info:\s*The network does not exist",
    re.IGNORECASE,
)
# Tabela de community: o VRP é o único dos dois que imprime uma coluna
# `Community` com os valores entre colchetes angulares. O IOS repete o filtro
# no AS path e não tem essa coluna, então a assinatura é segura. O
# `Total Number of Routes: 0` entra junto porque community sem uso no VRP não
# vem com tabela nenhuma — sem ele, essa resposta cairia no parser do IOS e
# viraria erro em vez do estado vazio da card.
_VRP_BGP_COMMUNITY_RE = re.compile(
    r"\bPrefVal\s+Community\b|<[A-Za-z0-9_.:*-]+>|Total Number of Routes:\s*0",
    re.IGNORECASE,
)


def parse_ping(output: str, context: dict | None = None) -> dict:
    """Ping do IOS (`!!!!!`, 5 packets transmitted) ou do VRP (`4 ms` por sonda)."""
    if _VRP_PING_RE.search(output):
        return parse_ping_vrp(output, context)
    return _ios_ping(output, context)


def parse_traceroute(output: str, context: dict | None = None) -> dict:
    """`traceroute` do IOS ou `tracert` do VRP, com endereço por sonda."""
    if _VRP_TRACERT_RE.search(output):
        return parse_traceroute_vrp(output, context)
    return _ios_traceroute(output, context)


def parse_bgp_prefix(output: str, context: dict | None = None) -> dict:
    """Detalhe de prefixo BGP do IOS (`next hop from ...`) ou do VRP (`From: ...`)."""
    if _VRP_BGP_PREFIX_RE.search(output):
        return parse_bgp_prefix_vrp(output, context)
    return _ios_bgp_prefix(output, context)


def parse_bgp_community(output: str, context: dict | None = None) -> dict:
    """Busca por community do IOS (`Weight Path`) ou do VRP (`PrefVal Community`)."""
    if _VRP_BGP_COMMUNITY_RE.search(output):
        return parse_bgp_community_vrp(output, context)
    return _ios_bgp_community(output, context)
