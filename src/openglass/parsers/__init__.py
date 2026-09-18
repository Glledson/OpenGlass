"""Registro de parsers de output por comando.

O `parser:` declarado em nodes/<nos>.yaml aponta para uma chave daqui. Novos
parsers (traceroute, bgp, ...) entram em PARSERS.

Assinatura de um parser: ``parser(output: str, context: dict | None) -> dict``,
onde `context` traz os parâmetros validados do comando (ex.: o prefixo).
"""

from typing import Callable

from openglass.parsers import bgp_prefix, ping
from openglass.parsers.base import ParserError

Parser = Callable[..., dict]

PARSERS: dict[str, Parser] = {
    "ping": ping.parse_ping,
    "bgp_prefix": bgp_prefix.parse_bgp_prefix,
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
