"""GENETIC optimizer: the only module code outside the optimizer imports (ADR 0005).

``GeneticOptimizer``, ``GeneticSettings`` and ``normalize_genetic_settings`` are loaded on first
access, so importing the package does not load the optimizer and its simulation.
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from akkudoktoreos.optimization.genetic.geneticoptimizer import GeneticOptimizer
    from akkudoktoreos.optimization.genetic.geneticsettings import (
        GeneticSettings,
        normalize_genetic_settings,
    )

__all__ = ["GeneticOptimizer", "GeneticSettings", "normalize_genetic_settings"]


def __getattr__(name: str) -> Any:
    if name == "GeneticOptimizer":
        from akkudoktoreos.optimization.genetic.geneticoptimizer import (
            GeneticOptimizer,
        )

        return GeneticOptimizer
    if name in ("GeneticSettings", "normalize_genetic_settings"):
        from akkudoktoreos.optimization.genetic import geneticsettings

        return getattr(geneticsettings, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
