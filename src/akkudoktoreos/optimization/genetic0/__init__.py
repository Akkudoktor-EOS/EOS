"""GENETIC0 optimizer: the only module code outside the optimizer imports (ADR 0005).

``Genetic0Optimizer`` and ``Genetic0Settings`` are loaded on first access, so importing the package
does not load the optimizer and its simulation.
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from akkudoktoreos.optimization.genetic0.genetic0optimizer import Genetic0Optimizer
    from akkudoktoreos.optimization.genetic0.genetic0settings import Genetic0Settings

__all__ = ["Genetic0Optimizer", "Genetic0Settings"]


def __getattr__(name: str) -> Any:
    if name == "Genetic0Optimizer":
        from akkudoktoreos.optimization.genetic0.genetic0optimizer import (
            Genetic0Optimizer,
        )

        return Genetic0Optimizer
    if name == "Genetic0Settings":
        from akkudoktoreos.optimization.genetic0.genetic0settings import (
            Genetic0Settings,
        )

        return Genetic0Settings
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
