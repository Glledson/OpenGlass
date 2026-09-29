"""Perfil de comandos por NOS (pasta nodes/).

Cada NOS tem um arquivo nodes/<nos>.yaml declarando a whitelist de comandos e
os formatos (templates). Ex.: devices.yaml com `nos: cisco_ios` usa
nodes/cisco_ios.yaml.

`template:` aceita uma string ou um mapa por família:

    template: "ping $ip"
    template:
      ipv4: "ping $ip repeat 5 source $source_address"
      ipv6: "ping ipv6 $ip repeat 5 source $source_address"

A engine escolhe a variante pela família do destino informado pelo usuário.

Placeholders num template:
- $<param>          → parâmetro informado pelo usuário (declarado em `params`)
- $source_address   → preenchido automaticamente (VRF do device), nunca do usuário
- $as_path_regexp   → preenchido automaticamente com a regex montada a partir
                     dos ASNs informados em `params`, no formato de `asn_regexp:`
- $prefix_address   → endereço do prefixo informado, sem a máscara
- $prefix_length    → tamanho da máscara do prefixo informado
Placeholders desconhecidos são rejeitados no load.
"""

import re
from functools import lru_cache
from pathlib import Path
from typing import Dict, Literal, Union

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from openglass.config import settings
from openglass.parsers import PARSER_NAMES


class NodeError(Exception):
    pass


# Placeholders resolvidos pela engine (nunca informados pelo usuário).
AUTO_PLACEHOLDERS = frozenset(
    {
        "source_address",
        "as_path_regexp",
        "prefix_address",
        "prefix_length",
    }
)

# Marcadores usados no `asn_regexp:` do comando, distinto do `$placeholder`
# do template. O VRP trata `|` como separador de modificador de output, então
# o padrão por NOS decide se a alternância entre AS é utilizável.
AS_MARKER_SINGLE = "{asn}"
AS_MARKER_LIST = "{asns}"
AS_MARKERS = (AS_MARKER_SINGLE, AS_MARKER_LIST)

_PLACEHOLDER_RE = re.compile(r"\$([A-Za-z0-9_]+)")

# Famílias aceitas na chave de um `template:` por família.
FAMILIES = ("ipv4", "ipv6")

# Template único ou um por família (`{ipv4: ..., ipv6: ...}`).
Template = Union[str, Dict[str, str]]


class ParamSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal[
        "destination", "ip", "prefix", "hostname", "asn", "community"
    ] = "destination"


class NodeCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str = ""
    template: Template
    timeout: int | None = None
    params: dict[str, ParamSpec] = Field(default_factory=dict)
    asn_regexp: str | None = None
    parser: str | None = Field(
        default=None,
        json_schema_extra={"help": "Fase futura: parser estruturado do output"},
    )

    @model_validator(mode="after")
    def _check_template(self) -> "NodeCommand":
        if isinstance(self.template, str):
            self._check_asn_regexp()
            return self
        unknown = set(self.template) - set(FAMILIES)
        if unknown or not self.template:
            raise ValueError(
                f"template por família aceita apenas {list(FAMILIES)}: "
                f"recebido {sorted(self.template)}"
            )
        self._check_asn_regexp()
        return self

    def _check_asn_regexp(self) -> None:
        """Valida o padrão de regex de AS Path, se declarado.

        Precisa declarar exatamente um marcador (`{asn}` ou `{asns}`) e nada
        além deles entre chaves — assim a engine nunca interpola texto do
        usuário fora dos dígitos já validados.
        """
        if self.asn_regexp is None:
            return
        pattern = self.asn_regexp
        if "asn" not in self.params:
            raise ValueError("asn_regexp exige um parâmetro 'asn'")
        markers = [m for m in AS_MARKERS if m in pattern]
        if len(markers) != 1:
            raise ValueError(
                f"asn_regexp precisa de exatamente um marcador "
                f"{list(AS_MARKERS)}: {pattern!r}"
            )
        residue = pattern
        for marker in markers:
            residue = residue.replace(marker, "")
        if "{" in residue or "}" in residue:
            raise ValueError(f"asn_regexp com marcador desconhecido: {pattern!r}")

    def template_for(self, family: str) -> str:
        """Template da família pedida (ou o template único).

        Levanta ValueError se o comando não declara variante para a família.
        """
        if isinstance(self.template, str):
            return self.template
        try:
            return self.template[family]
        except KeyError as exc:
            raise ValueError(
                f"comando sem variante {family!r} (definidas: {sorted(self.template)})"
            ) from exc

    def all_templates(self) -> list[str]:
        """Todos os templates declarados (para validar os placeholders)."""
        if isinstance(self.template, str):
            return [self.template]
        return list(self.template.values())

    def effective_timeout(self) -> float:
        return float(self.timeout or settings.default_command_timeout)


class NodeProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nos: str
    commands: dict[str, NodeCommand] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_placeholders(self) -> "NodeProfile":
        for label, command in self.commands.items():
            declared = set(command.params)
            allowed = declared | set(AUTO_PLACEHOLDERS)
            for template in command.all_templates():
                unknown = set(_PLACEHOLDER_RE.findall(template)) - allowed
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