import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import psutil
import pytest
import requests
from conftest import cleanup_eos_eosdash, server_base
from xprocess import ProcessStarter


@pytest.fixture
def cleanup_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> SimpleNamespace:
    """Isolate every process and network operation performed by server cleanup."""
    processes: dict[int, Mock] = {}
    process_type = psutil.Process

    def make_process(pid: int, module: str = "akkudoktoreos.server.eos") -> Mock:
        process = Mock(spec=process_type)
        # Track either platform's termination method with the same mock.
        process.terminate = process.kill
        process.pid = pid
        process.cmdline.return_value = ["python", "-m", module]
        process.environ.return_value = {"EOS_CONFIG_DIR": str(tmp_path)}
        process.children.return_value = []
        process.status.return_value = psutil.STATUS_RUNNING
        processes[pid] = process
        return process

    def get_process(pid: int) -> Mock:
        if pid not in processes:
            raise psutil.NoSuchProcess(pid)
        return processes[pid]

    connections = Mock(return_value=[])
    health = Mock(side_effect=requests.ConnectionError)
    wait = Mock(return_value=([], []))
    monkeypatch.setattr("conftest.psutil.Process", get_process)
    monkeypatch.setattr("conftest.psutil.net_connections", connections)
    monkeypatch.setattr("conftest.psutil.wait_procs", wait)
    monkeypatch.setattr("conftest.requests.get", health)

    return SimpleNamespace(
        make_process=make_process,
        connections=connections,
        health=health,
        wait=wait,
        config_dir=str(tmp_path),
    )


def run_cleanup(environment: SimpleNamespace, *processes: psutil.Process) -> None:
    cleanup_eos_eosdash(
        "127.0.0.1",
        8503,
        "127.0.0.1",
        8555,
        owned_processes=processes,
        config_dir=environment.config_dir,
    )


def connection(pid: int | None, port: int) -> SimpleNamespace:
    return SimpleNamespace(pid=pid, laddr=SimpleNamespace(port=port))


@pytest.mark.parametrize("error", [psutil.AccessDenied(1), PermissionError(1, "Denied")])
def test_cleanup_owned_tree_when_enumeration_is_denied(
    cleanup_environment: SimpleNamespace, error: Exception
) -> None:
    """Protected system processes must not prevent termination of owned servers."""
    env = cleanup_environment
    parent = env.make_process(101)
    child = env.make_process(102, "akkudoktoreos.server.eosdash")
    parent.children.return_value = [child]
    env.connections.side_effect = error

    run_cleanup(env, parent)

    parent.kill.assert_called_once_with()
    child.kill.assert_called_once_with()
    env.connections.assert_called_once_with(kind="inet")
    assert env.wait.call_args.args[0] == [parent, child]
    assert 0 <= env.wait.call_args.kwargs["timeout"] <= 10


def test_cleanup_uses_verified_health_pid_without_connection_inspection(
    cleanup_environment: SimpleNamespace,
) -> None:
    """A restarted test server can be found without system-wide connections."""
    env = cleanup_environment
    restarted = env.make_process(103)
    # EOS restarts using the script path rather than `python -m`.
    script = Path(__file__).parent.parent / "src/akkudoktoreos/server/eos.py"
    restarted.cmdline.return_value = ["python", str(script)]
    response = Mock(status_code=200)
    response.json.return_value = {"pid": restarted.pid}
    env.health.side_effect = [response, requests.ConnectionError()]
    env.connections.side_effect = psutil.AccessDenied(1)

    run_cleanup(env)

    restarted.kill.assert_called_once_with()


def test_cleanup_connection_fallback_is_limited_to_verified_test_servers(
    cleanup_environment: SimpleNamespace,
) -> None:
    """Port matches alone must not kill other applications or another EOS instance."""
    env = cleanup_environment
    eos = env.make_process(101)
    dashboard = env.make_process(102, "akkudoktoreos.server.eosdash")
    other_application = env.make_process(103, "unrelated.application")
    other_test = env.make_process(104)
    other_test.environ.return_value = {"EOS_CONFIG_DIR": "/another/test"}
    misleading_module = env.make_process(105, "akkudoktoreos.server.eos_extra")
    wrong_port = env.make_process(106, "akkudoktoreos.server.eosdash")
    env.connections.return_value = [
        connection(101, 8503),
        connection(101, 8503),
        connection(102, 8555),
        connection(103, 8503),
        connection(104, 8503),
        connection(105, 8503),
        connection(106, 8504),
        connection(None, 8503),
    ]

    run_cleanup(env)

    eos.kill.assert_called_once_with()
    dashboard.kill.assert_called_once_with()
    for process in (other_application, other_test, misleading_module, wrong_port):
        process.kill.assert_not_called()


@pytest.mark.parametrize("pid", [103, True, "103", -1, None])
def test_cleanup_does_not_trust_health_pid(
    cleanup_environment: SimpleNamespace, pid: object
) -> None:
    """A health response cannot authorize termination of an unrelated process."""
    env = cleanup_environment
    unrelated = env.make_process(103, "unrelated.application")
    response = Mock(status_code=200)
    response.json.return_value = {"pid": pid}
    env.health.side_effect = [response, requests.ConnectionError()]

    run_cleanup(env)

    unrelated.kill.assert_not_called()


