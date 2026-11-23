"""GENETIC optimizer: the only module code outside the optimizer imports (ADR 0005).

``GeneticOptimizer`` is loaded on first access, so importing the package (e.g. for its settings
model) does not load the optimizer and its simulation.
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from akkudoktoreos.optimization.genetic.geneticoptimizer import GeneticOptimizer

__all__ = ["GeneticOptimizer"]


def __getattr__(name: str) -> Any:
    if name == "GeneticOptimizer":
        from akkudoktoreos.optimization.genetic.geneticoptimizer import (
            GeneticOptimizer,
        )

        return GeneticOptimizer
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
