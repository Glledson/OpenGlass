"""Camada de segurança: whitelist de comandos e sanitização de parâmetros.

Regras:
- Nenhum comando arbitrário é aceito: só comandos pré-definidos (commands.py).
- Parâmetros (ex.: IP de destino no ping) passam por sanitização para impedir
  injeção de comando dentro da sessão SSH (ex.: "8.8.8.8; show running-config").
- Métricas de validação com validação rigorosa de IP/prefixo.
"""

import ipaddress
import re

# Caracteres que podem quebrar a sessão/encadear comandos numa CLI de rede
FORBIDDEN_CHARS = set(";&|`$!(){}[]<>'\"\\\n\r")

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


def validate_final_command(command: str) -> str:
    """Última barreira: rejeita qualquer caractere perigoso restante no comando.

    Roda APÓS montar o comando (templates + parâmetros validados) para nunca
    enviar algo com injeção ou placeholder não resolvido.
    """
    if not command or not command.strip():
        raise SecurityError("Comando final vazio")
    if any(char in command for char in FORBIDDEN_CHARS):
        raise SecurityError(
            "Comando final contém caracteres não permitidos (possível injeção)"
        )
    if "$" in command:
        raise SecurityError("Comando final contém placeholder não resolvido")
    return command.strip()