@pytest.mark.parametrize("operation", ["cmdline", "environ"])
def test_cleanup_skips_inaccessible_fallback_process(
    cleanup_environment: SimpleNamespace, operation: str
) -> None:
    """One inaccessible process must not hide a subsequent verified server."""
    env = cleanup_environment
    protected = env.make_process(103)
    getattr(protected, operation).side_effect = psutil.AccessDenied(103)
    owned = env.make_process(104)
    env.connections.return_value = [connection(103, 8503), connection(104, 8503)]

    run_cleanup(env)

    protected.kill.assert_not_called()
    owned.kill.assert_called_once_with()


def test_cleanup_stops_owned_process_when_children_are_inaccessible(
    cleanup_environment: SimpleNamespace,
) -> None:
    env = cleanup_environment
    owned = env.make_process(101)
    owned.children.side_effect = psutil.AccessDenied(101)

    run_cleanup(env, owned)

    owned.kill.assert_called_once_with()


def test_cleanup_tolerates_process_exit_during_termination(
    cleanup_environment: SimpleNamespace,
) -> None:
    env = cleanup_environment
    exited = env.make_process(101)
    exited.kill.side_effect = psutil.NoSuchProcess(101)
    remaining = env.make_process(102)

    run_cleanup(env, exited, remaining)

    remaining.kill.assert_called_once_with()


@pytest.mark.parametrize("status", [psutil.STATUS_ZOMBIE, psutil.STATUS_RUNNING])
def test_cleanup_reports_only_live_processes_after_bounded_wait(
    cleanup_environment: SimpleNamespace, status: str
) -> None:
    env = cleanup_environment
    process = env.make_process(101)
    process.status.return_value = status
    env.wait.return_value = ([], [process])

    if status == psutil.STATUS_RUNNING:
        with pytest.raises(AssertionError, match="cleanup timed out.*101"):
            run_cleanup(env, process)
    else:
        run_cleanup(env, process)


@pytest.mark.parametrize("startup_failure", [False, True])
def test_server_base_cleans_owned_process_and_directory_on_failure(
    cleanup_environment: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    startup_failure: bool,
) -> None:
    """Both startup failures and exceptions from the test body run teardown."""
    env = cleanup_environment
    process = env.make_process(101)
    xprocess = Mock()
    xprocess.getinfo.return_value.pid = process.pid
    config_dirs: list[Path] = []
    monkeypatch.setattr("conftest.subprocess.run", Mock())
    monkeypatch.setattr("conftest.ProcessStarter.wait", Mock(return_value=True))

    def ensure(name: str, starter_type: type[ProcessStarter]) -> tuple[int, str]:
        config_dirs.append(Path(starter_type.env["EOS_CONFIG_DIR"]))
        assert name == f"eos-{config_dirs[-1].name}"
        starter = starter_type(None, xprocess)
        starter.wait(Mock())
        if startup_failure:
            raise RuntimeError("startup failed")
        return process.pid, "server.log"

    xprocess.ensure.side_effect = ensure
    with pytest.raises(RuntimeError, match="failed"):
        with server_base(xprocess):
            raise RuntimeError("test body failed")

    process.kill.assert_called_once_with()
    assert config_dirs and not config_dirs[0].exists()


@pytest.mark.parametrize("reported_pid", [101, 999])
def test_server_startup_check_rejects_another_server_on_the_same_port(
    cleanup_environment: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    reported_pid: int,
) -> None:
    """A pre-existing server must never be mistaken for this test's process."""
    env = cleanup_environment
    process = env.make_process(101)
    xprocess = Mock()
    xprocess.getinfo.return_value.pid = process.pid
    monkeypatch.setattr("conftest.subprocess.run", Mock())
    monkeypatch.setattr("conftest.ProcessStarter.wait", Mock(return_value=True))
    response = Mock(status_code=200)
    response.json.return_value = {"pid": reported_pid}
    env.health.side_effect = None
    env.health.return_value = response

    def ensure(name: str, starter_type: type[ProcessStarter]) -> tuple[int, str]:
        starter = starter_type(None, xprocess)
        starter.wait(Mock())
        assert starter.startup_check() is (reported_pid == process.pid)
        return process.pid, "server.log"

    xprocess.ensure.side_effect = ensure
    with server_base(xprocess):
        pass


@pytest.mark.parametrize("enumeration_denied", [False, True])
def test_cleanup_terminates_real_owned_subprocess(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, enumeration_denied: bool
) -> None:
    """Exercise real process termination with available and denied enumeration."""
    connections = Mock(return_value=[])
    if enumeration_denied:
        connections.side_effect = psutil.AccessDenied(1)
    monkeypatch.setattr("conftest.psutil.net_connections", connections)
    monkeypatch.setattr("conftest.requests.get", Mock(side_effect=requests.ConnectionError))
    process = subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stdin.buffer.read()"],
        stdin=subprocess.PIPE,
    )
    try:
        owned = psutil.Process(process.pid)
        cleanup_eos_eosdash(
            "127.0.0.1",
            8503,
            "127.0.0.1",
            8504,
            owned_processes=[owned],
            config_dir=str(tmp_path),
        )
        assert not owned.is_running()
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        if process.stdin is not None:
            process.stdin.close()
