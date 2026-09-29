"""Camada de segurança: whitelist de comandos e sanitização de parâmetros.

Regras:
- Nenhum comando arbitrário é aceito: só comandos pré-definidos (commands.py).
- Parâmetros (ex.: IP de destino no ping) passam por sanitização para impedir
  injeção de comando dentro da sessão SSH (ex.: "8.8.8.8; show running-config").
- Métricas de validação com validação rigorosa de IP/prefixo.
"""

import ipaddress
import re

# Caracteres que podem quebrar a sessão/encadear comandos numa CLI de rede.
# Vale para qualquer entrada do USUÁRIO (parâmetros).
FORBIDDEN_CHARS = set(";&|`$!(){}[]<>'\"\\\n\r")

# Caracteres proibidos no COMANDO FINAL montado. O template vem da config do
# admin (nodes/*.yaml), então pipes (`| include`, `| no-more`), aspas
# (`quote-regexp "..."`, `vtysh -c "..."`) e metacaracteres de regex são
# permitidos. Continuam bloqueados apenas separadores/encadeamento (; &),
# substituição de linha, redirecionamento e `$` (placeholder não resolvido).
# Parâmetros do usuário seguem restritos a FORBIDDEN_CHARS pelos validadores.
FORBIDDEN_TEMPLATE_CHARS = set(";&`$<>\n\r")

# Comunidade BGP: `aa:nn` (16 bits cada), nomes bem conhecidos (no-export,
# internet) e listas curtas separadas por espaço.
_COMMUNITY_RE = re.compile(r"^[A-Za-z0-9:._-]+(?:\s+[A-Za-z0-9:._-]+)*$")
_COMMUNITY_NUMBER_RE = re.compile(r"^(?P<aa>\d+):(?P<nn>\d+)$")
_COMMUNITY_MAX = 65535

# AS number: 2 ou 4 bytes. Vários AS podem vir separados por vírgula/espaço.
_ASN_RE = re.compile(r"^\d{1,10}$")
MAX_ASN = 4294967295
MAX_ASN_LIST = 10

# Regex conservadora para destinos hostname/IP (defesa em camadas)
_DESTINATION_RE = re.compile(r"^[A-Za-z0-9.-]+$")
_HOSTNAME_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")

# Faixas de endereços que não podem ser alvo de consulta (internas/reservadas).
# Layout de cada entrada: RFC, faixa (ip_network), finalidade (para o alerta).
RFC_RESERVED_NETWORKS: list[dict] = [
    {"rfc": "RFC 1918", "network": ipaddress.ip_network("10.0.0.0/8"), "purpose": "Privado"},
    {"rfc": "RFC 1918", "network": ipaddress.ip_network("172.16.0.0/12"), "purpose": "Privado"},
    {"rfc": "RFC 1918", "network": ipaddress.ip_network("192.168.0.0/16"), "purpose": "Privado"},
    {"rfc": "RFC 6598", "network": ipaddress.ip_network("100.64.0.0/10"), "purpose": "CGN/CGNAT"},
    {"rfc": "RFC 5737", "network": ipaddress.ip_network("192.0.2.0/24"), "purpose": "Documentação"},
    {"rfc": "RFC 5737", "network": ipaddress.ip_network("198.51.100.0/24"), "purpose": "Documentação"},
    {"rfc": "RFC 5737", "network": ipaddress.ip_network("203.0.113.0/24"), "purpose": "Documentação"},
    {"rfc": "RFC 3927", "network": ipaddress.ip_network("169.254.0.0/16"), "purpose": "Link-local/APIPA"},
    {"rfc": "RFC 1122", "network": ipaddress.ip_network("127.0.0.0/8"), "purpose": "Loopback"},
    # Faixas abaixo completam o registro IPv4 de uso especial (RFC 6890 /
    # IANA IPv4 Special-Purpose Address Registry). Não entram na lista as
    # entradas marcadas como Globally Reachable = TRUE no registro, que são
    # anycast público real e alvos legítimos de consulta: 192.31.196.0/24
    # (AS112-v4), 192.52.193.0/24 (AMT) e 192.175.48.0/24 (Direct Delegation
    # AS112). 192.0.0.0/24 cobre 192.0.0.9/32 e 192.0.0.10/32 por decisão
    # explícita: nenhum deles é alvo de looking glass, mesmo sendo anycast.
    {"rfc": "RFC 6890", "network": ipaddress.ip_network("0.0.0.0/8"), "purpose": "This network"},
    {"rfc": "RFC 6890", "network": ipaddress.ip_network("192.0.0.0/24"), "purpose": "IETF Protocol Assignments"},
    {"rfc": "RFC 2544", "network": ipaddress.ip_network("198.18.0.0/15"), "purpose": "Benchmarking"},
    # Ordem importa: `_find_reserved` devolve a primeira entrada que casa, então
    # 255.255.255.255/32 precisa vir antes de 240.0.0.0/4 para o alerta citar a
    # faixa específica em vez do /4 que a contém.
    {"rfc": "RFC 8190", "network": ipaddress.ip_network("255.255.255.255/32"), "purpose": "Limited broadcast"},
    {"rfc": "RFC 1112", "network": ipaddress.ip_network("240.0.0.0/4"), "purpose": "Reservado"},
    {"rfc": "RFC 7526", "network": ipaddress.ip_network("192.88.99.0/24"), "purpose": "6to4 Relay Anycast (depreciado)"},
    {"rfc": "RFC 4291", "network": ipaddress.ip_network("::1/128"), "purpose": "Loopback"},
    {"rfc": "RFC 4291", "network": ipaddress.ip_network("::/128"), "purpose": "Indefinido"},
    {"rfc": "RFC 4291", "network": ipaddress.ip_network("fe80::/10"), "purpose": "Link-local"},
    {"rfc": "RFC 4193", "network": ipaddress.ip_network("fc00::/7"), "purpose": "ULA (privado IPv6)"},
    {"rfc": "RFC 3849", "network": ipaddress.ip_network("2001:db8::/32"), "purpose": "Documentação"},
]


