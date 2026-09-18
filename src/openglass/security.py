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


class SecurityError(Exception):
    pass


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
        return str(ipaddress.ip_address(cleaned))
    except ValueError as exc:
        raise SecurityError(f"{field} inválido: {cleaned!r} não é um endereço IP") from exc


def validate_prefix(value: str, field: str = "prefixo") -> str:
    """Valida um prefixo/rota (IP simples ou CIDR, ex.: '8.8.8.8' ou '8.8.8.0/24')."""
    cleaned = sanitize_parameter(value, field)
    # IP simples também é aceito (interpretado como /32)
    try:
        ipaddress.ip_address(cleaned)
        return cleaned
    except ValueError:
        pass
    try:
        network = ipaddress.ip_network(cleaned, strict=False)
        return str(network)
    except ValueError as exc:
        raise SecurityError(
            f"{field} inválido: {cleaned!r} não é um IP nem um prefixo CIDR"
        ) from exc


def validate_destination(value: str, field: str = "destino") -> str:
    """Valida um destino para ping/traceroute: IP ou hostname simples.

    - Sem ':' → hostname/IPv4: apenas letras, números, ponto e hífen.
    - Com ':' → aceito somente se for um endereço válido (IPv6), péla rigorosa
      para impedir injeção disfarçada.
    """
    cleaned = sanitize_parameter(value, field)
    if ":" in cleaned:
        try:
            return str(ipaddress.ip_address(cleaned))
        except ValueError as exc:
            raise SecurityError(f"{field} inválido: {cleaned!r} não é IPv6 nem hostname") from exc
    if len(cleaned) > 253:
        raise SecurityError(f"{field} muito longo")
    if not _DESTINATION_RE.match(cleaned):
        raise SecurityError(
            f"{field} inválido: {cleaned!r} contém caracteres não permitidos"
        )
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