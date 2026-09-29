"""Registro de parsers de output por comando.

O `parser:` declarado em nodes/<nos>.yaml aponta para uma chave daqui. Novos
parsers (traceroute, bgp, ...) entram em PARSERS.

Assinatura de um parser: ``parser(output: str, context: dict | None) -> dict``,
onde `context` traz os parâmetros validados do comando (ex.: o prefixo).

Um nome de parser pode atender a mais de um NOS. `ping`, `traceroute`,
`bgp_prefix` e `bgp_community` são lidos por `dispatch.py`, que decide pelo
**formato da saída** e não pelo NOS declarado: o texto que o equipamento devolve
é a evidência, e depender do rótulo do profile faria a card de um comando de VRP
só aparecer se alguém acertasse o NOS na configuração.

`bgp_as_path` não passa por lá: o IOS usa `quote-regexp` e aceita vários AS na
mesma consulta, o VRP usa `regular-expression` e aceita um só — a diferença é de
montagem de comando, não de leitura, e o mesmo parser dos dois lados é o que
mantém a card única.
"""

from typing import Callable

from openglass.parsers import (
    bgp_as_path,
    bgp_prefix,
    dispatch,
    ping,
    traceroute,
)
from openglass.parsers.base import ParserError

Parser = Callable[..., dict]

PARSERS: dict[str, Parser] = {
    "ping": dispatch.parse_ping,
    "bgp_prefix": dispatch.parse_bgp_prefix,
    "traceroute": dispatch.parse_traceroute,
    "bgp_as_path": bgp_as_path.parse_bgp_as_path,
    "bgp_community": dispatch.parse_bgp_community,
}
PARSER_NAMES = frozenset(PARSERS)


def get_parser(name: str) -> Parser:
    try:
        return PARSERS[name]
    except KeyError as exc:
        raise ParserError(f"Parser desconhecido: {name!r}") from exc


def parse(name: str, output: str, context: dict | None = None) -> dict:
    """Executa o parser `name` sobre `output` (com contexto opcional)."""
    return get_parser(name)(output, context)
