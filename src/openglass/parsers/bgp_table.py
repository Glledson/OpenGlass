"""Leitura da tabela BGP que o IOS e o VRP imprimem.

`bgp-as-path` e `bgp-community` devolvem a **mesma tabela** — o IOS-XE com
`show bgp ... quote-regexp` e `show bgp ... community`, o VRP com `display bgp
routing-table`. A diferença entre os dois parsers é o que eles fazem com as
rotas; ler a tabela em si é a mesma coisa, e ficar aqui evita duplicar a parte
delicada.

Duas armadilhas medidas em equipamento real, e as duas motivam ler por
**colunas do cabeçalho** em vez de tokenizar a linha:

1. O caminho não é a última palavra da linha. `0 265269 13335` (peso + 2 AS) e
   `200 0 13335` (métrica + peso + 1 AS) são indistinguíveis por token, então o
   corte tem que ser na coluna `Path`/`Path/Ogn` do cabeçalho.
2. O IOS **realoca a largura das colunas** conforme o conteúdo da tabela, e usa
   a coluna Network **vazia** em caminhos adicionais e rotas suprimidas. Sem
   tratar isso, o endereço do next hop era lido como se fosse o prefixo, e o
   prefixo verdadeiro se perdia.

Diferenças do VRP: o código de origem vem grudado no último AS (`64512i`) e o
prefixo pode ter a marca `N` (rota agregada) antes do endereço.
"""

import re
from dataclasses import dataclass

from openglass.parsers.base import ParserError

# Cabeçalho da tabela → nome da coluna que abre o AS Path.
_PATH_COLUMNS = ("Path/Ogn", "Path")
_NEXT_HOP_COLUMNS = ("Next Hop", "NextHop")

# Linha de rota começa pelos códigos de estado (`*>`, `s`, `d`, `h`...). O
# espaço entra como preenchimento das colunas, mas a linha precisa ter pelo
# menos um código de verdade — senão o cabeçalho e a legenda de status, que
# começam com espaços, seriam candidatos.
_CODES = "*>shdrixa mLcfSt"
ROUTE_RE = re.compile(rf"^\s*(?=[{_CODES}]*[{_CODES.strip()}])[{_CODES}]{{1,6}}\s")

# Um AS no caminho: número, AS set `(1 2 3)` ou confederation set `{1 2 3}`.
AS_TOKEN = r"(?:\d+|\([^)]*\)|\{[^}]*\})"
AS_FULL_RE = re.compile(rf"^(?:{AS_TOKEN})$")
_ORIGIN_RE = re.compile(r"(?P<last>\d+|[\(\{][\)\}])(?P<origin>[ie?])$")

# Endereço (v4/v6) usado para achar o next hop entre colunas numéricas soltas.
_ADDRESS_RE = re.compile(
    r"^(?:[0-9A-Fa-f]*:){2,}[0-9A-Fa-f:]*|[0-9A-Fa-f]*(?:\.[0-9A-Fa-f]+)+:?$"
)

# Total declarado pelo próprio equipamento (VRP). O IOS não traz essa linha.
TOTAL_RE = re.compile(r"Total Number of Routes:\s*(\d+)", re.IGNORECASE)

ORIGIN_LABELS = {"i": "IGP", "e": "EGP", "?": "incompleto"}

# Teto de payload: a tabela inteira pode ter centenas de milhares de prefixos e
# o JSON não pode ser o gargalo da consulta. O que for cortado é sinalizado.
MAX_CHAINS = 60
MAX_PREFIXES_PER_CHAIN = 250
MAX_PREFIXES_PER_ORIGIN = 250
# Teto de grupos (ASs de origem) na busca por community. Vive aqui, e não no
# parser do IOS, para os dois lados compartilharem o teto sem se importar.
MAX_ORIGINS = 40


@dataclass(frozen=True)
class Columns:
    """Posições das colunas da tabela, lidas do cabeçalho."""

    network: int
    next_hop: int
    path: int


def find_header(lines: list[str]) -> Columns | None:
    """Localiza a linha de cabeçalho e devolve os deslocamentos das colunas.

    `Path/Ogn` (VRP) é testado antes de `Path` (IOS) para não casar com o
    prefixo do nome da coluna.
    """
    for line in lines:
        if "Network" not in line:
            continue
        path = next((line.find(name) for name in _PATH_COLUMNS if name in line), -1)
        if path == -1:
            continue
        next_hop = next(
            (line.find(name) for name in _NEXT_HOP_COLUMNS if name in line), -1
        )
        return Columns(
            network=line.index("Network"),
            next_hop=next_hop if next_hop != -1 else path,
            path=path,
        )
    return None


