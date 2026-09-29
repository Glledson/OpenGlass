import textwrap

import pytest
from fastapi.testclient import TestClient

import openglass.api as api
from openglass.config import settings

INVENTORY = textwrap.dedent(
    """
    routers:
      - name: r1
        address: 192.0.2.1
        nos: cisco_ios
        credential:
          username: admin
          password: secret
        vrfs:
          - name: global
            default: true
            ipv4:
              source_address: 192.0.2.10
    """
)


@pytest.fixture
def client(tmp_path, monkeypatch):
    inventory = tmp_path / "devices.yaml"
    inventory.write_text(INVENTORY, encoding="utf-8")
    config = tmp_path / "openglass.yaml"
    config.write_text(
        "org_name: Teste\n"
        "primary_asn: 64500\n"
        "site_title: Open Glass Teste\n"
        'site_description: "{org_name} Network Open Glass"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "inventory_path", str(inventory))
    monkeypatch.setattr(settings, "config_path", str(config))
    return TestClient(api.app)


def test_index_serves_frontend(client) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "OpenGlass" in response.text


def test_site_config_for_frontend(client) -> None:
    response = client.get("/api/site")
    assert response.status_code == 200
    body = response.json()
    assert body["site_title"] == "Open Glass Teste"
    assert body["primary_asn"] == 64500
    assert body["site_description"] == "Teste Network Open Glass"


def test_devices_hide_credentials(client) -> None:
    response = client.get("/api/devices")
    assert response.status_code == 200
    assert response.json() == [
        {"name": "r1", "nos": "cisco_ios", "address": "192.0.2.1"}
    ]
    assert "secret" not in response.text


def test_commands_for_device(client) -> None:
    response = client.get("/api/devices/r1/commands")
    assert response.status_code == 200
    names = {command["name"] for command in response.json()}
    assert names == {
        "bgp-route",
        "bgp-as-path",
        "bgp-community",
        "ping",
        "traceroute",
    }


def test_unknown_device_returns_404(client) -> None:
    assert client.get("/api/devices/nope/commands").status_code == 404


def test_run_rejects_unknown_command(client) -> None:
    response = client.post(
        "/api/run", json={"device": "r1", "command": "enable", "params": {}}
    )
    assert response.status_code == 400


def test_run_rejects_injection(client) -> None:
    response = client.post(
        "/api/run",
        json={"device": "r1", "command": "ping", "params": {"ip": "8.8.8.8; reload"}},
    )
    assert response.status_code == 400


def test_run_rejects_reserved_range_with_alert(client) -> None:
    response = client.post(
        "/api/run",
        json={"device": "r1", "command": "ping", "params": {"ip": "192.168.1.1"}},
    )
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["code"] == "reserved_range"
    assert detail["meta"]["requested"] == "192.168.1.1"
    assert detail["meta"]["rfc"] == "RFC 1918"
    assert detail["meta"]["network"] == "192.168.0.0/16"
    assert detail["meta"]["purpose"] == "Privado"
    assert "faixa reservada" in detail["message"]


def test_run_rejects_reserved_prefix(client) -> None:
    response = client.post(
        "/api/run",
        json={"device": "r1", "command": "bgp-route", "params": {"prefix": "10.0.0.0/8"}},
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "reserved_range"


def test_run_success_builds_command(client, monkeypatch) -> None:
    captured = {}
    ping_output = (
        "Sending 5, 100-byte ICMP Echos to 8.8.8.8, timeout is 2 seconds:\n"
        "Packet sent with a source address of 192.0.2.10\n"
        "!!!!!\n"
        "Success rate is 100 percent (5/5), round-trip min/avg/max = 1/2/3 ms\n"
    )

    class FakeSession:
        def __init__(self, device):
            self.device = device

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def run_command(self, command, timeout):
            captured["command"] = command
            captured["timeout"] = timeout
            return ping_output

    monkeypatch.setattr(api, "DeviceSession", FakeSession)

    response = client.post(
        "/api/run", json={"device": "r1", "command": "ping", "params": {"ip": "8.8.8.8"}}
    )
    assert response.status_code == 200
    assert captured["command"] == "ping 8.8.8.8 repeat 5 source 192.0.2.10"
    body = response.json()
    assert body["command"] == "ping 8.8.8.8 repeat 5 source 192.0.2.10"
    assert body["output"] == ping_output
    assert body["parser"] == "ping"
    assert body["parsed"]["status"] == "success"
    assert body["parsed"]["target"] == "8.8.8.8"
    assert body["truncated"] is False
    assert body["line_count"] == 4


def _fake_session_returning(output: str):
    class FakeSession:
        def __init__(self, device):
            self.device = device

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def run_command(self, command, timeout):
            return output

    return FakeSession


def test_run_flags_large_output_as_truncated(client, monkeypatch) -> None:
    # tabela BGP inteira: avisa mas não corta o output
    output = "\n".join(f"BGP line {i}" for i in range(5001))
    monkeypatch.setattr(api, "DeviceSession", _fake_session_returning(output))

    response = client.post(
        "/api/run",
        json={"device": "r1", "command": "bgp-as-path", "params": {"asn": "3356"}},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["truncated"] is True
    assert body["line_count"] == 5001
    assert body["output"] == output


def test_run_sends_asn_built_regex_and_long_timeout(client, monkeypatch) -> None:
    captured = {}

    class FakeSession:
        def __init__(self, device):
            self.device = device

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def run_command(self, command, timeout):
            captured["command"] = command
            captured["timeout"] = timeout
            return "BGP table version is 1"

    monkeypatch.setattr(api, "DeviceSession", FakeSession)

    response = client.post(
        "/api/run",
        json={
            "device": "r1",
            "command": "bgp-as-path",
            "params": {"asn": "3356,15169"},
        },
    )
    assert response.status_code == 200
    assert captured["command"] == (
        'show bgp ipv4 unicast quote-regexp "(_3356_|_15169_)"'
    )
    assert captured["timeout"] == 300


def test_run_rejects_invalid_asn_before_connecting(client, monkeypatch) -> None:
    def explode(*args, **kwargs):  # pragma: no cover - não deve conectar
        raise AssertionError("não deveria abrir conexão")

    monkeypatch.setattr(api, "DeviceSession", explode)

    response = client.post(
        "/api/run",
        json={"device": "r1", "command": "bgp-as-path", "params": {"asn": "3356; reboot"}},
    )
    assert response.status_code == 400


def test_run_rejects_private_asn_before_connecting(client, monkeypatch) -> None:
    def explode(*args, **kwargs):  # pragma: no cover - não deve conectar
        raise AssertionError("não deveria abrir conexão")

    monkeypatch.setattr(api, "DeviceSession", explode)

    response = client.post(
        "/api/run",
        json={"device": "r1", "command": "bgp-as-path", "params": {"asn": "65000"}},
    )
    assert response.status_code == 400
    detail = response.json()["detail"]
    # mesmo alert card das faixas de IP: o front renderiza requested/rfc/
    # network/purpose, e aqui `network` é o intervalo de AS.
    assert detail["code"] == "reserved_range"
    assert detail["meta"] == {
        "requested": "65000",
        "rfc": "RFC 6996",
        "network": "64512-65534",
        "purpose": "Privado (16 bits)",
    }


def test_run_rejects_benchmarking_prefix_before_connecting(client, monkeypatch) -> None:
    def explode(*args, **kwargs):  # pragma: no cover - não deve conectar
        raise AssertionError("não deveria abrir conexão")

    monkeypatch.setattr(api, "DeviceSession", explode)

    response = client.post(
        "/api/run",
        json={"device": "r1", "command": "bgp-route", "params": {"prefix": "198.18.0.0/24"}},
    )
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["code"] == "reserved_range"
    assert detail["meta"]["network"] == "198.18.0.0/15"
    assert detail["meta"]["rfc"] == "RFC 2544"