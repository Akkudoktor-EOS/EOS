"""Optimizer kernel: fast, algorithm neutral simulation of a plan.

- ``problem``: ``PlanningProblem`` (what is given) and ``Plan`` (what is decided)
- ``simulate``: ``simulate`` a plan, optionally from a later slot, and ``simulate_batch``;
  the thermal devices (heat pumps with a room model) are simulated first and add their
  electricity to the load
- ``core``: the compiled slot loop; ``selfconsumption``: the direct consumption table
"""

from akkudoktoreos.optimization.kernel.problem import GridConnection, Plan, PlanningProblem
from akkudoktoreos.optimization.kernel.simulate import (
    BatchResult,
    SimulationResult,
    ThermalResult,
    ThermalStart,
    initial_state,
    simulate,
    simulate_batch,
)

__all__ = [
    "BatchResult",
    "GridConnection",
    "Plan",
    "PlanningProblem",
    "SimulationResult",
    "ThermalResult",
    "ThermalStart",
    "initial_state",
    "simulate",
    "simulate_batch",
]