def field(line: str, start: int, end: int) -> str:
    """Recorta uma coluna da linha, nas duas pontas."""
    if end <= start or start >= len(line):
        return ""
    return line[start:end]


def tokenize_path(value: str) -> list[str]:
    """Divide o campo do caminho em tokens, sem quebrar AS sets.

    Um AS set é um único token mesmo com espaço (`(13335 15169)`), então o
    `str.split()` não serve: ele quebraria o set em pedaços e o caminho
    perderia o salto.
    """
    tokens: list[str] = []
    buffer: list[str] = []
    depth = 0
    for char in value:
        if char in "({":
            depth += 1
        elif char in ")}":
            depth = max(0, depth - 1)
        if char.isspace() and depth == 0:
            if buffer:
                tokens.append("".join(buffer))
                buffer = []
        else:
            buffer.append(char)
    if buffer:
        tokens.append("".join(buffer))
    return tokens


def split_origin(token: str) -> tuple[str, str | None]:
    """Separa o código de origem grudado no token (só o VRP gruda)."""
    match = _ORIGIN_RE.match(token)
    if match is None:
        return token, None
    return match.group("last"), match.group("origin")


def parse_path_field(value: str) -> tuple[list[str], str | None, bool]:
    """Coluna do AS Path → (ASs, origem, é local)."""
    tokens = tokenize_path(value)
    if not tokens:
        return [], None, False
    if tokens[0].lower().startswith("local"):
        return [], None, True

    origin: str | None = None
    # origem solta no fim (IOS) ou grudada no último AS (VRP)
    if tokens[-1] in ("i", "e", "?"):
        origin = tokens[-1]
        tokens = tokens[:-1]
    else:
        last, glued = split_origin(tokens[-1])
        if glued is not None:
            tokens[-1] = last
            origin = glued

    as_path = [token for token in tokens if AS_FULL_RE.match(token)]
    return as_path, origin, False


def iter_routes(lines: list[str], columns: Columns) -> list[dict]:
    """Linhas da tabela → rotas, herdando o prefixo nos caminhos adicionais.

    Cada rota sai com `prefix`, `next_hop`, `as_path`, `origin`, `best` e
    `is_local`. Linhas que não são rota (cabeçalho, legenda de status, preâmbulo)
    são ignoradas aqui para os dois parsers aproveitarem a mesma filtragem.
    """
    routes: list[dict] = []
    last_prefix: str | None = None
    for line in lines:
        if not ROUTE_RE.match(line) or len(line) <= columns.path:
            continue
        # A coluna Network vazia é a marca de caminho adicional: o prefixo é o
        # da rota anterior. Conferir a posição evita ler o next hop como prefixo.
        has_prefix = bool(field(line, columns.network, columns.next_hop).strip())
        region = field(line, columns.network, columns.path).split()
        if has_prefix:
            # a marca de agregação do VRP vem antes do prefixo (`N`)
            while region and re.fullmatch(r"[A-Z]", region[0]):
                region = region[1:]
            prefix = region[0] if region else None
            region = region[1:]
        else:
            prefix = last_prefix
        if prefix is None:
            continue
        last_prefix = prefix

        # O next hop é o primeiro endereço da região; as colunas numéricas
        # (métrica, localpref, peso) são inteiros e ficam de fora.
        next_hop = next((t for t in region if _ADDRESS_RE.match(t)), None)
        if next_hop is None:
            continue

        as_path, origin, is_local = parse_path_field(line[columns.path :])
        if not as_path and not is_local:
            continue
        routes.append(
            {
                "prefix": prefix,
                "next_hop": next_hop,
                "as_path": as_path,
                "origin": origin,
                "best": ">" in line[: columns.network],
                "is_local": is_local,
            }
        )
    return routes


def read_table(output: str, what: str) -> tuple[list[dict], int | None]:
    """Saída bruta → (rotas, total declarado pelo equipamento).

    `what` nomeia a tabela na mensagem de erro (`"AS Path"`, `"community"`).
    """
    text = output.replace("\r", "")
    lines = text.splitlines()
    columns = find_header(lines)
    if columns is None:
        raise ParserError(
            f"Saída não reconhecida como tabela de {what} "
            "(cabeçalho 'Path'/'Path/Ogn' não encontrado)"
        )
    routes = iter_routes(lines, columns)
    if not routes:
        raise ParserError(f"Tabela de {what} sem rotas interpretáveis")
    declared = TOTAL_RE.search(text)
    return routes, int(declared.group(1)) if declared else None


