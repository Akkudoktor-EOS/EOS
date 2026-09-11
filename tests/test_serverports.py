import errno
import socket
from collections.abc import Generator
from types import SimpleNamespace
from unittest.mock import Mock

import psutil
import pytest

from akkudoktoreos.server import server


@pytest.fixture
def port_environment(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Provide socket probes, optional process diagnostics, and a deterministic clock."""
    probe = Mock()
    probe.__enter__ = Mock(return_value=probe)
    probe.__exit__ = Mock(return_value=False)
    factory = Mock(return_value=probe)
    connections = Mock(side_effect=psutil.AccessDenied(1))
    clock = [0.0]

    def sleep(seconds: float) -> None:
        clock[0] += seconds

    sleep_mock = Mock(side_effect=sleep)
    monkeypatch.setattr(server.socket, "socket", factory)
    monkeypatch.setattr(server.socket, "has_ipv6", False)
    monkeypatch.setattr(server.psutil, "net_if_addrs", Mock(return_value={}))
    monkeypatch.setattr(server.psutil, "net_connections", connections)
    monkeypatch.setattr(server.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(server.time, "sleep", sleep_mock)
    return SimpleNamespace(
        probe=probe, factory=factory, connections=connections, clock=clock, sleep=sleep_mock
    )


def test_free_port_does_not_require_system_process_inspection(
    port_environment: SimpleNamespace,
) -> None:
    env = port_environment

    assert server.wait_for_port_free(8503)

    env.connections.assert_not_called()
    env.sleep.assert_not_called()
    env.probe.__exit__.assert_called_once()


@pytest.mark.parametrize("error", [psutil.AccessDenied(1), PermissionError(1, "Denied")])
def test_occupied_port_remains_occupied_when_diagnostics_are_denied(
    port_environment: SimpleNamespace, error: Exception
) -> None:
    env = port_environment
    env.probe.bind.side_effect = OSError(errno.EADDRINUSE, "Already bound")
    env.connections.side_effect = error

    assert not server.wait_for_port_free(8503)

    env.connections.assert_called_once_with(kind="inet")
    env.sleep.assert_not_called()


@pytest.mark.parametrize("timeout", [1, 5, 6])
def test_port_wait_respects_timeout_with_denied_diagnostics(
    port_environment: SimpleNamespace, timeout: int
) -> None:
    env = port_environment
    env.probe.bind.side_effect = OSError(errno.EADDRINUSE, "Already bound")

    assert not server.wait_for_port_free(8503, timeout=timeout)

    assert env.clock[0] == timeout
    assert all(call.args[0] <= 3 for call in env.sleep.call_args_list)
    env.connections.assert_called_once_with(kind="inet")


def test_port_becomes_available_during_wait(port_environment: SimpleNamespace) -> None:
    env = port_environment
    env.probe.bind.side_effect = [OSError(errno.EADDRINUSE, "Already bound"), None]

    assert server.wait_for_port_free(8503, timeout=5)

    assert env.clock[0] == 3
    env.connections.assert_not_called()


@pytest.mark.parametrize("error", [psutil.AccessDenied(101), psutil.NoSuchProcess(101)])
def test_diagnostic_process_errors_do_not_change_occupied_result(
    port_environment: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    env = port_environment
    env.probe.bind.side_effect = OSError(errno.EADDRINUSE, "Already bound")
    env.connections.side_effect = None
    env.connections.return_value = [
        SimpleNamespace(pid=101, laddr=SimpleNamespace(port=8503)),
        SimpleNamespace(pid=102, laddr=SimpleNamespace(port=8503)),
        SimpleNamespace(pid=102, laddr=SimpleNamespace(port=8503)),
        SimpleNamespace(pid=103, laddr=SimpleNamespace(port=9000)),
        SimpleNamespace(pid=None, laddr=SimpleNamespace(port=8503)),
    ]
    inaccessible = Mock()
    inaccessible.cmdline.side_effect = error
    accessible = Mock()
    accessible.cmdline.return_value = ["python", "-m", "akkudoktoreos.server.eos"]
    process = Mock(side_effect=[inaccessible, accessible])
    monkeypatch.setattr(server.psutil, "Process", process)

    assert not server.wait_for_port_free(8503)

    assert [call.args[0] for call in process.call_args_list] == [101, 102]
    accessible.cmdline.assert_called_once_with()


def test_port_with_unknown_owner_is_still_occupied(port_environment: SimpleNamespace) -> None:
    env = port_environment
    env.probe.bind.side_effect = OSError(errno.EADDRINUSE, "Already bound")
    env.connections.side_effect = None
    env.connections.return_value = [SimpleNamespace(pid=None, laddr=SimpleNamespace(port=8503))]

    assert not server.wait_for_port_free(8503)


def test_ipv6_listener_is_detected_after_free_ipv4_probe(
    port_environment: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = port_environment
    monkeypatch.setattr(server.socket, "has_ipv6", True)
    env.probe.bind.side_effect = [None, OSError(errno.EADDRINUSE, "IPv6 listener")]

    assert not server.wait_for_port_free(8503)

    assert [call.args[0] for call in env.factory.call_args_list] == [
        socket.AF_INET,
        socket.AF_INET6,
    ]


@pytest.mark.parametrize(
    "error_number", [errno.EAFNOSUPPORT, errno.EPROTONOSUPPORT, errno.EADDRNOTAVAIL]
)
def test_disabled_ipv6_does_not_block_free_ipv4_port(
    port_environment: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, error_number: int
) -> None:
    env = port_environment
    monkeypatch.setattr(server.socket, "has_ipv6", True)
    env.factory.side_effect = [env.probe, OSError(error_number, "IPv6 unavailable")]

    assert server.wait_for_port_free(8503)


def test_socket_resource_error_is_not_reported_as_free(port_environment: SimpleNamespace) -> None:
    port_environment.factory.side_effect = OSError(errno.EMFILE, "No file descriptors")

    with pytest.raises(OSError) as error:
        server.wait_for_port_free(8503)

    assert error.value.errno == errno.EMFILE


def test_bind_permission_denied_is_not_reported_as_free(port_environment: SimpleNamespace) -> None:
    port_environment.probe.bind.side_effect = OSError(errno.EACCES, "Cannot bind")

    assert not server.wait_for_port_free(8503)


@pytest.mark.parametrize("port,timeout", [(-1, 0), (65536, 0), (8503, -1)])
def test_port_wait_validates_arguments(
    port_environment: SimpleNamespace, port: int, timeout: int
) -> None:
    with pytest.raises(ValueError):
        server.wait_for_port_free(port, timeout=timeout)

    port_environment.factory.assert_not_called()


@pytest.fixture(params=[socket.AF_INET, socket.AF_INET6])
def listener(request: pytest.FixtureRequest) -> Generator[socket.socket, None, None]:
    """Use real sockets to verify both protocol families as an ordinary user."""
    family = request.param
    try:
        sock = socket.socket(family, socket.SOCK_STREAM)
    except OSError as error:
        if family == socket.AF_INET6 and error.errno in (errno.EAFNOSUPPORT, errno.EPROTONOSUPPORT):
            pytest.skip("IPv6 unavailable")
        raise
    with sock:
        if family == socket.AF_INET6:
            sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("::1" if family == socket.AF_INET6 else "127.0.0.1", 0))
        except OSError as error:
            if family == socket.AF_INET6 and error.errno == errno.EADDRNOTAVAIL:
                pytest.skip("IPv6 loopback unavailable")
            raise
        sock.listen()
        yield sock


def test_real_port_availability_with_denied_enumeration(
    listener: socket.socket, monkeypatch: pytest.MonkeyPatch
) -> None:
    port = listener.getsockname()[1]
    monkeypatch.setattr(server.psutil, "net_connections", Mock(side_effect=psutil.AccessDenied(1)))

    assert not server.wait_for_port_free(port)
    # The probe must leave the existing listener open and untouched.
    assert listener.getsockname()[1] == port
    listener.close()
    assert server.wait_for_port_free(port)


def test_closed_tcp_connection_does_not_delay_port_reuse(
    listener: socket.socket, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A server can restart while its previous connections remain in TIME_WAIT."""
    if server.os.name == "nt":
        pytest.skip("Windows uses exclusive server sockets")
    port = listener.getsockname()[1]
    monkeypatch.setattr(server.psutil, "net_connections", Mock(side_effect=psutil.AccessDenied(1)))
    with socket.socket(listener.family, socket.SOCK_STREAM) as client:
        client.connect(listener.getsockname())
        accepted, _ = listener.accept()
        accepted.close()
        assert client.recv(1) == b""
    listener.close()

    assert server.wait_for_port_free(port)


def test_missing_interface_details_use_exclusive_probe(
    port_environment: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = port_environment
    monkeypatch.setattr(server.psutil, "net_if_addrs", Mock(side_effect=psutil.AccessDenied()))
    env.probe.bind.side_effect = OSError(errno.EADDRINUSE, "Already bound")

    assert not server.wait_for_port_free(8503)

    assert all(call.args[1] != socket.SO_REUSEADDR for call in env.probe.setsockopt.call_args_list)
