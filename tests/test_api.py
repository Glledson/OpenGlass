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
              source_address: 45.5.40.255
    """
)


@pytest.fixture
def client(tmp_path, monkeypatch):
    inventory = tmp_path / "devices.yaml"
    inventory.write_text(INVENTORY, encoding="utf-8")
    monkeypatch.setattr(settings, "inventory_path", str(inventory))
    return TestClient(api.app)


def test_index_serves_frontend(client) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "OpenGlass" in response.text


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
    assert names == {"show-ip-route", "show-bgp-summary", "show-bgp-prefix", "ping", "traceroute"}


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


def test_run_success_builds_command(client, monkeypatch) -> None:
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
            return "!! resultado"

    monkeypatch.setattr(api, "DeviceSession", FakeSession)

    response = client.post(
        "/api/run", json={"device": "r1", "command": "ping", "params": {"ip": "8.8.8.8"}}
    )
    assert response.status_code == 200
    assert captured["command"] == "ping 8.8.8.8 source 45.5.40.255"
    body = response.json()
    assert body["command"] == "ping 8.8.8.8 source 45.5.40.255"
    assert body["output"] == "!! resultado"