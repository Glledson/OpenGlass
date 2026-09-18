from unittest.mock import MagicMock, patch

import pytest
from netmiko.exceptions import NetmikoAuthenticationException, NetmikoTimeoutException

from openglass.connection import (
    DeviceAuthError,
    DeviceError,
    DeviceSSHError,
    DeviceSession,
    DeviceTimeoutError,
    DeviceUnreachableError,
)
from openglass.inventory import Device

DEVICE = Device(
    name="r1",
    address="192.0.2.1",
    credential={"username": "admin", "password": "secret"},
    nos="cisco_ios",
)


@pytest.fixture()
def fake_conn() -> MagicMock:
    conn = MagicMock()
    conn.send_command.return_value = "output cinza cru\nlinha2"
    return conn


class TestConnect:
    def test_success(self, fake_conn) -> None:
        with patch("openglass.connection.ConnectHandler", return_value=fake_conn):
            session = DeviceSession(DEVICE, connect_timeout=5)
            session.connect()
        assert session.is_connected
        session.close()
        assert not session.is_connected

    def test_auth_error_mapped(self) -> None:
        with patch(
            "openglass.connection.ConnectHandler",
            side_effect=NetmikoAuthenticationException("bad login"),
        ):
            session = DeviceSession(DEVICE, connect_timeout=5)
            with pytest.raises(DeviceAuthError):
                session.connect()

    def test_timeout_error_mapped(self) -> None:
        with patch(
            "openglass.connection.ConnectHandler",
            side_effect=NetmikoTimeoutException("tcp timeout"),
        ):
            session = DeviceSession(DEVICE, connect_timeout=5)
            with pytest.raises(DeviceTimeoutError):
                session.connect()

    def test_unreachable_error_mapped(self) -> None:
        with patch(
            "openglass.connection.ConnectHandler",
            side_effect=OSError("connection refused"),
        ):
            session = DeviceSession(DEVICE, connect_timeout=5)
            with pytest.raises(DeviceUnreachableError):
                session.connect()

    def test_other_error_mapped(self, fake_conn) -> None:
        with patch(
            "openglass.connection.ConnectHandler", side_effect=RuntimeError("boom")
        ):
            session = DeviceSession(DEVICE, connect_timeout=5)
            with pytest.raises(DeviceSSHError):
                session.connect()

    def test_connect_uses_netmiko_kwargs(self, fake_conn) -> None:
        with patch("openglass.connection.ConnectHandler", return_value=fake_conn) as mock:
            DeviceSession(DEVICE, connect_timeout=7).connect()
        kwargs = mock.call_args.kwargs
        assert kwargs["device_type"] == "cisco_ios"
        assert kwargs["host"] == "192.0.2.1"
        assert kwargs["username"] == "admin"
        assert kwargs["password"] == "secret"
        assert kwargs["timeout"] == 7


class TestRunCommand:
    def test_requires_connect(self) -> None:
        session = DeviceSession(DEVICE)
        with pytest.raises(DeviceError):
            session.run_command("show ip route")

    def test_returns_raw_output(self, fake_conn) -> None:
        with patch("openglass.connection.ConnectHandler", return_value=fake_conn):
            with DeviceSession(DEVICE, connect_timeout=5) as session:
                output = session.run_command("show ip route", timeout=20)
        assert output == "output cinza cru\nlinha2"
        fake_conn.send_command.assert_called_once_with("show ip route", read_timeout=20)

    def test_read_timeout_mapped(self, fake_conn) -> None:
        fake_conn.send_command.side_effect = NetmikoTimeoutException("lento")
        with patch("openglass.connection.ConnectHandler", return_value=fake_conn):
            session = DeviceSession(DEVICE, connect_timeout=5)
            session.connect()
            with pytest.raises(DeviceTimeoutError):
                session.run_command("show ip route")
            session.close()


class TestClose:
    def test_context_manager_closes(self, fake_conn) -> None:
        with patch("openglass.connection.ConnectHandler", return_value=fake_conn):
            with DeviceSession(DEVICE, connect_timeout=5) as session:
                assert session.is_connected
            assert not session.is_connected
            fake_conn.disconnect.assert_called_once()

    def test_close_after_error(self, fake_conn) -> None:
        fake_conn.send_command.side_effect = RuntimeError("ssh rompeu")
        with patch("openglass.connection.ConnectHandler", return_value=fake_conn):
            with DeviceSession(DEVICE, connect_timeout=5) as session:
                with pytest.raises(DeviceSSHError):
                    session.run_command("show ip route")
            # fechamento garantido mesmo após erro
            fake_conn.disconnect.assert_called_once()