class SecurityError(Exception):
    pass


class ReservedRangeError(SecurityError):
    """Consulta a um endereço em faixa reservada (RFC)."""

    def __init__(self, requested: str, entry: dict) -> None:
        self.requested = requested
        self.rfc = entry["rfc"]
        self.network = str(entry["network"])
        self.purpose = entry["purpose"]
        super().__init__(
            f"Consulta bloqueada: '{requested}' está em faixa reservada "
            f"({self.rfc} — {self.purpose} — {self.network})"
        )


# ASNs reservados para uso privado (RFC 6996). Não são globalmente únicos e não
# aparecem na tabela BGP global, então consultá-los a partir de um looking glass
# só pode devolver nada. Range inclusivo em ambos os extremos.
RFC_PRIVATE_AS_RANGES: list[dict] = [
    {"rfc": "RFC 6996", "start": 64512, "end": 65534, "purpose": "Privado (16 bits)"},
    {"rfc": "RFC 6996", "start": 4200000000, "end": 4294967294, "purpose": "Privado (32 bits)"},
]


class ReservedAsnError(SecurityError):
    """Consulta a um AS number em faixa reservada (RFC 6996)."""

    def __init__(self, requested: str, entry: dict) -> None:
        self.requested = requested
        self.rfc = entry["rfc"]
        self.start = entry["start"]
        self.end = entry["end"]
        self.purpose = entry["purpose"]
        super().__init__(
            f"Consulta bloqueada: AS {requested} está em faixa reservada "
            f"({self.rfc} — {self.purpose} — {self.start}-{self.end})"
        )


def _find_private_as(number: int) -> dict | None:
    """Retorna a entrada da tabela de ASNs privados que contém `number`."""
    for entry in RFC_PRIVATE_AS_RANGES:
        if entry["start"] <= number <= entry["end"]:
            return entry
    return None


def _find_reserved(address) -> dict | None:
    """Retorna a entrada da tabela que contém `address`, se houver."""
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    version = address.version
    for entry in RFC_RESERVED_NETWORKS:
        network = entry["network"]
        if network.version != version:
            continue
        if isinstance(address, ipaddress.IPv4Address | ipaddress.IPv6Address):
            if address in network:
                return entry
        elif address.subnet_of(network):
            return entry
    return None


def _reject_if_reserved(requested: str, address) -> None:
    entry = _find_reserved(address)
    if entry is not None:
        raise ReservedRangeError(requested, entry)


def sanitize_parameter(value: str, field: str = "parâmetro") -> str:
    """Remove espaços e rejeita qualquer valor com caracteres perigosos."""
    if value is None or not str(value).strip():
        raise SecurityError(f"{field} não pode ser vazio")
    cleaned = str(value).strip()
    if any(char in cleaned for char in FORBIDDEN_CHARS):
        raise SecurityError(
            f"{field} contém caracteres não permitidos (possível injeção de comando)"
        )
    return cleaned


def validate_ip(value: str, field: str = "IP") -> str:
    """Valida um endereço IP (IPv4 ou IPv6) e devolve em formato canônico."""
    cleaned = sanitize_parameter(value, field)
    try:
        address = ipaddress.ip_address(cleaned)
    except ValueError as exc:
        raise SecurityError(f"{field} inválido: {cleaned!r} não é um endereço IP") from exc
    _reject_if_reserved(cleaned, address)
    return str(address)


def validate_prefix(value: str, field: str = "prefixo") -> str:
    """Valida um prefixo/rota (IP simples ou CIDR, ex.: '8.8.8.8' ou '8.8.8.0/24')."""
    cleaned = sanitize_parameter(value, field)
    # IP simples também é aceito (interpretado como /32)
    try:
        address = ipaddress.ip_address(cleaned)
        _reject_if_reserved(cleaned, address)
        return cleaned
    except ValueError:
        pass
    try:
        network = ipaddress.ip_network(cleaned, strict=False)
    except ValueError as exc:
        raise SecurityError(
            f"{field} inválido: {cleaned!r} não é um IP nem um prefixo CIDR"
        ) from exc
    _reject_if_reserved(cleaned, network)
    return str(network)


