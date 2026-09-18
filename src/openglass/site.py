"""Configuração de site/UI (openglass.yaml) — campos exibidos no frontend.

Baseado no hyperglass (https://hyperglass.dev/docs/parameters).

As chaves são tratadas de forma case-insensitive (ex.: `Logo` == `logo`) para
tolerar edições manuais. Seções ainda não usadas pela aplicação são ignoradas,
então o arquivo pode crescer sem quebrar o carregamento.
"""

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class SiteError(Exception):
    pass


def _lower_keys(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key).lower(): _lower_keys(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_lower_keys(item) for item in value]
    return value


class Link(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str
    url: str
    side: str = "left"
    order: int = 0


class Menu(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str
    content: str
    side: str = "right"


class Logo(BaseModel):
    model_config = ConfigDict(extra="ignore")

    dark: str | None = None
    light: str | None = None
    favicon: str | None = None
    height: str | None = None
    width: str | None = None


class ThemeColors(BaseModel):
    model_config = ConfigDict(extra="ignore")

    primary: str = "#38bdf8"
    secondary: str = "#0f172a"
    text: str = "#e2e8f0"
    background: str = "#0f172a"


class Theme(BaseModel):
    model_config = ConfigDict(extra="ignore")

    default: str = "default"
    show_toggle: bool = True
    colors: ThemeColors = Field(default_factory=ThemeColors)


class WebText(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str | None = None
    subtitle: str | None = None
    title_mode: str = "logo_subtitle"
    complete_time: str = "Completed in {seconds}"
    query_location: str = "Location"
    query_target: str = "Target"
    query_type: str = "Query Type"
    query_vrf: str = "Routing Table"


class Web(BaseModel):
    model_config = ConfigDict(extra="ignore")

    links: list[Link] = Field(default_factory=list)
    menus: list[Menu] = Field(default_factory=list)
    logo: Logo = Field(default_factory=Logo)
    text: WebText = Field(default_factory=WebText)
    theme: Theme = Field(default_factory=Theme)

    @model_validator(mode="after")
    def _sort_links(self) -> "Web":
        self.links.sort(key=lambda link: link.order)
        return self


class SiteConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    org_name: str = "OpenGlass"
    primary_asn: int | None = None
    site_title: str = "OpenGlass"
    site_description: str = "{org_name} Network Open Glass"
    site_keywords: list[str] = Field(default_factory=list)
    request_timeout: float = 30
    listen_address: str = "127.0.0.1"
    listen_port: int = 8000
    web: Web = Field(default_factory=Web)

    @model_validator(mode="before")
    @classmethod
    def _normalize_keys(cls, data: Any) -> Any:
        return _lower_keys(data) if isinstance(data, dict) else data

    def render(self, template: str | None) -> str:
        """Interpola {org_name}, {site_title} e {primary_asn} em textos da UI."""
        if not template:
            return ""
        values = {
            "org_name": self.org_name,
            "site_title": self.site_title,
            "primary_asn": "" if self.primary_asn is None else str(self.primary_asn),
        }
        for key, value in values.items():
            template = template.replace("{" + key + "}", value)
        return template

    def frontend_payload(self) -> dict:
        """Subconjunto seguro do config para o navegador (sem segredos)."""
        return {
            "org_name": self.org_name,
            "primary_asn": self.primary_asn,
            "site_title": self.site_title,
            "site_description": self.render(self.site_description),
            "subtitle": self.render(self.web.text.subtitle)
            or self.render(self.site_description),
            "title_mode": (self.web.text.title_mode or "").lower(),
            "links": [link.model_dump() for link in self.web.links],
            "menus": [menu.model_dump() for menu in self.web.menus],
            "logo": self.web.logo.model_dump(),
            "theme": self.web.theme.model_dump(),
        }


def load_site_config(path: str | Path) -> SiteConfig:
    file = Path(path).expanduser()
    if not file.exists():
        return SiteConfig()
    try:
        raw = yaml.safe_load(file.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise SiteError(f"YAML inválido em {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise SiteError(f"Config inválida em {path}: esperado um mapa YAML")
    try:
        return SiteConfig(**raw)
    except Exception as exc:
        raise SiteError(f"Falha ao carregar {path}: {exc}") from exc