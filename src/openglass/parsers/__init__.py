"""Registro de parsers de output por comando.

O `parser:` declarado em nodes/<nos>.yaml aponta para uma chave daqui. Novos
parsers (traceroute, bgp, ...) entram em PARSERS.
"""

from typing import Callable

from openglass.parsers import ping
from openglass.parsers.base import ParserError

PARSERS: dict[str, Callable[[str], dict]] = {
    "ping": ping.parse_ping,
}
PARSER_NAMES = frozenset(PARSERS)


def get_parser(name: str) -> Callable[[str], dict]:
    try:
        return PARSERS[name]
    except KeyError as exc:
        raise ParserError(f"Parser desconhecido: {name!r}") from exc


def parse(name: str, output: str) -> dict:
    """Executa o parser `name` sobre `output`."""
    return get_parser(name)(output)
