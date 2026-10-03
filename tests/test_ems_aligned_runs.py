"""Energy management runs can start on the wall-clock multiples of the interval."""

from types import SimpleNamespace

import pytest

from akkudoktoreos.core import ems as ems_module
from akkudoktoreos.core.ems import ems_run_is_due, next_interval_boundary
from akkudoktoreos.server.retentionmanager import RetentionManager

QUARTER = 900.0


@pytest.mark.parametrize(
    "now,expected",
    [
        (10 * QUARTER + 10, 11 * QUARTER),
        (11 * QUARTER - 1, 11 * QUARTER),
        (11 * QUARTER, 11 * QUARTER),  # exactly on the boundary
    ],
)
def test_next_interval_boundary(now, expected):
    assert next_interval_boundary(now, QUARTER) == expected


def _planned() -> float:
    """Wall-clock time of the planned next start."""
    planned = ems_module._planned_start
    assert planned is not None
    return planned[1]


def _job(interval=QUARTER, last_run_at=0.0):
    return SimpleNamespace(interval=lambda: interval, last_run_at=last_run_at)


@pytest.fixture
def clock(monkeypatch):
    state = SimpleNamespace(wall=10 * QUARTER + 10, mono=5000.0, aligned=False)
    monkeypatch.setattr(ems_module.time, "time", lambda: state.wall)
    monkeypatch.setattr(ems_module.time, "monotonic", lambda: state.mono)
    monkeypatch.setattr(
        ems_module,
        "get_config",
        lambda: SimpleNamespace(ems=SimpleNamespace(start_on_interval_boundary=state.aligned)),
    )
    monkeypatch.setattr(ems_module, "_planned_start", None)
    return state


def test_default_runs_interval_after_the_previous_run(clock):
    job = _job(last_run_at=clock.mono - 899)
    assert ems_run_is_due(job) is False
    job.last_run_at = clock.mono - 900
    assert ems_run_is_due(job) is True


def test_first_run_starts_right_away(clock):
    clock.aligned = True
    assert ems_run_is_due(_job(last_run_at=0.0)) is True


def test_run_starts_on_the_quarter_hour(clock):
    clock.aligned = True
    job = _job(last_run_at=clock.mono)  # previous run ended 10 s after a boundary

    assert ems_run_is_due(job) is False
    clock.wall = 11 * QUARTER - 1
    assert ems_run_is_due(job) is False
    clock.wall = 11 * QUARTER
    assert ems_run_is_due(job) is True


def test_long_run_is_followed_by_the_next_boundary_after_it_ended(clock):
    clock.aligned = True
    # Started 10 * QUARTER, took 16.5 min: ended inside the next quarter hour.
    clock.wall = 10 * QUARTER + 990
    job = _job(last_run_at=clock.mono)

    assert ems_run_is_due(job) is False
    assert _planned() == 12 * QUARTER


def test_start_is_replanned_when_the_interval_changes(clock):
    clock.aligned = True
    job = _job(last_run_at=clock.mono)
    assert ems_run_is_due(job) is False
    assert _planned() == 11 * QUARTER

    job.interval = lambda: 60.0  # a client shortens the interval
    assert ems_run_is_due(job) is False
    assert _planned() == 10 * QUARTER + 60


def test_disabled_job_is_never_due(clock):
    clock.aligned = True
    assert ems_run_is_due(_job(interval=None)) is False


@pytest.mark.asyncio
async def test_retention_manager_uses_the_due_check():
    calls: list[str] = []
    due = {"value": False}
    manager = RetentionManager(config_getter=lambda key: 900.0)
    manager.register(
        "job", lambda: calls.append("run"), interval_attr="x", due_check=lambda job: due["value"]
    )

    await manager.tick()
    await manager.shutdown()
    assert calls == []  # never run, but the due check says no

    due["value"] = True
    await manager.tick()
    await manager.shutdown()
    assert calls == ["run"]
