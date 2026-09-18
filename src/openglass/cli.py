"""CLI de validação da conexão Cisco.

Permite:
- listar devices do inventário e comandos disponíveis (whitelist do NOS)
- conectar em um roteador (com timeouts e erros tratados) e rodar comandos
- modo interativo ou one-shot (--command)

Uso (interativo):
    uv run openglass          # ou: uv run python main.py

Uso (one-shot):
    uv run python main.py --device edge-router --command ping --param ip=8.8.8.8

Exit codes: 0 ok | 2 erro (inventário/conexão/comando) | 130 abortado
"""

import argparse
import sys
from typing import NoReturn

from openglass.commands import CommandNotAllowedError, run as run_command
from openglass.config import settings
from openglass.connection import DeviceError, DeviceSession
from openglass.inventory import Device, InventoryError, load_inventory
from openglass.nodes import NodeError, load_profile
from openglass.security import SecurityError


def _print_commands(nos: str, prefix: str = "  ") -> None:
    profile = load_profile(nos)
    width = max(len(label) for label in profile.commands)
    print(f"{prefix}{'comando':<{width}}  descrição")
    print(f"{prefix}{'-------':<{width}}  ----------")
    for label, command in sorted(profile.commands.items()):
        print(f"{prefix}{label:<{width}}  {command.description}")


def _print_devices(devices: list[Device]) -> None:
    for index, device in enumerate(devices, start=1):
        auth = f"chave ({device.credential.key_path})" if device.credential.key_path else "senha"
        print(f"  {index}. {device.name:<18} {device.address}:{device.port} "
              f"[{device.nos} / {auth}]")


def _prompt(text: str) -> str:
    try:
        return input(text).strip()
    except EOFError:
        raise KeyboardInterrupt


def _pick_device(devices: list[Device], reference: str | None) -> Device:
    if reference is None:
        print("Devices disponíveis:")
        _print_devices(devices)
        reference = _prompt("\nDevice (nome ou número): ")

    for index, candidate in enumerate(devices, start=1):
        if candidate.name == reference or reference == str(index):
            return candidate
    raise InventoryError(f"Device não encontrado: {reference!r}")


def run_and_print(
    session: DeviceSession, label: str, params: dict[str, str] | None = None
) -> tuple[str, object, str]:
    """Roda um comando da whitelist do NOS e imprime o output cru formatado."""
    output, spec, command = run_command(session, label, params)
    lines = output.splitlines()
    total = len(lines)
    print("\n" + "─" * 60)
    print(f"[{spec.description} | {label}] {session.device.name}")
    print(f"$ {command}")
    print("─" * 60)
    if total <= 500:
        print(output.rstrip())
    else:
        print("\n".join(lines[:300]))
        print(f"\n... output truncado: {total} linhas no total. "
              f"Redirecione para um arquivo para ver tudo:")
        print(f"    uv run python main.py --device {session.device.name} "
              f"--command {label} > saida.txt")
    print("─" * 60)
    return output, spec, command


def interactive(session: DeviceSession) -> None:
    profile = load_profile(session.device.nos)
    print()
    print(f"Conectado em {session.device.name} "
          f"({session.device.address}:{session.device.port}), NOS {profile.nos}.")
    print("'help' lista comandos, 'exit' encerra.")

    while True:
        raw = _prompt(f"(openglass:{session.device.name})> ")
        if raw in ("exit", "quit"):
            break
        if raw in ("help", "?"):
            _print_commands(profile.nos)
            continue

        label = raw
        command = profile.commands.get(label)
        if command is None:
            print(f"  [?] comando não reconhecido: {label!r} (use 'help')")
            continue

        params: dict[str, str] = {}
        for name in command.params:
            value = _prompt(f"  {name} → ")
            params[name] = value

        try:
            run_and_print(session, label, params)
        except (Exception, KeyboardInterrupt) as exc:
            print(f"  [erro] {exc}")


def interactive_mode(device_name: str | None) -> int:
    devices = load_inventory(settings.inventory_path)
    device = _pick_device(devices, device_name)
    try:
        with DeviceSession(device) as session:
            interactive(session)
    except DeviceError as exc:
        print(f"[erro de conexão] {exc}")
        return 2
    print("\n[desconectado]")
    return 0


def one_shot(device_name: str | None, label: str, raw_params: list[str]) -> int:
    from openglass.commands import build_command

    params: dict[str, str] = {}
    for item in raw_params:
        if "=" not in item:
            raise InventoryError(f"--param deve ser CHAVE=VALOR, recebido: {item!r}")
        key, value = item.split("=", 1)
        params[key] = value

    devices = load_inventory(settings.inventory_path)
    device = _pick_device(devices, device_name)

    # Valida comando e parâmetros ANTES de abrir qualquer conexão.
    build_command(device.nos, label, params, device=device)

    with DeviceSession(device) as session:
        run_and_print(session, label, params)
    print("[desconectado]")
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="openglass",
        description="OpenGlass — diagnóstico de rede em roteadores de borda (Cisco)",
    )
    parser.add_argument("--list-devices", action="store_true",
                        help="lista os devices do inventário")
    parser.add_argument("--list-commands", action="store_true",
                        help="lista os comandos permitidos do NOS (exige --device)")
    parser.add_argument("--device", help="nome do device no inventário")
    parser.add_argument("--command", help="comando da whitelist (modo one-shot)")
    parser.add_argument("--param", action="append", default=[],
                        metavar="CHAVE=VALOR",
                        help="parâmetro do comando (ex.: ip=8.8.8.8)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> NoReturn:
    args = parse_args(argv)

    if args.list_commands:
        try:
            devices = load_inventory(settings.inventory_path)
            device = _pick_device(devices, args.device)
            print(f"Comandos permitidos para {device.name} (NOS {device.nos}):")
            _print_commands(device.nos)
        except (InventoryError, NodeError) as exc:
            print(f"[erro] {exc}")
            raise SystemExit(2)
        raise SystemExit(0)

    if args.list_devices:
        print("Devices no inventário:")
        try:
            _print_devices(load_inventory(settings.inventory_path))
        except InventoryError as exc:
            print(f"[erro] {exc}")
            raise SystemExit(2)
        raise SystemExit(0)

    try:
        if args.command:
            exit_code = one_shot(args.device, args.command, args.param)
        else:
            exit_code = interactive_mode(args.device)
    except (InventoryError, DeviceError, SecurityError, NodeError) as exc:
        print(f"[erro] {exc}")
        raise SystemExit(2)
    except KeyboardInterrupt:
        print("\n[abortado]")
        raise SystemExit(130)

    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()