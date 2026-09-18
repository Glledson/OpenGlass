"""Camada de comandos: whitelist e formatos por NOS (nodes/*.yaml).

A whitelist e os templates NÃO são mais hardcoded: veêm do perfil do NOS do
device (nodes/<nos>.yaml). Esta camada:

- rejeita qualquer comando fora da whitelist do NOS (CommandNotAllowedError)
- valida parâmetros do usuário (security.py) antes de montar o comando
- resolve placeholders automáticos (ex.: $source_address do VRF do device)
- valida o comando final (defense-in-depth) e executa na sessão

Separada da camada de conexão: só conhece a interface da sessão
(run_command/close), nunca Netmiko diretamente.
"""

import re
from dataclasses import dataclass
from typing import Callable

from openglass.connection import DeviceSession
from openglass.inventory import Device
from openglass.nodes import AUTO_PLACEHOLDERS, NodeCommand, NodeProfile, load_profile
from openglass.parsers import parse as parse_output
from openglass.parsers.base import ParserError
from openglass.security import (
    SecurityError,
    sanitize_parameter,
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
}


class CommandNotAllowedError(SecurityError):
    pass


@dataclass(frozen=True)
class CommandResult:
    """Resultado de um comando: output cru + dados estruturados (opcional)."""

    label: str
    command: str
    description: str
    raw: str
    parser: str | None = None
    parsed: dict | None = None


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


def _resolve_template(template: str, values: dict[str, str], missing_auto: set[str]) -> str:
    """Substitui placeholders; remove cláusulas cujo placeholder automático faltou."""
    command = template
    for placeholder in missing_auto:
        # remove "keyword $placeholder" (ex.: "source $source_address") quando o
        # valor não está disponível (ex.: device sem source_address no VRF)
        command = re.sub(rf"\b[A-Za-z0-9_.-]+\s+\${placeholder}", "", command)
        command = command.replace(f"${placeholder}", "")
    for name, value in values.items():
        command = command.replace(f"${name}", value)
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

    Retorna (NodeCommand, comando_final). `NodeCommand.parser` é o gancho para
    a futura camada de parsing.
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

    # Placeholders automáticos presentes no template
    placeholders = set(re.findall(r"\$([A-Za-z0-9_]+)", command.template))
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

    final = _resolve_template(command.template, values, missing_auto)
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
            parsed = parse_output(spec.parser, raw)
        except ParserError:
            parsed = None

    return CommandResult(
        label=label,
        command=command,
        description=spec.description,
        raw=raw,
        parser=spec.parser,
        parsed=parsed,
    )