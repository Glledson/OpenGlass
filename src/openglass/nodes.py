"""Perfil de comandos por NOS (pasta nodes/).

Cada NOS tem um arquivo nodes/<nos>.yaml declarando a whitelist de comandos e
os formatos (templates). Ex.: devices.yaml com `nos: cisco_ios` usa
nodes/cisco_ios.yaml.

Placeholders num template:
- $<param>          → parâmetro informado pelo usuário (declarado em `params`)
- $source_address   → preenchido automaticamente (VRF do device), nunca do usuário
Placeholders desconhecidos são rejeitados no load.
"""

import re
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from openglass.config import settings
from openglass.parsers import PARSER_NAMES


class NodeError(Exception):
    pass


# Placeholders resolvidos pela engine (nunca informados pelo usuário).
AUTO_PLACEHOLDERS = frozenset({"source_address"})

_PLACEHOLDER_RE = re.compile(r"\$([A-Za-z0-9_]+)")


class ParamSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["destination", "ip", "prefix", "hostname"] = "destination"


class NodeCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str = ""
    template: str
    timeout: int | None = None
    params: dict[str, ParamSpec] = Field(default_factory=dict)
    parser: str | None = Field(
        default=None,
        json_schema_extra={"help": "Fase futura: parser estruturado do output"},
    )

    def effective_timeout(self) -> float:
        return float(self.timeout or settings.default_command_timeout)


class NodeProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nos: str
    commands: dict[str, NodeCommand] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_placeholders(self) -> "NodeProfile":
        for label, command in self.commands.items():
            found = set(_PLACEHOLDER_RE.findall(command.template))
            declared = set(command.params)
            allowed = declared | set(AUTO_PLACEHOLDERS)
            unknown = found - allowed
            if unknown:
                raise ValueError(
                    f"{self.nos}: comando '{label}' usa placeholder(s) "
                    f"não declarado(s): {sorted(unknown)!r}"
                )
        return self

    @model_validator(mode="after")
    def _check_parsers(self) -> "NodeProfile":
        for label, command in self.commands.items():
            if command.parser is not None and command.parser not in PARSER_NAMES:
                raise ValueError(
                    f"{self.nos}: comando '{label}' usa parser desconhecido: "
                    f"{command.parser!r} (disponíveis: {sorted(PARSER_NAMES)!r})"
                )
        return self


def _profile_path(nos: str) -> Path:
    return Path(settings.nodes_dir).expanduser() / f"{nos}.yaml"


@lru_cache(maxsize=64)
def load_profile(nos: str) -> NodeProfile:
    """Carrega (com cache) o perfil de comandos do NOS."""
    path = _profile_path(nos)
    if not path.exists():
        raise NodeError(
            f"Perfil de comandos não encontrado para NOS {nos!r}: {path} "
            f"(crie nodes/{nos}.yaml)"
        )
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("commands"), dict):
        raise NodeError(f"Perfil inválido em {path}: esperado seção 'commands:'")
    try:
        return NodeProfile(nos=nos, **raw)
    except Exception as exc:
        raise NodeError(f"Falha ao carregar perfil {path}: {exc}") from exc


def clear_profile_cache() -> None:
    load_profile.cache_clear()