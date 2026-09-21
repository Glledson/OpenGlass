import textwrap

import pytest

from openglass.site import SiteConfig, SiteError, load_site_config

SITE = textwrap.dedent(
    """
    org_name: LG Telecom
    primary_asn: 12345
    site_title: Open Glass LG
    site_description: "{org_name} Network Open Glass"
    listen_address: "0.0.0.0"
    listen_port: 8000
    web:
      links:
        - title: Segundo
          url: https://example.com/2
          side: left
          order: 2
        - title: Primeiro
          url: https://example.com/1
          side: left
          order: 1
      menus:
        - title: Contato
          content: "mailto:noc@example.com"
          side: right
      Logo:
        light: static/images/openglass-light.svg
        favicon: static/images/openglass-icon.svg
      text:
        subtitle: Network Open Glass
        title_mode: Logo_subtitle
      theme:
        colors:
          primary: "#22c55e"
    """
)


def _write(tmp_path, content: str):
    file = tmp_path / "openglass.yaml"
    file.write_text(content, encoding="utf-8")
    return file


class TestLoadSiteConfig:
    def test_loads_and_renders(self, tmp_path) -> None:
        config = load_site_config(_write(tmp_path, SITE))
        assert config.org_name == "LG Telecom"
        assert config.site_title == "Open Glass LG"
        assert config.primary_asn == 12345
        assert config.render(config.site_description) == "LG Telecom Network Open Glass"
        assert config.listen_address == "0.0.0.0"
        assert config.listen_port == 8000

    def test_case_insensitive_keys(self, tmp_path) -> None:
        config = load_site_config(_write(tmp_path, SITE))
        assert config.web.logo.light == "static/images/openglass-light.svg"
        assert config.web.text.title_mode.lower() == "logo_subtitle"

    def test_links_sorted_by_order(self, tmp_path) -> None:
        config = load_site_config(_write(tmp_path, SITE))
        assert [link.title for link in config.web.links] == ["Primeiro", "Segundo"]

    def test_theme_colors(self, tmp_path) -> None:
        config = load_site_config(_write(tmp_path, SITE))
        assert config.web.theme.colors.primary == "#22c55e"

    def test_missing_file_returns_defaults(self, tmp_path) -> None:
        config = load_site_config(tmp_path / "nao_existe.yaml")
        assert config == SiteConfig()
        assert config.site_title == "OpenGlass"

    def test_invalid_yaml_raises(self, tmp_path) -> None:
        with pytest.raises(SiteError):
            load_site_config(_write(tmp_path, "site_title: [sem fechar"))

    def test_frontend_payload(self, tmp_path) -> None:
        payload = load_site_config(_write(tmp_path, SITE)).frontend_payload()
        assert payload["site_title"] == "Open Glass LG"
        assert payload["subtitle"] == "Network Open Glass"
        assert payload["primary_asn"] == 12345
        assert payload["theme"]["colors"]["primary"] == "#22c55e"
        assert len(payload["menus"]) == 1

    def test_links_render_primary_asn(self, tmp_path) -> None:
        config = load_site_config(
            _write(
                tmp_path,
                textwrap.dedent(
                    """
                    primary_asn: 64577
                    web:
                      links:
                        - title: PeeringDB
                          url: https://www.peeringdb.com/asn/{primary_asn}
                          side: left
                          order: 1
                    """
                ),
            )
        )
        payload = config.frontend_payload()
        assert payload["links"][0]["url"] == "https://www.peeringdb.com/asn/64577"

    def test_links_without_placeholders_unchanged(self, tmp_path) -> None:
        config = load_site_config(_write(tmp_path, SITE))
        payload = config.frontend_payload()
        assert payload["links"][0]["url"] == "https://example.com/1"