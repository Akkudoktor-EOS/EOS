"""The registry of optimizers (ADR 0005).

The energy management, the server and EOSdash find an optimizer only here, by its name (the
value of ``optimization.algorithm``). Optimizers are imported on first use, so a server that runs
one optimizer never loads another.
"""

from collections.abc import Callable
from typing import Any, Optional

from akkudoktoreos.optimization.optimizer import Optimizer


def _genetic0() -> Optimizer[Any, Any, Any]:
    from akkudoktoreos.optimization.genetic0 import Genetic0Optimizer

    return Genetic0Optimizer()


# Optimizer name -> factory. GENETIC joins when it is migrated.
_FACTORIES: dict[str, Callable[[], Optimizer[Any, Any, Any]]] = {
    "GENETIC0": _genetic0,
}

_optimizers: dict[str, Optimizer[Any, Any, Any]] = {}


def get_optimizer(name: str) -> Optional[Optimizer[Any, Any, Any]]:
    """Return the optimizer registered under ``name``.

    Args:
        name: The optimizer's name, e.g. ``"GENETIC0"``.

    Returns:
        The optimizer, or ``None`` if no optimizer is registered under that name.
    """
    optimizer = _optimizers.get(name)
    if optimizer is None:
        factory = _FACTORIES.get(name)
        if factory is None:
            return None
        optimizer = factory()
        _optimizers[name] = optimizer
    return optimizer


def optimizer_names() -> list[str]:
    """Names of all registered optimizers.

    Returns:
        The names, e.g. ``["GENETIC0"]``.
    """
    return list(_FACTORIES)
