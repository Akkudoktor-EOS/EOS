"""The optimizer registry (ADR 0005)."""

import pytest

from akkudoktoreos.config.config import ConfigEOS
from akkudoktoreos.optimization.optimization import OptimizationAlgorithm
from akkudoktoreos.optimization.optimizer import RunClock
from akkudoktoreos.optimization.optimizerregistry import get_optimizer, optimizer_names


class TestOptimizerRegistry:
    """Finding optimizers by name."""

    def test_every_algorithm_is_registered(self) -> None:
        assert sorted(optimizer_names()) == sorted(OptimizationAlgorithm)

    @pytest.mark.parametrize("name", ["GENETIC", "GENETIC0"])
    def test_registered_optimizer_carries_its_name(self, name: str) -> None:
        optimizer = get_optimizer(name)
        assert optimizer is not None
        assert optimizer.name == name
        # The registry name is the value of optimization.algorithm.
        assert OptimizationAlgorithm(name) == name

    def test_algorithm_enum_finds_the_same_optimizer(self) -> None:
        assert get_optimizer(OptimizationAlgorithm.GENETIC0) is get_optimizer("GENETIC0")

    @pytest.mark.parametrize("name", ["UNKNOWN", "", "genetic", "genetic0"])
    def test_unregistered_name_gives_none(self, name: str) -> None:
        assert get_optimizer(name) is None


class TestRunClock:
    """The slot length and time zone the energy management starts a run in (ADR 0006)."""

    @pytest.mark.parametrize("interval", [900, 3600])
    def test_genetic_follows_its_slot_setting_and_the_site_time_zone(
        self, config_eos: ConfigEOS, interval: int
    ) -> None:
        config_eos.merge_settings_from_dict(
            {
                "general": {"timezone_override": "America/New_York"},
                "optimization": {"genetic": {"interval_sec": interval}},
            }
        )
        optimizer = get_optimizer("GENETIC")
        assert optimizer is not None
        assert optimizer.run_clock() == RunClock(
            slot_seconds=interval, timezone="America/New_York"
        )

    def test_genetic0_runs_hourly_in_server_time(self) -> None:
        optimizer = get_optimizer("GENETIC0")
        assert optimizer is not None
        assert optimizer.run_clock() == RunClock(slot_seconds=3600, timezone=None)