def validate_destination(value: str, field: str = "destino") -> str:
    """Valida um destino para ping/traceroute: IP ou hostname simples.

    - Sem ':' → hostname/IPv4: apenas letras, números, ponto e hífen.
    - Com ':' → aceito somente se for um endereço válido (IPv6), péla rigorosa
      para impedir injeção disfarçada.
    - Endereços em faixa reservada (RFC 1918 etc.) são bloqueados; hostnames
      não (podem resolver para IP público ou interno, mas não dá para saber).
    """
    cleaned = sanitize_parameter(value, field)
    if ":" in cleaned:
        try:
            address = ipaddress.ip_address(cleaned)
        except ValueError as exc:
            raise SecurityError(f"{field} inválido: {cleaned!r} não é IPv6 nem hostname") from exc
        _reject_if_reserved(cleaned, address)
        return str(address)
    if len(cleaned) > 253:
        raise SecurityError(f"{field} muito longo")
    if not _DESTINATION_RE.match(cleaned):
        raise SecurityError(
            f"{field} inválido: {cleaned!r} contém caracteres não permitidos"
        )
    try:
        address = ipaddress.ip_address(cleaned)
    except ValueError:
        address = None
    if address is not None:
        _reject_if_reserved(cleaned, address)
    return cleaned


def validate_hostname(value: str, field: str = "hostname") -> str:
    """Valida um hostname (LDH: letras, números, hífen, ponto; sem IP)."""
    cleaned = sanitize_parameter(value, field)
    if ":" in cleaned:
        raise SecurityError(f"{field} inválido: {cleaned!r} parece ser um IP")
    if len(cleaned) > 253 or not _HOSTNAME_RE.match(cleaned):
        raise SecurityError(f"{field} inválido: {cleaned!r} não é um hostname válido")
    return cleaned


def validate_asn(value: str, field: str = "AS") -> str:
    """Valida um ou mais números de AS (2 ou 4 bytes).

    Aceita `15169`, `15169,65000` ou `15169 65000`. A engine transforma isso
    em regex de AS Path, então o usuário nunca escreve regex. ASNs privados
    (RFC 6996) são rejeitados por não existirem na tabela BGP global.
    """
    cleaned = sanitize_parameter(value, field)
    tokens = [token for token in re.split(r"[,\s]+", cleaned) if token]
    if not tokens or len(tokens) > MAX_ASN_LIST:
        raise SecurityError(
            f"{field} inválido: {cleaned!r} (informe de 1 a {MAX_ASN_LIST} AS)"
        )
    for token in tokens:
        if not _ASN_RE.match(token) or int(token) > MAX_ASN:
            raise SecurityError(
                f"{field} inválido: {token!r} (esperado um número de 1 a {MAX_ASN})"
            )
        entry = _find_private_as(int(token))
        if entry is not None:
            raise ReservedAsnError(token, entry)
    return ",".join(tokens)


def validate_community(value: str, field: str = "community") -> str:
    """Valida uma community BGP (ou lista curta de communities).

    Aceita `aa:nn` (cada parte em 0..65535, como no IOS), nomes bem conhecidos
    (`no-export`, `internet`) e listas separadas por espaço. Qualquer
    metacaractere de regex ou separador de comando é barrado por
    `sanitize_parameter`/padrão abaixo.
    """
    cleaned = sanitize_parameter(value, field)
    if len(cleaned) > 255 or not _COMMUNITY_RE.match(cleaned):
        raise SecurityError(f"{field} inválido: {cleaned!r}")
    for token in cleaned.split():
        number = _COMMUNITY_NUMBER_RE.match(token)
        if number is None:
            continue
        for part in ("aa", "nn"):
            if int(number.group(part)) > _COMMUNITY_MAX:
                raise SecurityError(
                    f"{field} inválido: {token!r} — a parte {part} deve estar "
                    f"entre 0 e {_COMMUNITY_MAX}"
                )
    return cleaned


def validate_final_command(command: str) -> str:
    """Barreira final sobre o comando montado (template + parâmetros).

    O template é configuração do admin (nodes/*.yaml), então pipes, aspas e
    metacaracteres são legítimos; o que não pode sobrar é encadeamento de
    comandos, redirecionamento, quebra de linha ou `$` de placeholder não
    resolvido. Os parâmetros do usuário já foram barrados na origem por
    `sanitize_parameter` (FORBIDDEN_CHARS).
    """
    if not command or not command.strip():
        raise SecurityError("Comando final vazio")
    if any(char in command for char in FORBIDDEN_TEMPLATE_CHARS):
        raise SecurityError(
            "Comando final contém caracteres não permitidos (possível injeção)"
        )
    if "$" in command:
        raise SecurityError("Comando final contém placeholder não resolvido")
    return command.strip()