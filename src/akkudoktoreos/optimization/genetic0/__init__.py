"""GENETIC0 optimizer: the only module code outside the optimizer imports (ADR 0005).

``Genetic0Optimizer`` is loaded on first access, so importing the package (e.g. for its settings
model) does not load the optimizer and its simulation.
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from akkudoktoreos.optimization.genetic0.genetic0optimizer import Genetic0Optimizer

__all__ = ["Genetic0Optimizer"]


def __getattr__(name: str) -> Any:
    if name == "Genetic0Optimizer":
        from akkudoktoreos.optimization.genetic0.genetic0optimizer import (
            Genetic0Optimizer,
        )

        return Genetic0Optimizer
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
