"""Camada de conexão: abre/fecha sessão SSH via Netmiko.

Responsabilidades:
- Estabelecer a sessão com o dispositivo (timeout tratado)
- Mapear falhas para erros tipados (autenticação, timeout, host inacessível...)
- Garantir o fechamento da sessão, inclusive em caso de exceção

A camada de comandos (commands.py) usa esta camada, mas não conhece Netmiko.
"""

import socket
from typing import Any

import paramiko
from netmiko import ConnectHandler
from netmiko.exceptions import NetmikoAuthenticationException, NetmikoTimeoutException

from openglass.config import settings
from openglass.inventory import Device


class DeviceError(Exception):
    """Erro base da camada de conexão."""


class DeviceAuthError(DeviceError):
    """Falha de autenticação (usuário/senha/chave inválidos)."""


class DeviceTimeoutError(DeviceError):
    """Timeout ao estabelecer a conexão."""


class DeviceUnreachableError(DeviceError):
    """Host inacessível: DNS falhou, conexão recusada, rede indisponível."""


class DeviceSSHError(DeviceError):
    """Falha SSH não especificada."""


class DeviceSession:
    """Wrapper da sessão de um dispositivo.

    Uso como context manager (close garantido):

        with DeviceSession(device) as session:
            output = session.run_command("show ip route", timeout=30)
    """

    def __init__(self, device: Device, connect_timeout: float | None = None) -> None:
        self.device = device
        self.connect_timeout = connect_timeout or settings.device_timeout
        self._conn: ConnectHandler | None = None

    # ------------------------------------------------------------------
    # Conexão
    # ------------------------------------------------------------------
    def connect(self) -> "DeviceSession":
        if self._conn is not None:
            return self
        netmiko_kwargs: dict[str, Any] = {
            "device_type": self.device.netmiko_device_type,
            "host": self.device.address,
            "port": self.device.port,
            "username": self.device.username,
            "timeout": self.connect_timeout,
            "fast_cli": False,
            "verbose": settings.debug,
        }
        netmiko_kwargs.update(self.device.authentication_options())

        try:
            self._conn = ConnectHandler(**netmiko_kwargs)
        except NetmikoAuthenticationException as exc:
            raise DeviceAuthError(
                f"Falha de autenticação em {self.device.name} "
                f"({self.device.address}:{self.device.port}): {exc}"
            ) from exc
        except NetmikoTimeoutException as exc:
            raise DeviceTimeoutError(
                f"Timeout de conexão com {self.device.name} "
                f"({self.device.address}:{self.device.port}) após "
                f"{self.connect_timeout}s: {exc}"
            ) from exc
        except (
            paramiko.AuthenticationException,
            paramiko.ssh_exception.SSHException,
            paramiko.ssh_exception.NoValidConnectionsError,
            socket.gaierror,
            socket.timeout,
            OSError,
            EOFError,
        ) as exc:
            raise DeviceUnreachableError(
                f"Host {self.device.name} inacessível "
                f"({self.device.address}:{self.device.port}): {exc}"
            ) from exc
        except Exception as exc:
            raise DeviceSSHError(
                f"Falha de SSH com {self.device.name} "
                f"({self.device.address}:{self.device.port}): {exc}"
            ) from exc
        return self

    @property
    def is_connected(self) -> bool:
        return self._conn is not None

    # ------------------------------------------------------------------
    # Execução
    # ------------------------------------------------------------------
    def run_command(self, command: str, timeout: float | None = None) -> str:
        """Roda um comando e retorna o output cru (sem parsing)."""
        if self._conn is None:
            raise DeviceError("Sessão não conectada: chame connect() antes de run_command()")
        read_timeout = timeout or settings.default_command_timeout
        try:
            return self._conn.send_command(command, read_timeout=read_timeout)
        except NetmikoTimeoutException as exc:
            raise DeviceTimeoutError(
                f"Timeout lendo a resposta de '{command}' após {read_timeout}s: {exc}"
            ) from exc
        except Exception as exc:
            raise DeviceSSHError(f"Falha ao executar '{command}': {exc}") from exc

    # ------------------------------------------------------------------
    # Fechamento
    # ------------------------------------------------------------------
    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.disconnect()
            finally:
                self._conn = None

    def __enter__(self) -> "DeviceSession":
        return self.connect()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object | None,
    ) -> None:
        self.close()

    def __repr__(self) -> str:
        state = "conectado" if self.is_connected else "desconectado"
        return f"<DeviceSession {self.device.name} ({state})>"