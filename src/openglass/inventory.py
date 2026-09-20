import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class InventoryError(Exception):
    pass


class Credential(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str
    password: str | None = None
    key_path: str | None = None

    @model_validator(mode="after")
    def _check_authentication(self) -> "Credential":
        if not self.password and not self.key_path:
            raise ValueError(
                "credential precisa de 'password' ou 'key_path' para autenticar"
            )
        return self


class NetworkInfo(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    display_name: str | None = None


class AccessEntry(BaseModel):
    model_config = ConfigDict(extra="ignore")

    network: str
    action: Literal["permit", "deny"] = "permit"
    ge: int | None = Field(default=None, ge=0, le=128)
    le: int | None = Field(default=None, ge=0, le=128)


class VrfFamily(BaseModel):
    model_config = ConfigDict(extra="ignore")

    source_address: str | None = None
    access_list: list[AccessEntry] | None = None


class Vrf(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    default: bool = False
    ipv4: VrfFamily | None = None
    ipv6: VrfFamily | None = None


# Mapeia o `nos` usado no hyperglass para o device_type do Netmiko.
_NETMIKO_DEVICE_TYPE: dict[str, str] = {
    "cisco": "cisco_ios",
    "cisco_ios": "cisco_ios",
    "huawei": "huawei",
    "frr": "linux",
}


class Device(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    address: str
    credential: Credential
    port: int = Field(default=22, ge=1, le=65535)
    nos: str = "cisco_ios"
    network: NetworkInfo | None = None
    vrfs: list[Vrf] | None = None

    @property
    def username(self) -> str:
        return self.credential.username

    @property
    def netmiko_device_type(self) -> str:
        return _NETMIKO_DEVICE_TYPE.get(self.nos, self.nos)

    def authentication_options(self) -> dict:
        """Opções de autenticação para o Netmiko (sem segredos em logs)."""
        opts: dict = {}
        if self.credential.key_path:
            opts["use_keys"] = True
            opts["key_file"] = str(Path(self.credential.key_path).expanduser())
        if self.credential.password:
            opts["password"] = self.credential.password
        return opts


def _expand_env(value: object) -> object:
    """Interpola ${VAR} e $VAR em valores do YAML com variáveis do ambiente."""
    if isinstance(value, str):
        return os.path.expandvars(value)
    if isinstance(value, list):
        return [_expand_env(item) for item in value]
    if isinstance(value, dict):
        return {key: _expand_env(item) for key, item in value.items()}
    return value


def load_inventory(path: str | Path) -> list[Device]:
    inventory_file = Path(path).expanduser()
    if not inventory_file.exists():
        raise InventoryError(f"Arquivo de inventário não encontrado: {path}")

    raw = yaml.safe_load(inventory_file.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("routers"), list):
        raise InventoryError(
            f"Inventário inválido em {path}: esperado uma lista em 'routers:'"
        )

    devices: list[Device] = []
    for entry in _expand_env(raw["routers"]):
        if not isinstance(entry, dict):
            raise InventoryError(f"Entrada inválida no inventário: {entry!r}")
        try:
            devices.append(Device(**entry))
        except Exception as exc:
            raise InventoryError(
                f"Falha ao carregar device {entry.get('name', '?')}: {exc}"
            ) from exc

    if not devices:
        raise InventoryError(f"Inventário vazio em {path}")

    return devices


def find_device(devices: list[Device], reference: str) -> Device:
    """Localiza device por nome (ou por ordinal '1', '2'... para uso no CLI)."""
    for device in devices:
        if device.name == reference:
            return device
    if reference.isdigit():
        index = int(reference) - 1
        if 0 <= index < len(devices):
            return devices[index]
    raise InventoryError(f"Device não encontrado: {reference!r}")