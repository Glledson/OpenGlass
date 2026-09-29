"""Camada de comandos: whitelist e formatos por NOS (nodes/*.yaml).

A whitelist e os templates NÃO são mais hardcoded: veêm do perfil do NOS do
device (nodes/<nos>.yaml). Esta camada:

- rejeita qualquer comando fora da whitelist do NOS (CommandNotAllowedError)
- valida parâmetros do usuário (security.py) antes de montar o comando
- resolve placeholders automáticos ($source_address do VRF do device,
  $as_path_regexp montada a partir dos ASNs informados)
- valida o comando final (defense-in-depth) e executa na sessão

Separada da camada de conexão: só conhece a interface da sessão
(run_command/close), nunca Netmiko diretamente.
"""

import ipaddress
import re
from dataclasses import dataclass
from typing import Callable

from openglass.connection import DeviceSession
from openglass.inventory import Device
from openglass.nodes import (
    AS_MARKER_LIST,
    AS_MARKER_SINGLE,
    AUTO_PLACEHOLDERS,
    NodeCommand,
    NodeProfile,
    load_profile,
)
from openglass.parsers import parse as parse_output
from openglass.parsers.base import ParserError
from openglass.security import (
    SecurityError,
    sanitize_parameter,
    validate_asn,
    validate_community,
    validate_destination,
    validate_final_command,
    validate_hostname,
    validate_ip,
    validate_prefix,
)

# Mapeia o `type` declarado no YAML para o validador (security.py).
_VALIDATORS: dict[str, Callable[[str, str], str]] = {
    "destination": validate_destination,
    "ip": validate_ip,
    "prefix": validate_prefix,
    "hostname": validate_hostname,
    "asn": validate_asn,
    "community": validate_community,
}

# Acima disso o output é sinalizado como grande (comandos de tabela BGP podem
# devolver dezenas de milhares de linhas). Não corta nada: o bruto vai inteiro
# e a flag só avisa quem consome (UI/CLI) para não achar que a tela travou.
LARGE_OUTPUT_LINES = 5000


class CommandNotAllowedError(SecurityError):
    pass


# Tipos de parâmetro que representam um endereço (selecionam o template v4/v6).
_ADDRESS_TYPES = frozenset({"destination", "ip", "prefix"})


@dataclass(frozen=True)
class CommandResult:
    """Resultado de um comando: output cru + dados estruturados (opcional)."""

    label: str
    command: str
    description: str
    raw: str
    parser: str | None = None
    parsed: dict | None = None
    line_count: int = 0
    truncated: bool = False


def _profile_for(device: Device | None, nos: str | None = None) -> NodeProfile:
    """Resolve o NOS (explícito ou do device) e carrega o perfil."""
    selected = nos or (device.nos if device else None)
    if not selected:
        raise SecurityError("Não foi possível determinar o NOS do device")
    return load_profile(selected)


def list_commands(nos: str) -> dict[str, NodeCommand]:
    """Whitelist de comandos do NOS (para o CLI)."""
    return dict(load_profile(nos).commands)


def _source_address_for(device: Device, destination: str) -> str | None:
    """source_address do VRF do device (ipv4/ipv6 conforme o destino)."""
    if not device.vrfs:
        return None
    vrf = next((v for v in device.vrfs if v.default), device.vrfs[0])
    family = vrf.ipv6 if ":" in destination else vrf.ipv4
    if family is None:
        return None
    return family.source_address


def _family_of(value: str) -> str:
    """Família do destino informado: `:` indica IPv6, o resto é tratado como v4."""
    return "ipv6" if ":" in value else "ipv4"


def _target_family(command: NodeCommand, values: dict[str, str]) -> str:
    """Família do alvo, derivada do parâmetro de endereço do comando.

    Só os tipos de endereço definem a família; params sem endereço (community,
    asn, ...) não são IPs e caem no padrão IPv4.
    """
    for name, spec in command.params.items():
        if spec.type in _ADDRESS_TYPES:
            return _family_of(values.get(name, ""))
    return "ipv4"


def _as_path_regexp(pattern: str, asn: str) -> str:
    """Monta a regex de AS Path a partir dos ASNs e do padrão do comando.

    O padrão vem do `asn_regexp:` do node, porque a gramática muda por NOS: o
    IOS quer a regex entre aspas (o `|` não colide com nada) e aceita
    alternância; o VRP não aceita aspas e trata `|` como separador de
    modificador de output, então só um AS por consulta.

    O usuário nunca escreve regex: só entram dígitos já validados.
    """
    numbers = asn.split(",")
    if AS_MARKER_LIST in pattern:
        return pattern.replace(AS_MARKER_LIST, "|".join(f"_{n}_" for n in numbers))
    if len(numbers) > 1:
        raise SecurityError(
            f"este NOS aceita apenas um AS por consulta de AS Path "
            f"({len(numbers)} informados)"
        )
    return pattern.replace(AS_MARKER_SINGLE, f"_{numbers[0]}_")


def _reject_own_as(label: str, asn: str, device: Device | None) -> None:
    """Recusa consultar o AS do próprio device.

    O IOS/VRP prefixa o AS local em todo caminho originado aqui, então
    `_265269_` casa quase a tabela inteira (foi 1.1M de linhas / 211s no
    edge-r1). Bloqueado aqui porque a alternativa custa um request de vários
    minutos e ~200MB de texto.
    """
    own = getattr(device, "asn", None) if device is not None else None
    if own is None:
        return
    if str(own) in asn.split(","):
        raise SecurityError(
            f"{label}: {own} é o AS deste device. Consultar o próprio AS devolve "
            "praticamente a tabela BGP inteira; informe o AS de um terceiro."
        )


