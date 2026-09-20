import pytest

from openglass.inventory import InventoryError, Device, load_inventory


def _write_yaml(tmp_path, content: str):
    path = tmp_path / "devices.yaml"
    path.write_text(content, encoding="utf-8")
    return path


GOOD_YAML = """
routers:
  - name: r1
    address: 192.0.2.1
    credential:
      username: admin
      password: secret
    nos: cisco_ios
  - name: r2
    address: 192.0.2.2
    credential:
      username: netop
      key_path: ~/.ssh/id_rsa
    port: 2222
    nos: cisco
"""


class TestInventory:
    def test_load_multiple_devices(self, tmp_path) -> None:
        path = _write_yaml(tmp_path, GOOD_YAML)
        devices = load_inventory(path)
        assert [d.name for d in devices] == ["r1", "r2"]
        assert devices[0].address == "192.0.2.1"
        assert devices[0].username == "admin"
        assert devices[0].port == 22
        assert devices[1].port == 2222
        assert devices[1].credential.key_path == "~/.ssh/id_rsa"
        assert devices[1].netmiko_device_type == "cisco_ios"

    def test_nos_huawei_maps_to_device_type(self) -> None:
        device = Device(name="h1", address="10.0.0.1", nos="huawei",
                        credential={"username": "u", "password": "p"})
        assert device.netmiko_device_type == "huawei"

    def test_network_and_vrfs_stored(self, tmp_path) -> None:
        path = _write_yaml(tmp_path, GOOD_YAML)
        devices = load_inventory(path)
        assert devices[0].network is None

    def test_missing_file(self, tmp_path) -> None:
        with pytest.raises(InventoryError):
            load_inventory(tmp_path / "nope.yaml")

    def test_empty_rejected(self, tmp_path) -> None:
        path = _write_yaml(tmp_path, "routers: []\n")
        with pytest.raises(InventoryError):
            load_inventory(path)

    def test_wrong_top_level_key_rejected(self, tmp_path) -> None:
        path = _write_yaml(tmp_path, "devices: []\n")
        with pytest.raises(InventoryError):
            load_inventory(path)

    def test_missing_auth_rejected(self, tmp_path) -> None:
        path = _write_yaml(
            tmp_path,
            "routers:\n  - name: r1\n    address: 192.0.2.1\n    credential:\n      username: u\n",
        )
        with pytest.raises(InventoryError):
            load_inventory(path)

    def test_env_interpolation(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("ROUTER_PASSWORD", "from-env")
        path = _write_yaml(
            tmp_path,
            "routers:\n  - name: r1\n    address: h\n    credential:\n"
            "      username: u\n      password: ${ROUTER_PASSWORD}\n",
        )
        device = load_inventory(path)[0]
        assert device.credential.password == "from-env"

    def test_disallows_unknown_extra_fields(self, tmp_path) -> None:
        path = _write_yaml(tmp_path, "routers:\n  - name: r1\n    address: h\n"
                                     "    credential:\n      username: u\n      password: p\n"
                                     "    inventado: x\n")
        with pytest.raises(InventoryError):
            load_inventory(path)

    def test_vrfs_stored_for_future(self, tmp_path) -> None:
        content = """
routers:
  - name: r1
    address: 192.0.2.1
    credential:
      username: u
      password: p
    nos: cisco_ios
    vrfs:
      - name: global
        default: true
        ipv4:
          source_address: 192.0.2.10
          access_list:
            - network: 0.0.0.0/0
              action: permit
              ge: 8
              le: 32
        ipv6:
          source_address: 2001:db8::1
          access_list:
            - network: ::/0
              action: permit
              ge: 32
              le: 128
"""
        path = _write_yaml(tmp_path, content)
        device = load_inventory(path)[0]
        assert device.vrfs is not None
        vrf = device.vrfs[0]
        assert vrf.name == "global"
        assert vrf.default is True
        assert vrf.ipv4.source_address == "192.0.2.10"
        assert vrf.ipv4.access_list[0].network == "0.0.0.0/0"
        assert vrf.ipv4.access_list[0].action == "permit"
        assert vrf.ipv6.access_list[0].le == 128

    def test_snmp_community_stored(self, tmp_path) -> None:
        content = """
routers:
  - name: r1
    address: 192.0.2.1
    credential:
      username: u
      password: p
    nos: cisco_ios
    snmp:
      community: public
"""
        path = _write_yaml(tmp_path, content)
        device = load_inventory(path)[0]
        assert device.snmp is not None
        assert device.snmp.community == "public"

    def test_snmp_optional(self, tmp_path) -> None:
        path = _write_yaml(tmp_path, GOOD_YAML)
        assert load_inventory(path)[0].snmp is None