# ---------------------------------------------------------------------------
# Tabela de community do VRP
# ---------------------------------------------------------------------------
# O VRP imprime a community **de cada prefixo** numa coluna própria
# (`PrefVal Community`, com os valores em `<65001:1000>`) e, em compensação, não
# imprime coluna de AS path. Por isso `read_table` não serve aqui: ela exige
# `Path`/`Path/Ogn` no cabeçalho. É a forma medida no equipamento:
#
#          Network            NextHop      MED   LocPrf  PrefVal Community
#   *>     198.51.100.0/23    0.0.0.0        0  10000000       0  <65001:1000>, ...
#
# O IOS faz o contrário: repete o filtro no AS path e não tem coluna de
# community, então quem tem coluna `Community` é o VRP.

_COMMUNITY_RE = re.compile(r"<([^<>]+)>")


@dataclass(frozen=True)
class CommunityColumns:
    """Posições das colunas da tabela de community, lidas do cabeçalho."""

    network: int
    next_hop: int
    community: int


def find_community_header(lines: list[str]) -> CommunityColumns | None:
    """Cabeçalho da tabela de community: tem `Community` e não tem AS path."""
    for line in lines:
        if "Network" not in line or "Community" not in line:
            continue
        if any(name in line for name in _PATH_COLUMNS):
            continue  # essa é a tabela com AS path, lida por read_table
        next_hop = next(
            (line.find(name) for name in _NEXT_HOP_COLUMNS if name in line), -1
        )
        return CommunityColumns(
            network=line.index("Network"),
            next_hop=next_hop if next_hop != -1 else line.index("Community"),
            community=line.index("Community"),
        )
    return None


def iter_community_routes(
    lines: list[str], columns: CommunityColumns
) -> list[dict]:
    """Linhas da tabela de community → rotas, com a lista de communities.

    Sem coluna de AS path não há como saber o AS que originou a rota: a entrada
    sai com `as_path` vazio e `is_local`, que é o que a ausência de caminho
    significa numa tabela BGP.
    """
    routes: list[dict] = []
    last_prefix: str | None = None
    for line in lines:
        if not ROUTE_RE.match(line) or len(line) <= columns.community:
            continue
        has_prefix = bool(field(line, columns.network, columns.next_hop).strip())
        region = field(line, columns.network, columns.community).split()
        if has_prefix:
            while region and re.fullmatch(r"[A-Z]", region[0]):
                region = region[1:]  # marca de agregação do VRP
            prefix = region[0] if region else None
            region = region[1:]
        else:
            prefix = last_prefix
        if prefix is None:
            continue
        last_prefix = prefix

        next_hop = next((t for t in region if _ADDRESS_RE.match(t)), None)
        if next_hop is None:
            continue

        communities = _COMMUNITY_RE.findall(line[columns.community :])
        routes.append(
            {
                "prefix": prefix,
                "next_hop": next_hop,
                "as_path": [],
                "origin": None,
                "best": ">" in line[: columns.network],
                "is_local": True,
                "communities": communities,
            }
        )
    return routes


def read_community_table(output: str, what: str) -> tuple[list[dict], int | None]:
    """Saída de busca por community no VRP → (rotas, total declarado).

    `Total Number of Routes: 0` é resposta legítima, não erro: significa que
    ninguém está usando aquela community. Nesse caso o VRP nem imprime a
    tabela, e o retorno vazio é o que faz a card mostrar o estado vazio em vez
    de cair em texto cru.
    """
    text = output.replace("\r", "")
    lines = text.splitlines()
    declared_match = TOTAL_RE.search(text)
    declared = int(declared_match.group(1)) if declared_match else None

    columns = find_community_header(lines)
    if columns is None:
        if declared == 0:
            return [], 0
        raise ParserError(
            f"Saída não reconhecida como tabela de {what} "
            "(cabeçalho 'Community' sem coluna de AS path não encontrado)"
        )
    routes = iter_community_routes(lines, columns)
    if not routes and declared != 0:
        raise ParserError(f"Tabela de {what} sem rotas interpretáveis")
    return routes, declared
