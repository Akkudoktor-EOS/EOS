"""The optimizer registry (ADR 0005)."""

import pytest

from akkudoktoreos.optimization.optimization import OptimizationAlgorithm
from akkudoktoreos.optimization.optimizerregistry import get_optimizer


class TestOptimizerRegistry:
    """Finding optimizers by name."""

    @pytest.mark.parametrize("name", ["GENETIC0"])
    def test_registered_optimizer_carries_its_name(self, name: str) -> None:
        optimizer = get_optimizer(name)
        assert optimizer is not None
        assert optimizer.name == name
        # The registry name is the value of optimization.algorithm.
        assert OptimizationAlgorithm(name) == name

    def test_algorithm_enum_finds_the_same_optimizer(self) -> None:
        assert get_optimizer(OptimizationAlgorithm.GENETIC0) is get_optimizer("GENETIC0")

    @pytest.mark.parametrize("name", ["UNKNOWN", "", "genetic0"])
    def test_unregistered_name_gives_none(self, name: str) -> None:
        assert get_optimizer(name) is None