def _prefix_of(command: NodeCommand, values: dict[str, str]) -> ipaddress.IPv4Network | ipaddress.IPv6Network:
    """Rede do parâmetro de endereço do comando (host vira /32 ou /128).

    Usa o mesmo critério de família de `_target_family`: só um param cujo tipo
    é endereço serve, para não pegar outro valor por acidente.
    """
    for name, spec in command.params.items():
        if spec.type in _ADDRESS_TYPES and name in values:
            return ipaddress.ip_network(values[name], strict=False)
    raise SecurityError("comando exige um parâmetro de endereço")


def _resolve_template(template: str, values: dict[str, str], missing_auto: set[str]) -> str:
    """Substitui placeholders; remove cláusulas cujo placeholder automático faltou."""
    command = template
    for placeholder in missing_auto:
        # remove "keyword $placeholder" (ex.: "source $source_address") quando o
        # valor não está disponível (ex.: device sem source_address no VRF)
        command = re.sub(rf"\b[A-Za-z0-9_.-]+\s+\${placeholder}", "", command)
        command = command.replace(f"${placeholder}", "")
    # do mais longo para o mais curto: senão `$prefix` casaria dentro de
    # `$prefix_address` e deixaria o sufixo `_address` no comando
    for name in sorted(values, key=len, reverse=True):
        command = command.replace(f"${name}", values[name])
    command = re.sub(r"\s{2,}", " ", command).strip()
    return command


def build_command(
    nos: str,
    label: str,
    params: dict[str, str] | None = None,
    *,
    device: Device | None = None,
) -> tuple[NodeCommand, str]:
    """Valida comando + parâmetros contra o perfil do NOS e monta a string.

    Retorna (NodeCommand, comando_final). A variante do template (quando o
    comando declara uma por família) é escolhida pelo destino informado.
    `NodeCommand.parser` é o gancho para a camada de parsing.
    """
    profile = _profile_for(device, nos)
    command = profile.commands.get(label)
    if command is None:
        raise CommandNotAllowedError(
            f"Comando não permitido no NOS '{profile.nos}': {label!r} "
            f"(permitidos: {', '.join(profile.commands)})"
        )

    params = params or {}
    values: dict[str, str] = {}
    for name, spec in command.params.items():
        if name not in params:
            raise SecurityError(f"Comando '{label}' exige o parâmetro '{name}'")
        validator = _VALIDATORS.get(spec.type)
        if validator is None:
            raise SecurityError(f"Tipo de parâmetro desconhecido: {spec.type!r}")
        values[name] = validator(params[name], name)

    # Variante do template conforme a família do destino. Comandos sem
    # parâmetro de endereço (ex.: bgp-community) usam a variante IPv4.
    try:
        template = command.template_for(_target_family(command, values))
    except ValueError as exc:
        raise SecurityError(f"Comando '{label}' rejeitado: {exc}") from exc

    # Placeholders automáticos presentes no template
    placeholders = set(re.findall(r"\$([A-Za-z0-9_]+)", template))
    missing_auto: set[str] = set()
    for placeholder in placeholders & set(AUTO_PLACEHOLDERS):
        if placeholder == "source_address":
            if device is None:
                missing_auto.add(placeholder)
                continue
            source = _source_address_for(device, next(iter(values.values()), ""))
            if source is None:
                missing_auto.add(placeholder)
            else:
                values[placeholder] = source
        elif placeholder == "as_path_regexp":
            if command.asn_regexp is None:
                raise SecurityError(
                    f"Comando '{label}' usa $as_path_regexp sem declarar asn_regexp"
                )
            _reject_own_as(label, values["asn"], device)
            values[placeholder] = _as_path_regexp(command.asn_regexp, values["asn"])
        elif placeholder in ("prefix_address", "prefix_length"):
            network = _prefix_of(command, values)
            values[placeholder] = (
                str(network.network_address)
                if placeholder == "prefix_address"
                else str(network.prefixlen)
            )

    final = _resolve_template(template, values, missing_auto)
    try:
        final = validate_final_command(final)
    except SecurityError as exc:
        raise SecurityError(f"Comando '{label}' rejeitado: {exc}") from exc
    return command, final


def run(
    session: DeviceSession,
    label: str,
    params: dict[str, str] | None = None,
) -> CommandResult:
    """Executa um comando da whitelist do NOS do device.

    Retorna um `CommandResult` com o output cru e, quando o comando declara um
    `parser`, os dados estruturados. Falha do parser não quebra a execução: o
    resultado sai apenas com o output cru (`parsed=None`).
    """
    nos = session.device.nos
    spec, command = build_command(nos, label, params, device=session.device)
    raw = session.run_command(command, timeout=spec.effective_timeout())

    parsed: dict | None = None
    if spec.parser:
        try:
            parsed = parse_output(spec.parser, raw, params or {})
        except ParserError:
            parsed = None

    line_count = len(raw.splitlines())
    return CommandResult(
        label=label,
        command=command,
        description=spec.description,
        raw=raw,
        parser=spec.parser,
        parsed=parsed,
        line_count=line_count,
        truncated=line_count > LARGE_OUTPUT_LINES,
    )
