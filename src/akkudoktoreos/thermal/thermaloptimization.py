"""Explicit single-zone thermal/FLEET shadow planning, without configuration or I/O.

Electrical limits use W/Wh and EUR/kWh, as in FLEET. The legacy RC2 API takes
effective electrical input; the generic adapter supplies thermal building
dynamics and an explicit source conversion. A room curve is a model proposal:
native thermostat tracking is not simulated or authorized.
"""

import copy
import hashlib
import os
import re
import tempfile
import time
import warnings
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Literal, Self, TypeVar

import numpy as np
import numpy.typing as npt
from pydantic import (
    Field,
    SerializerFunctionWrapHandler,
    model_serializer,
    model_validator,
)

from akkudoktoreos.optimization.fleet import (
    FleetDevicePlan,
    FleetRequest,
    FleetSolution,
    optimize_fleet,
)
from akkudoktoreos.thermal.thermalmodels import (
    FiniteNumber,
    NonemptyText,
    NonnegativeRevision,
    TemperatureInterval,
    ThermalComfortBand,
    ThermalModel,
    ThermalPlan,
    ThermalRC2Parameters,
    ThermalState,
    UTCDatetime,
)

FloatArray = npt.NDArray[np.float64]
PositiveSeconds = Annotated[int, Field(strict=True, gt=0)]
ShadowStatus = Literal["optimal", "feasible", "infeasible", "unsupported", "limit", "error"]
_TOL = 1e-5


if TYPE_CHECKING:
    # The installed SciPy stubs omit documented HiGHS options forwarded by
    # SciPy's **unknown_options path. Extend the types without a runtime import
    # of these stub-only names or weakening the rest of the call's types.
    from scipy.optimize._linprog import _OptionsHighs
    from scipy.optimize._milp import _OptionsMILP
    from scipy.sparse import csc_matrix

    class _LinearHighsOptions(_OptionsHighs, total=False):
        small_matrix_value: float

    class _MixedHighsOptions(_OptionsMILP, total=False):
        small_matrix_value: float


class ThermalRecoverySettings(ThermalModel):
    """Opt-in common heating or passive cooldown prefix outside initial comfort.

    Recovery is a modeled comfort exception, not a guarantee of native device
    tracking. The prefix is fixed identically in the reference and candidate.
    """

    max_duration_seconds: PositiveSeconds = 14400
    allow_passive_cooldown: bool = Field(default=False, strict=True)
    max_heating_rate_kelvin_per_hour: FiniteNumber | None = Field(default=None, gt=0, le=20)

    @model_serializer(mode="wrap")
    def omit_unset_rate(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        """Keep serialized requests (and their fingerprints) unchanged without a rate."""
        data = handler(self)
        if data.get("max_heating_rate_kelvin_per_hour") is None:
            data.pop("max_heating_rate_kelvin_per_hour", None)
        return data

    @model_validator(mode="after")
    def check_quarter_hour_duration(self) -> Self:
        """Keep recovery on the same explicit quarter-hour control grid."""
        if self.max_duration_seconds % 900:
            raise ValueError("Recovery duration must be a whole number of 900-second intervals")
        return self


class ThermalRecoveryReport(ThermalModel):
    """Explicit comfort deviations and identical recovery for both schedules."""

    active: Literal[True] = True
    direction: Literal["heating", "passive_cooldown"] = "heating"
    completed: bool
    end_datetime: UTCDatetime
    heat_power_w: tuple[FiniteNumber, ...]
    room_temperature_c: tuple[FiniteNumber, ...]
    mass_temperature_c: tuple[FiniteNumber, ...]
    cold_minutes: Annotated[int, Field(strict=True, ge=0)]
    cold_degree_hours: FiniteNumber = Field(ge=0)
    maximum_cold_deficit_c: FiniteNumber = Field(ge=0)
    hot_minutes: Annotated[int, Field(strict=True, ge=0)] = 0
    hot_degree_hours: FiniteNumber = Field(default=0, ge=0)
    maximum_hot_excess_c: FiniteNumber = Field(default=0, ge=0)
    native_tracking_validated: Literal[False] = False
    power_input_kind: Literal["electrical", "thermal"] = "electrical"
    distribution_temperatures_c: dict[str, tuple[FiniteNumber, ...]] = Field(default_factory=dict)
    requested_heating_rate_kelvin_per_hour: FiniteNumber | None = None
    heating_rate_kelvin_per_hour: FiniteNumber | None = None
    heating_rate_raised: bool | None = None

    @model_serializer(mode="wrap")
    def omit_unset_rate_fields(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        """Keep reports unchanged when no recovery rate was requested."""
        data = handler(self)
        for key in (
            "requested_heating_rate_kelvin_per_hour",
            "heating_rate_kelvin_per_hour",
            "heating_rate_raised",
        ):
            if data.get(key) is None:
                data.pop(key, None)
        return data


class ThermalPlanningInputs(ThermalModel):
    """Common bounded schedule and electrical inputs, independent of source physics.

    ``fleet.load_w`` is explicitly the household residual excluding this heat
    source. EVs remain native FLEET devices, not part of that residual. Smoothing
    holds mean electrical heating power constant within UTC-aligned blocks;
    it never smooths an already verified room trajectory after optimization.
    """

    fleet: FleetRequest
    load_basis: Literal["residual_excluding_heat"]
    zone_id: NonemptyText
    heat_source_id: NonemptyText
    state: ThermalState
    candidate_pause_slots: tuple[bool, ...] = ()
    comfort_required: tuple[bool, ...] = ()
    comfort_bands: tuple[ThermalComfortBand, ...] = Field(min_length=1, max_length=288)
    issued_at: UTCDatetime
    revision: NonnegativeRevision
    basis_control_revision: NonnegativeRevision
    plan_ttl_seconds: PositiveSeconds = 1800
    measurement_max_age_seconds: PositiveSeconds = 180
    max_absolute_gap_eur: FiniteNumber = Field(default=0.005, ge=0)
    smoothing_seconds: PositiveSeconds | None = 1800
    recovery: ThermalRecoverySettings | None = None
    planning_time_limit_seconds: FiniteNumber = Field(default=60.0, gt=0, le=3600)
    # Seconds of the joint stage spent on an exact local improvement of the on/off pattern
    # (close short pauses / drop short runs, accepted only if the verified cost falls).
    # 0 = off = unchanged behaviour. Left out of the plan fingerprints so plan ids stay identical.
    pattern_polish_seconds: FiniteNumber = Field(default=0.0, ge=0, le=60)

    @model_validator(mode="after")
    def check_supported_shape(self) -> Self:
        """Require one aligned 15-minute horizon with a single explicit zone."""
        fleet = FleetRequest.model_validate(self.fleet.model_dump())
        n = len(fleet.load_w)
        if fleet.interval_seconds != 900 or n > 288:
            raise ValueError("Thermal shadow supports at most 72 hours in 900-second intervals")
        if fleet.start_datetime.microsecond or fleet.start_datetime.timestamp() % 900:
            raise ValueError("Thermal shadow start must be aligned to the UTC 900-second grid")
        if self.candidate_pause_slots and (
            len(self.candidate_pause_slots) != n
            or len(self.comfort_required) != n
            or any(
                pause and self.comfort_required[i]
                for i, pause in enumerate(self.candidate_pause_slots)
            )
        ):
            raise ValueError("Candidate pauses must align with declared absence slots")
        if self.comfort_required and len(self.comfort_required) != n:
            raise ValueError("Comfort-required mask must match horizon")
        if len(self.comfort_bands) != n:
            raise ValueError("Comfort length must match the fleet horizon")
        if self.smoothing_seconds is not None and self.smoothing_seconds % 900:
            raise ValueError("Smoothing must be a whole number of 900-second intervals")
        if self.heat_source_id in {device.device_id for device in fleet.devices}:
            raise ValueError("Heat source ID must be distinct from fleet storage device IDs")
        return self


class ThermalShadowRequest(ThermalPlanningInputs):
    """Legacy effective-electrical RC2 request, without a separate heat-pump COP.

    Existing payloads remain valid. Its electrical gain is never multiplied by
    the generic heat-pump module; that path uses a separate thermal building DTO.
    """

    model: ThermalRC2Parameters
    outdoor_temperature_c: tuple[FiniteNumber, ...] = Field(min_length=1, max_length=288)
    max_electric_power_w: FiniteNumber = Field(gt=0)
    min_electric_power_w: FiniteNumber = Field(default=0, ge=0)

    @model_validator(mode="after")
    def check_electrical_model_horizon(self) -> Self:
        """Validate the effective electrical model's explicit horizon and limits."""
        if len(self.outdoor_temperature_c) != len(self.fleet.load_w):
            raise ValueError("Thermal forecast length must match the fleet horizon")
        if self.min_electric_power_w > self.max_electric_power_w:
            raise ValueError("Minimum electric power exceeds maximum electric power")
        return self


class ThermalSolveAttempt(ThermalModel):
    """One completed backend call, before any rescue or coordinate refinement."""

    kind: Literal[
        "milp", "feasibility_milp", "lp", "original_lp", "fixed_pattern_lp", "physical_cleanup_lp"
    ]
    representation: Literal["sparse", "condensed"] = "sparse"
    status: int
    presolve: bool
    time_limit_seconds: FiniteNumber = Field(gt=0)
    elapsed_seconds: FiniteNumber = Field(ge=0)
    message: str


class ThermalPhysicalCheck(ThermalModel):
    """Retained physical error metrics, including failures preceding a repair."""

    phase: str
    metrics: dict[str, FiniteNumber]


class ThermalSolveDiagnostics(ThermalModel):
    """Measured stage work; retries and cleanup share the same stage/global budget."""

    name: str
    status: str
    elapsed_seconds: FiniteNumber = Field(ge=0)
    solver_calls: int = Field(ge=0)
    presolve_retries: int = Field(ge=0)
    original_refinements: int = Field(default=0, ge=0)
    feasibility_rescues: int = Field(default=0, ge=0)
    fixed_pattern_rescues: int = Field(default=0, ge=0)
    fixed_pattern_hint_rejection: str = ""
    representation_switches: int = Field(default=0, ge=0)
    representation_switch_reason: str = ""
    physical_refinements: int = Field(default=0, ge=0)
    physical_checks: tuple[ThermalPhysicalCheck, ...] = ()
    initial_allocation_seconds: FiniteNumber = Field(default=0, ge=0)
    final_allocation_seconds: FiniteNumber = Field(default=0, ge=0)
    adaptive_extensions: int = Field(default=0, ge=0)
    downstream_reserve_seconds: FiniteNumber = Field(default=0, ge=0)
    condensed_state_variables: int = Field(default=0, ge=0)
    condensed_dynamics_rows: int = Field(default=0, ge=0)
    attempts: tuple[ThermalSolveAttempt, ...] = ()
    objective: FiniteNumber | None = None
    lower_bound: FiniteNumber | None = None
    violation: FiniteNumber | None = None


class ThermalShadowSolution(ThermalModel):
    """Separated reference/candidate results, never an executable EMS plan.

    Qualified savings are conditional model comparisons, not measured bills.
    Candidate storage endpoints match the native reference budget. Cash savings
    remain unset unless these endpoints and both solver gap budgets qualify.
    """

    status: ShadowStatus
    reason: str
    shadow_only: Literal[True] = True
    execution_allowed: Literal[False] = False
    comparison_qualified: bool = False
    reference: FleetSolution | None = None
    candidate: FleetSolution | None = None
    reference_heat_power_w: tuple[FiniteNumber, ...] = ()
    candidate_heat_power_w: tuple[FiniteNumber, ...] = ()
    reference_source_electric_power_w: tuple[FiniteNumber, ...] = ()
    candidate_source_electric_power_w: tuple[FiniteNumber, ...] = ()
    reference_room_temperature_c: tuple[FiniteNumber, ...] = ()
    candidate_room_temperature_c: tuple[FiniteNumber, ...] = ()
    reference_mass_temperature_c: tuple[FiniteNumber, ...] = ()
    candidate_mass_temperature_c: tuple[FiniteNumber, ...] = ()
    reference_distribution_temperatures_c: dict[str, tuple[FiniteNumber, ...]] = Field(
        default_factory=dict
    )
    candidate_distribution_temperatures_c: dict[str, tuple[FiniteNumber, ...]] = Field(
        default_factory=dict
    )
    plan: ThermalPlan | None = None
    savings_cash_eur: FiniteNumber | None = None
    savings_objective_eur: FiniteNumber | None = None
    objective_lower_bound_eur: FiniteNumber | None = None
    absolute_gap_eur: FiniteNumber | None = None
    reference_absolute_gap_eur: FiniteNumber | None = None
    checks: dict[str, bool | float | str] = Field(default_factory=dict)
    recovery: ThermalRecoveryReport | None = None
    stage_diagnostics: tuple[ThermalSolveDiagnostics, ...] = ()


class _NoSolution(Exception):
    def __init__(
        self,
        status: ShadowStatus,
        message: str,
        *,
        numerical: bool = False,
        fixed_integers: FloatArray | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.numerical = numerical
        self.fixed_integers = fixed_integers


@dataclass
class _PlanningBudget:
    """One cooperative wallclock deadline, including preparation and verification.

    Python/SciPy setup cannot be hard-killed here. Any result that overruns is
    rejected; no late plan is published. HTTP parsing/native-data resolution
    before the public pure function call is outside this budget.
    """

    seconds: float
    started: float = field(default_factory=lambda: time.monotonic())
    stage: str = "preparation"
    failed_stage: str | None = None
    reference_energy_witness_reused: bool = False
    reference_tracking_feasibility_rescue: bool = False
    reference_tracking_fixed_pattern_rescue: bool = False
    candidate_numerical_reference_fallback: bool = False
    candidate_worse_incumbent_replaced: bool = False
    candidate_cost_seed_used: bool = False
    cleanup_original_refinement_used: bool = False
    cleanup_physical_refinement_used: bool = False
    candidate_plan_origin: str = "joint_optimization"
    pattern_polish_enabled: bool = False
    pattern_polish_moves: int = 0
    pattern_polish_evaluations: int = 0
    pattern_polish_gain_eur: float = 0.0
    pattern_polish_start_eur: float = 0.0
    pattern_polish_seconds_used: float = 0.0
    pattern_polish_extra_seconds: float = 0.0
    native_fleet_reference_retry_used: bool = False
    native_fleet_reference_idle_fallback: bool = False
    native_fleet_reference_start_used: bool = False
    verification_reserve_seconds: float = 0.0
    diagnostics: list[ThermalSolveDiagnostics] = field(default_factory=list)
    physical_metrics: dict[str, float] = field(default_factory=dict)

    def remaining(self, stage: str | None = None) -> float:
        if stage is not None:
            self.stage = stage
        left = self.seconds - (time.monotonic() - self.started)
        if left <= 0:
            self.failed_stage = self.failed_stage or self.stage
            raise _NoSolution("limit", f"Planning deadline exceeded during {self.failed_stage}")
        return left

    def allowance(self, cap: float, stages_left: int) -> float:
        """Reserve equal shares for all remaining stages; unused time carries on."""
        left = self.remaining()
        if stages_left == 1:
            # Last optimization/cleanup must leave paid time for complete
            # original-model witness and minute/terminal/result verification.
            self.verification_reserve_seconds = min(1.0, left * 0.1)
            left -= self.verification_reserve_seconds
        return min(cap, left / stages_left)


_SolutionT = TypeVar("_SolutionT", bound=ThermalShadowSolution)


_FLEET_RETRY_SECONDS = 5.0
_FLEET_IDLE_SECONDS = 2.0


def _warm_start_file(reference_request: FleetRequest) -> Path:
    """File holding the last verified fleet reference of this device set and raster.

    Runs in the isolated solver process (see ``thermalworker``) use a throw-away
    data directory, so neither process memory nor EOS_DATA_DIR survives between
    those plans; the native planning path runs in-process. The file keeps the
    reference across plans in both cases. ``EOS_THERMAL_WARM_START_DIR``
    overrides the default user cache.
    """
    folder = os.environ.get("EOS_THERMAL_WARM_START_DIR")
    base = (
        Path(folder)
        if folder
        else Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
        / "akkudoktoreos"
        / "thermal-warm-start"
    )
    devices = sorted(d.device_id for d in reference_request.devices)
    key = repr((devices, reference_request.interval_seconds))
    return base / f"fleet-reference-{hashlib.sha256(key.encode()).hexdigest()[:20]}.json"


def _load_warm_start(reference_request: FleetRequest) -> FleetSolution | None:
    """Previous fleet reference as MILP start; a missing or damaged file means no start."""
    try:
        solution = FleetSolution.model_validate_json(
            _warm_start_file(reference_request).read_text(encoding="utf-8")
        )
    except Exception:  # noqa: BLE001 - the start is optional, never a failure
        return None
    return solution if solution.status in ("optimal", "feasible") else None


def _store_warm_start(reference_request: FleetRequest, solution: FleetSolution) -> None:
    """Atomically keep a verified, optimized fleet reference for the next plan."""
    if solution.status not in ("optimal", "feasible") or solution.objective_eur is None:
        return
    try:
        path = _warm_start_file(reference_request)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        handle, name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                stream.write(solution.model_copy(update={"parameters": None}).model_dump_json())
            os.replace(name, path)
        except BaseException:
            Path(name).unlink(missing_ok=True)
            raise
    except Exception:  # noqa: BLE001 - failing to keep a start must not fail the plan
        return


def _native_fleet_reference_fallback(
    reference_request: FleetRequest, reference: FleetSolution, budget: _PlanningBudget
) -> FleetSolution:
    """Recover a verified electrical reference when HiGHS has no incumbent at the cap.

    The storage MILP (gap 0) always runs to its cap; whether an incumbent exists
    after it depends on the search path. First retry the IDENTICAL model with
    presolve disabled, a different search path that is still optimized. As a
    last resort fix all devices idle: same model with tightened bounds, so the
    existing feasibility verification in optimize_fleet applies. That schedule
    is deliberately unoptimized and disqualifies the economic comparison.
    Infeasible/error results are returned unchanged (no fallback hides them).
    """
    if reference.status != "limit" or reference.objective_eur is not None:
        return reference
    started = time.monotonic()
    retry = optimize_fleet(
        reference_request.model_copy(
            update={
                "solver_time_limit_seconds": min(
                    _FLEET_RETRY_SECONDS,
                    reference_request.solver_time_limit_seconds,
                    budget.remaining("native_fleet_reference_retry") / 4,
                )
            }
        ),
        highs_options={"presolve": False},
    )
    budget.diagnostics.append(
        ThermalSolveDiagnostics(
            name="native_fleet_reference_retry",
            status=retry.status,
            elapsed_seconds=time.monotonic() - started,
            solver_calls=1,
            presolve_retries=1,
            objective=retry.objective_eur,
        )
    )
    if retry.status in ("optimal", "feasible") and retry.objective_eur is not None:
        budget.native_fleet_reference_retry_used = True
        return retry
    started = time.monotonic()
    idle = optimize_fleet(
        reference_request.model_copy(
            update={
                "solver_time_limit_seconds": min(
                    _FLEET_IDLE_SECONDS,
                    budget.remaining("native_fleet_reference_idle_fallback") / 4,
                )
            }
        ),
        idle_devices=True,
    )
    budget.diagnostics.append(
        ThermalSolveDiagnostics(
            name="native_fleet_reference_idle_fallback",
            status=idle.status,
            elapsed_seconds=time.monotonic() - started,
            solver_calls=1,
            presolve_retries=0,
            objective=idle.objective_eur,
        )
    )
    if idle.status == "feasible" and idle.objective_eur is not None:
        budget.native_fleet_reference_idle_fallback = True
        return idle
    return reference


def _finish_planning(solution: _SolutionT, budget: _PlanningBudget) -> _SolutionT:
    """Fail closed after all preparation/solve/check work, with failure diagnostics too."""
    elapsed = time.monotonic() - budget.started
    expired = elapsed >= budget.seconds
    if expired:
        budget.failed_stage = budget.failed_stage or budget.stage
        solution = type(solution)(
            status="limit", reason=f"Planning deadline exceeded during {budget.failed_stage}"
        )
    if solution.plan is not None and (
        budget.reference_energy_witness_reused
        or budget.reference_tracking_feasibility_rescue
        or budget.reference_tracking_fixed_pattern_rescue
        or budget.candidate_numerical_reference_fallback
        or budget.cleanup_original_refinement_used
        or budget.cleanup_physical_refinement_used
        or budget.native_fleet_reference_idle_fallback
        or budget.candidate_plan_origin.endswith("_fallback")
    ):
        solution = solution.model_copy(
            update={
                "status": "feasible",
                "comparison_qualified": False,
                "savings_cash_eur": None,
                "savings_objective_eur": None,
                "reason": "Verified feasible numerical/rescue result; no optimized economic comparison",
            }
        )
    if solution.plan is not None and (
        budget.reference_tracking_fixed_pattern_rescue or budget.cleanup_physical_refinement_used
    ):
        # A fixed source branch provides a feasible reference, never a global
        # tracking/economic certificate. Keep genuine backend bounds only in
        # their named stage diagnostics, not in comparison certificate fields.
        solution = solution.model_copy(
            update={
                "objective_lower_bound_eur": None,
                "absolute_gap_eur": None,
                "reference_absolute_gap_eur": None,
            }
        )
    return solution.model_copy(
        update={
            "stage_diagnostics": tuple(budget.diagnostics),
            "checks": {
                **solution.checks,
                **budget.physical_metrics,
                "planning_elapsed_seconds": elapsed,
                "planning_budget_seconds": budget.seconds,
                "planning_deadline_exceeded": expired,
                "planning_stage": budget.stage,
                "planning_failed_stage": budget.failed_stage or "",
                "reference_energy_witness_reused": budget.reference_energy_witness_reused,
                "reference_tracking_feasibility_rescue": budget.reference_tracking_feasibility_rescue,
                "reference_tracking_fixed_pattern_rescue": budget.reference_tracking_fixed_pattern_rescue,
                "candidate_numerical_reference_fallback": budget.candidate_numerical_reference_fallback,
                "candidate_worse_incumbent_replaced": budget.candidate_worse_incumbent_replaced,
                "candidate_cost_seed_used": budget.candidate_cost_seed_used,
                "cleanup_original_refinement_used": budget.cleanup_original_refinement_used,
                "cleanup_physical_refinement_used": budget.cleanup_physical_refinement_used,
                "planning_verification_reserve_seconds": budget.verification_reserve_seconds,
                "native_fleet_reference_retry_used": budget.native_fleet_reference_retry_used,
                "native_fleet_reference_idle_fallback": budget.native_fleet_reference_idle_fallback,
                "native_fleet_reference_start_used": budget.native_fleet_reference_start_used,
                "candidate_plan_origin": budget.candidate_plan_origin
                if solution.candidate is not None
                else "none",
                **(
                    {
                        "pattern_polish_moves": float(budget.pattern_polish_moves),
                        "pattern_polish_evaluations": float(budget.pattern_polish_evaluations),
                        "pattern_polish_gain_eur": budget.pattern_polish_gain_eur,
                        "pattern_polish_start_objective_eur": budget.pattern_polish_start_eur,
                        "pattern_polish_seconds_used": budget.pattern_polish_seconds_used,
                    }
                    if budget.pattern_polish_enabled
                    else {}
                ),
            },
        }
    )


@dataclass
class _Result:
    x: FloatArray
    objective: float
    lower_bound: float | None
    gap: float | None
    status: Literal["optimal", "feasible"]
    violation: float


@dataclass(frozen=True)
class _ThermalStateSubstitution:
    """Explicit known-state/dynamics metadata, never inferred from arbitrary rows."""

    initial: tuple[int, ...]
    inputs: tuple[int, ...]
    recurrences: tuple[tuple[int, int], ...]  # (successor variable, original row)
    enabled_stages: tuple[str, ...] | None = None


@dataclass(frozen=True)
class _ThermalReduction:
    projection: "csc_matrix"
    offset: FloatArray
    free_columns: npt.NDArray[np.int64]
    state_columns: npt.NDArray[np.int64]
    kept_rows: npt.NDArray[np.int64]


@dataclass(frozen=True)
class _SourceHorizon:
    """Prepared source coupling, independent of the public legacy request."""

    outdoor_c: tuple[float, ...]
    power_gain_scales: tuple[float, ...]
    min_power_w: tuple[float, ...]
    max_power_w: tuple[float, ...]
    steps: tuple[tuple[FloatArray, FloatArray, FloatArray], ...]
    fingerprint: str
    model_kind: str
    fixed_heat_prefix_w: tuple[float, ...] = ()
    standby_power_w: float = 0.0
    running_auxiliary_power_w: float = 0.0
    startup_energy_wh: float = 0.0
    startup_warmup_s: float = 0.0
    initial_running: bool = False
    total_electric_power_cap_w: float | None = None
    operating_points: tuple[tuple[tuple[float, float], ...], ...] = ()
    additional_initial_temperatures_c: tuple[float, ...] = ()
    additional_state_names: tuple[str, ...] = ()
    additional_temperature_bounds_c: tuple[tuple[float, float], ...] = ()
    slot_steps: tuple[tuple[tuple[FloatArray, FloatArray, FloatArray], ...], ...] = ()
    source_temperature_state_index: int | None = None
    source_flow_conductance_w_per_k: tuple[float, ...] = ()
    delivery_temperature_c: tuple[float, ...] = ()

    @property
    def startup_modelled(self) -> bool:
        """True when any startup loss (fixed energy and/or warm-up time) is modelled."""
        return self.startup_energy_wh > 0 or self.startup_warmup_s > 0


def _steps_at(
    source: _SourceHorizon, index: int
) -> tuple[tuple[FloatArray, FloatArray, FloatArray], ...]:
    """Return the selected exogenous fan/distribution dynamics for one slot."""
    return source.slot_steps[index] if source.slot_steps else source.steps


def _initial_temperatures(request: ThermalPlanningInputs, source: _SourceHorizon) -> FloatArray:
    """Keep explicit distribution stores alongside room and building mass."""
    return np.array(
        [
            request.state.room_temperature_c,
            request.state.mass_temperature_c,
            *source.additional_initial_temperatures_c,
        ],
        dtype=float,
    )


def _distribution_trajectories(
    source: _SourceHorizon, states: FloatArray
) -> dict[str, tuple[float, ...]]:
    return {
        name: tuple(float(value) for value in states[:, j + 2])
        for j, name in enumerate(source.additional_state_names)
    }


def _base_electric_at_input(source: _SourceHorizon, index: int, power: float) -> float:
    """Evaluate exactly the adjacent Q/P segment used by the mathematical plan."""
    if not source.operating_points:
        return power
    if power <= _TOL:
        return 0.0
    points = source.operating_points[index]
    if power < points[0][0] - _TOL or power > points[-1][0] + _TOL:
        raise _NoSolution("error", "Source input is outside its operating-point segment support")
    return float(np.interp(power, [q for q, _ in points], [p for _, p in points]))


def _input_at_electric_cap(source: _SourceHorizon, index: int, cap: float) -> float:
    """Return the largest thermal input within a monotone electrical segment cap."""
    if not source.operating_points:
        return max(0.0, cap)
    previous: tuple[float, float] | None = None
    allowed = 0.0
    for q, power in source.operating_points[index]:
        if power <= cap:
            allowed = q
        elif previous is not None and previous[1] <= cap:
            q0, p0 = previous
            allowed = q0 + (q - q0) * (cap - p0) / (power - p0)
            break
        else:
            break
        previous = q, power
    return allowed


class _Program:
    def __init__(self, budget: _PlanningBudget | None = None, stage: str = "thermal") -> None:
        self.lower: list[float] = []
        self.upper: list[float] = []
        self.integer: list[int] = []
        self.rows: list[dict[int, float]] = []
        self.row_lower: list[float] = []
        self.row_upper: list[float] = []
        self.budget = budget
        self.stage = stage
        self.feasible_witness: FloatArray | None = None
        self.witness_values: dict[int, float] = {}
        self.adaptive_stage_cap_seconds: float | None = None
        self.thermal_state_substitution: _ThermalStateSubstitution | None = None
        self.integer_hint_factory: Callable[[], FloatArray | None] | None = None
        self.reference_witness_validator: Callable[[FloatArray], bool] | None = None
        self.initial_representation: Literal["sparse", "condensed"] = "sparse"
        self.physical_validator: Callable[[FloatArray], Mapping[str, float]] | None = None
        self.physical_check_history: list[ThermalPhysicalCheck] = []
        self.on_slots: list[int | None] = []

    def physical_violation(self, values: FloatArray, phase: str = "candidate") -> float:
        """Check reconstructed physical outputs inside the same solver deadline."""
        if self.physical_validator is None:
            return 0.0
        metrics = dict(self.physical_validator(values))
        if any(not np.isfinite(value) or value < 0 for value in metrics.values()):
            raise _NoSolution("error", "Physical validator returned invalid error metrics")
        self.physical_check_history.append(ThermalPhysicalCheck(phase=phase, metrics=metrics))
        maximum = max(metrics.values(), default=0.0)
        if self.budget is not None:
            prefix = "reference_witness" if phase == "reference_witness" else "candidate"
            for name, value in metrics.items():
                self.budget.physical_metrics[f"{prefix}_{name}"] = value
                if maximum > _TOL:
                    self.budget.physical_metrics.setdefault(f"failed_{prefix}_{name}", value)
        return maximum

    def _thermal_reduction(self) -> _ThermalReduction | None:
        """Substitute only registered thermal recurrences, preserving their bounds.

        Read the SAME assembled original coefficients/RHS, rather than a second
        independently rounded state model. Every other row remains, and every
        eliminated variable bound becomes a linear row in the backend model.
        """
        from scipy.sparse import coo_matrix

        metadata = self.thermal_state_substitution
        if metadata is None or (
            metadata.enabled_stages is not None and self.stage not in metadata.enabled_stages
        ):
            return None
        states = metadata.initial + tuple(pivot for pivot, _ in metadata.recurrences)
        if len(set(states)) != len(states) or any(self.integer[i] for i in states):
            raise _NoSolution("error", "Invalid registered thermal state variables")
        state_set, inputs = set(states), set(metadata.inputs)
        free = np.array([i for i in range(len(self.lower)) if i not in state_set], dtype=np.int64)
        reverse = {int(column): i for i, column in enumerate(free)}
        offset = np.zeros(len(self.lower))
        expressions: dict[int, dict[int, float]] = {}
        for column in metadata.initial:
            if self.lower[column] != self.upper[column] or not np.isfinite(self.lower[column]):
                raise _NoSolution("error", "Thermal substitution requires known initial states")
            offset[column] = self.lower[column]
            expressions[column] = {}
        for pivot, row_id in metadata.recurrences:
            row = self.rows[row_id]
            if (
                self.row_lower[row_id] != self.row_upper[row_id]
                or not np.isfinite(self.row_lower[row_id])
                or row.get(pivot) != 1.0
            ):
                raise _NoSolution("error", "Invalid registered thermal recurrence")
            constant = self.row_lower[row_id]
            terms: dict[int, float] = {}
            for column, coefficient in row.items():
                if column == pivot or coefficient == 0:
                    continue
                if column in expressions:
                    constant -= coefficient * offset[column]
                    for original, value in expressions[column].items():
                        terms[original] = terms.get(original, 0.0) - coefficient * value
                elif column in inputs and column not in state_set:
                    terms[column] = terms.get(column, 0.0) - coefficient
                else:
                    raise _NoSolution("error", "Thermal recurrence has unregistered dependencies")
            offset[pivot] = constant
            expressions[pivot] = terms
        rr, cc, vv = list(free), list(range(len(free))), [1.0] * len(free)
        for column in states:
            for original, value in expressions[column].items():
                if value:
                    rr.append(column)
                    cc.append(reverse[original])
                    vv.append(value)
        projection = coo_matrix(
            (np.asarray(vv), (np.asarray(rr, dtype=int), np.asarray(cc, dtype=int))),
            shape=(len(self.lower), len(free)),
        ).tocsc()
        removed = {row_id for _, row_id in metadata.recurrences}
        if len(removed) != len(metadata.recurrences) or not np.isfinite(projection.data).all():
            raise _NoSolution("error", "Invalid thermal substitution matrix")
        return _ThermalReduction(
            projection=projection,
            offset=offset,
            free_columns=free,
            state_columns=np.array(states, dtype=np.int64),
            kept_rows=np.array(
                [i for i in range(len(self.rows)) if i not in removed], dtype=np.int64
            ),
        )

    def set_witness(self, ids: Sequence[int], values: Sequence[float] | FloatArray) -> None:
        """Fill explicit reference decisions, without asking a solver to trust them."""
        self.witness_values.update(
            (index, float(value)) for index, value in zip(ids, values, strict=True)
        )

    def witness_violation(self, values: FloatArray) -> float:
        """Check every original row, bound and integer of a complete candidate."""
        if values.shape != (len(self.lower),) or not np.isfinite(values).all():
            return np.inf
        violation = max(
            float(np.max(np.array(self.lower) - values, initial=0)),
            float(np.max(values - np.array(self.upper), initial=0)),
        )
        for row, lo, hi in zip(self.rows, self.row_lower, self.row_upper):
            value = sum(coefficient * values[index] for index, coefficient in row.items())
            violation = max(violation, lo - value, value - hi)
        integer = np.array(self.integer, dtype=bool)
        if integer.any():
            violation = max(
                violation, float(np.max(abs(values[integer] - np.round(values[integer]))))
            )
        return max(0.0, violation)

    def verified_fallback(self, objective: FloatArray, values: FloatArray) -> _Result:
        """Return only a fully checked feasible witness, with no optimality bound."""
        started = time.monotonic()
        if self.budget is not None:
            self.budget.remaining(self.stage)
        violation = self.witness_violation(values)
        if violation > _TOL:
            raise _NoSolution("error", "Reference witness failed original-model verification")
        physical_start = len(self.physical_check_history)
        if self.physical_violation(values, "reference_fallback") > _TOL:
            raise _NoSolution("error", "Reference witness failed physical verification")
        objective_value = float(objective @ values)
        if not np.isfinite(objective_value):
            raise _NoSolution("error", "Reference witness objective is nonfinite")
        if self.budget is not None:
            self.budget.remaining(self.stage)
            if self.budget.failed_stage == self.stage:
                # Keep the unsuccessful attempt in diagnostics, but do not
                # label a subsequently verified result as still failed.
                self.budget.failed_stage = None
            self.budget.diagnostics.append(
                ThermalSolveDiagnostics(
                    name=self.stage + "_witness_fallback",
                    status="feasible_witness",
                    elapsed_seconds=time.monotonic() - started,
                    solver_calls=0,
                    presolve_retries=0,
                    objective=objective_value,
                    violation=violation,
                    physical_checks=tuple(self.physical_check_history[physical_start:]),
                )
            )
        return _Result(values.copy(), objective_value, None, None, "feasible", violation)

    def var(self, n: int, low: float = 0, high: float = np.inf, binary: bool = False) -> list[int]:
        ids = list(range(len(self.lower), len(self.lower) + n))
        self.lower.extend([low] * n)
        self.upper.extend([high] * n)
        self.integer.extend([int(binary)] * n)
        return ids

    def row(self, terms: Mapping[int, float], low: float = -np.inf, high: float = np.inf) -> None:
        self.rows.append(dict(terms))
        self.row_lower.append(low)
        self.row_upper.append(high)

    def eq(self, terms: Mapping[int, float], value: float) -> None:
        self.row(terms, value, value)

    def solve(self, objective: FloatArray, seconds: float) -> _Result:
        from scipy.optimize import (
            Bounds,
            LinearConstraint,
            OptimizeResult,
            OptimizeWarning,
            linprog,
            milp,
        )
        from scipy.sparse import coo_matrix, diags, vstack

        started = time.monotonic()
        deadline = started + seconds
        calls, retries = 0, 0
        original_refinements, feasibility_rescues = 0, 0
        fixed_pattern_rescues, representation_switches = 0, 0
        fixed_pattern_hint_rejection = ""
        representation_switch_reason = ""
        fixed_pattern_used = False
        physical_refinements = 0
        physical_history_start = len(self.physical_check_history)
        repair_integers: FloatArray | None = None
        adaptive_extensions = 0
        downstream_reserve = 0.0
        condensed_variables, condensed_rows = 0, 0
        attempts: list[ThermalSolveAttempt] = []
        status = "error"
        final_objective: float | None = None
        lower_bound: float | None = None
        final_violation: float | None = None

        def remaining() -> float:
            left = deadline - time.monotonic()
            if self.budget is not None:
                left = min(left, self.budget.remaining(self.stage))
            if left <= 0:
                raise _NoSolution("limit", f"Stage deadline exceeded during {self.stage}")
            return left

        def extend_unwitnessed_tracking() -> bool:
            """Borrow unused shares once, keeping the absolute native stage cap.

            Without a first thermal witness no later stage can run. Spend part
            of that otherwise unusable budget, while retaining a quarter of
            the total (or half of the current remainder) for those stages and
            validation. Neither the global clock nor the stage cap is reset.
            """
            nonlocal deadline, adaptive_extensions, downstream_reserve
            if (
                self.stage != "reference_tracking"
                or self.budget is None
                or self.adaptive_stage_cap_seconds is None
                or adaptive_extensions
            ):
                return False
            now = time.monotonic()
            global_left = self.budget.remaining(self.stage)
            reserve = min(self.budget.seconds * 0.25, global_left * 0.5)
            extended = min(
                started + self.adaptive_stage_cap_seconds,
                now + global_left - reserve,
            )
            if extended <= deadline:
                return False
            deadline = extended
            downstream_reserve = reserve
            adaptive_extensions = 1
            return True

        try:
            remaining()
            store = getattr(self, "_matrix_store", None)  # only set by the pattern polish
            if store is not None and store.get("rows") == len(self.rows):
                matrix = store["matrix"]
            else:
                rr, cc, vv = [], [], []
                for i, terms in enumerate(self.rows):
                    for j, value in terms.items():
                        if value:
                            rr.append(i)
                            cc.append(j)
                            vv.append(value)
                matrix = coo_matrix(
                    (np.array(vv), (np.array(rr, dtype=int), np.array(cc, dtype=int))),
                    shape=(len(self.rows), len(self.lower)),
                ).tocsc()
                if store is not None:
                    store["rows"], store["matrix"] = len(self.rows), matrix
            low, high = np.array(self.row_lower, dtype=float), np.array(self.row_upper, dtype=float)
            lower, upper = np.array(self.lower, dtype=float), np.array(self.upper, dtype=float)
            integer = np.array(self.integer, dtype=bool)

            def verified_reference_available() -> bool:
                if len(self.witness_values) != len(self.lower):
                    return False
                witness = np.array([self.witness_values[i] for i in range(len(self.lower))])
                return (
                    self.witness_violation(witness) <= _TOL
                    and self.physical_violation(witness, "reference_witness") <= _TOL
                )

            metadata = self.thermal_state_substitution
            can_switch = (
                self.stage == "joint_candidate"
                and metadata is not None
                and (metadata.enabled_stages is None or self.stage in metadata.enabled_stages)
            )
            use_condensed = self.initial_representation == "condensed"
            while True:
                try:
                    reduction = self._thermal_reduction() if use_condensed else None
                    backend_matrix, backend_low, backend_high = matrix, low, high
                    backend_lower, backend_upper, backend_objective = lower, upper, objective
                    backend_integer = np.array(self.integer)
                    objective_constant = 0.0
                    if reduction is not None:
                        projection, offset = reduction.projection, reduction.offset
                        retained = matrix[reduction.kept_rows]
                        shift = retained @ offset
                        backend_matrix = vstack(
                            [retained @ projection, projection[reduction.state_columns]]
                        ).tocsc()
                        backend_low = np.r_[
                            low[reduction.kept_rows] - shift,
                            lower[reduction.state_columns] - offset[reduction.state_columns],
                        ]
                        backend_high = np.r_[
                            high[reduction.kept_rows] - shift,
                            upper[reduction.state_columns] - offset[reduction.state_columns],
                        ]
                        backend_lower, backend_upper = (
                            lower[reduction.free_columns],
                            upper[reduction.free_columns],
                        )
                        backend_integer = backend_integer[reduction.free_columns]
                        backend_objective = np.asarray(projection.T @ objective)
                        objective_constant = float(objective @ offset)
                        condensed_variables = len(reduction.state_columns)
                        condensed_rows = len(self.rows) - len(reduction.kept_rows)
                        empty = np.asarray(abs(backend_matrix).max(axis=1).toarray()).ravel() == 0
                        if np.any(backend_low[empty] > _TOL) or np.any(backend_high[empty] < -_TOL):
                            raise _NoSolution(
                                "infeasible",
                                "Registered thermal state contradicts a constant bound",
                            )
                        # Constant rows remain, including roundoff-sized residuals.
                        # No RHS snapping or dropping of original state bounds.
                    # Pure change of numerical coordinates: x_original = scale*y.
                    # Keep W/Wh, objective units and the ORIGINAL matrix for independent
                    # verification; only the backend sees kW/kWh-sized power variables.
                    size = np.maximum(
                        np.where(np.isfinite(backend_lower), np.abs(backend_lower), 0),
                        np.where(np.isfinite(backend_upper), np.abs(backend_upper), 0),
                    )
                    variable_scale = np.where(
                        ~backend_integer.astype(bool) & (size >= 1000), 1000.0, 1.0
                    )
                    numerical = backend_matrix @ diags(variable_scale)
                    row_scale = np.maximum(
                        1.0, np.asarray(abs(numerical).max(axis=1).toarray()).ravel()
                    )
                    numerical = diags(1 / row_scale) @ numerical
                    numerical_low, numerical_high = (
                        backend_low / row_scale,
                        backend_high / row_scale,
                    )
                    numerical_objective = backend_objective * variable_scale
                    eq = np.isfinite(backend_low) & (backend_low == backend_high)
                    up, down = np.isfinite(backend_high) & ~eq, np.isfinite(backend_low) & ~eq
                    original_eq = np.isfinite(low) & (low == high)
                    original_up = np.isfinite(high) & ~original_eq
                    original_down = np.isfinite(low) & ~original_eq
                    ub_matrix, ub_rhs = (
                        vstack([numerical[up], -numerical[down]]),
                        np.r_[numerical_high[up], -numerical_low[down]],
                    )

                    def violation(x: FloatArray) -> float:
                        if x.shape != lower.shape or not np.isfinite(x).all():
                            return np.inf
                        ax = matrix @ x
                        error = max(
                            float(np.max(np.maximum(lower - x, 0), initial=0)),
                            float(np.max(np.maximum(x - upper, 0), initial=0)),
                            float(np.max(np.maximum(low - ax, 0), initial=0)),
                            float(np.max(np.maximum(ax - high, 0), initial=0)),
                        )
                        if integer.any():
                            error = max(
                                error, float(np.max(np.abs(x[integer] - np.round(x[integer]))))
                            )
                        return error

                    def linear_solve(
                        lo: FloatArray,
                        hi: FloatArray,
                        *,
                        presolve: bool,
                        original_model: bool = False,
                        time_cap: float | None = None,
                        fixed_pattern: bool = False,
                        physical_cleanup: bool = False,
                    ) -> OptimizeResult:
                        nonlocal calls
                        limit = remaining()
                        if time_cap is not None:
                            limit = min(limit, time_cap)
                        calls += 1
                        # SciPy1.17 forwards this documented HiGHS option verbatim.
                        # Suppress ONLY its exact forwarding notice; other option,
                        # numerical or backend warnings must remain visible.
                        notice = "Unrecognized options detected: {'small_matrix_value': 1e-12}. These will be passed to HiGHS verbatim."
                        options: _LinearHighsOptions = {
                            "time_limit": limit,
                            "presolve": presolve,
                            "primal_feasibility_tolerance": 1e-10 if original_model else 1e-9,
                            "dual_feasibility_tolerance": 1e-10 if original_model else 1e-9,
                            "small_matrix_value": 1e-12,
                        }
                        attempt_started = time.monotonic()
                        with warnings.catch_warnings():
                            warnings.filterwarnings(
                                "ignore",
                                message="^" + re.escape(notice) + "$",
                                category=OptimizeWarning,
                            )
                            result = linprog(
                                objective if original_model else numerical_objective,
                                A_ub=vstack([matrix[original_up], -matrix[original_down]])
                                if original_model
                                else ub_matrix,
                                b_ub=np.r_[high[original_up], -low[original_down]]
                                if original_model
                                else ub_rhs,
                                A_eq=matrix[original_eq] if original_model else numerical[eq],
                                b_eq=low[original_eq] if original_model else numerical_low[eq],
                                bounds=list(zip(lo, hi))
                                if original_model
                                else list(
                                    zip(
                                        (lo if reduction is None else lo[reduction.free_columns])
                                        / variable_scale,
                                        (hi if reduction is None else hi[reduction.free_columns])
                                        / variable_scale,
                                    )
                                ),
                                method="highs",
                                options=options,
                            )
                        attempts.append(
                            ThermalSolveAttempt(
                                kind="physical_cleanup_lp"
                                if physical_cleanup
                                else (
                                    "fixed_pattern_lp"
                                    if fixed_pattern
                                    else ("original_lp" if original_model else "lp")
                                ),
                                representation="condensed"
                                if reduction is not None and not original_model
                                else "sparse",
                                status=int(result.status),
                                presolve=presolve,
                                time_limit_seconds=limit,
                                elapsed_seconds=time.monotonic() - attempt_started,
                                message=str(getattr(result, "message", "")),
                            )
                        )
                        if result.x is not None:
                            result.x = np.asarray(result.x, dtype=float) * (
                                1 if original_model else variable_scale
                            )
                            if reduction is not None and not original_model:
                                result.x = reduction.projection @ result.x + reduction.offset
                        value = getattr(result, "fun", None)
                        if not original_model and value is not None:
                            result.fun = value + objective_constant
                        return result

                    def mixed_solve(
                        *, presolve: bool, feasibility_only: bool = False
                    ) -> OptimizeResult:
                        nonlocal calls
                        # A tracking search reserves room for a same-model feasibility
                        # rescue and accurate cleanup, all inside this stage deadline.
                        fraction = (
                            0.85
                            if feasibility_only
                            else (
                                0.7
                                if self.integer_hint_factory is not None
                                or self.stage == "joint_cost_seed"
                                else 0.9
                            )
                        )
                        limit = remaining() * fraction
                        calls += 1
                        notice = "Unrecognized options detected: {'small_matrix_value'}. These will be passed to HiGHS verbatim."
                        options: _MixedHighsOptions = {
                            "time_limit": limit,
                            "presolve": presolve,
                            "mip_rel_gap": 1e-6,
                            "small_matrix_value": 1e-12,
                        }
                        attempt_started = time.monotonic()
                        with warnings.catch_warnings():
                            warnings.filterwarnings(
                                "ignore",
                                message="^" + re.escape(notice) + "$",
                                category=RuntimeWarning,
                            )
                            result = milp(
                                np.zeros_like(numerical_objective)
                                if feasibility_only
                                else numerical_objective,
                                integrality=backend_integer,
                                bounds=Bounds(
                                    backend_lower / variable_scale, backend_upper / variable_scale
                                ),
                                constraints=LinearConstraint(
                                    numerical, numerical_low, numerical_high
                                ),
                                options=options,
                            )
                        attempts.append(
                            ThermalSolveAttempt(
                                kind="feasibility_milp" if feasibility_only else "milp",
                                representation="condensed" if reduction is not None else "sparse",
                                status=int(result.status),
                                presolve=presolve,
                                time_limit_seconds=limit,
                                elapsed_seconds=time.monotonic() - attempt_started,
                                message=str(getattr(result, "message", "")),
                            )
                        )
                        if result.x is not None:
                            result.x = np.asarray(result.x, dtype=float) * variable_scale
                            if reduction is not None:
                                result.x = reduction.projection @ result.x + reduction.offset
                        if not feasibility_only:
                            value, bound = (
                                getattr(result, "fun", None),
                                getattr(result, "mip_dual_bound", None),
                            )
                            if value is not None:
                                result.fun = value + objective_constant
                            if bound is not None:
                                result.mip_dual_bound = bound + objective_constant
                        return result

                    if repair_integers is not None:
                        repair_lower, repair_upper = lower.copy(), upper.copy()
                        repair_lower[integer] = repair_upper[integer] = repair_integers
                        result = linear_solve(
                            repair_lower, repair_upper, presolve=False, physical_cleanup=True
                        )
                    else:
                        result = (
                            mixed_solve(presolve=True)
                            if integer.any()
                            else linear_solve(lower, upper, presolve=True)
                        )
                    # Presolve can wrongly reject tight but feasible thermal references.
                    # Retry the IDENTICAL mathematical model once, using remaining time.
                    if result.status == 2 and repair_integers is None:
                        remaining()
                        if not use_condensed and can_switch and verified_reference_available():
                            raise _NoSolution(
                                "error",
                                "Sparse solver rejected a fully verified reference witness",
                                numerical=True,
                            )
                        retries += 1
                        result = (
                            mixed_solve(presolve=False)
                            if integer.any()
                            else linear_solve(lower, upper, presolve=False)
                        )
                    if (
                        self.stage in ("reference_tracking", "joint_candidate")
                        and integer.any()
                        and result.status == 1
                        and result.x is None
                        and self.integer_hint_factory is not None
                        and self.reference_witness_validator is not None
                    ):
                        remaining()
                        try:
                            pattern = self.integer_hint_factory()
                        except (ValueError, _NoSolution) as hint_error:
                            if isinstance(hint_error, _NoSolution) and hint_error.status == "limit":
                                raise
                            pattern = None
                            fixed_pattern_hint_rejection = str(hint_error)
                        remaining()
                        if (
                            pattern is not None
                            and pattern.shape == (int(integer.sum()),)
                            and np.isfinite(pattern).all()
                            and np.array_equal(pattern, np.round(pattern))
                            and np.all(pattern >= lower[integer])
                            and np.all(pattern <= upper[integer])
                        ):
                            fixed_lower, fixed_upper = lower.copy(), upper.copy()
                            fixed_lower[integer] = fixed_upper[integer] = pattern
                            fixed_pattern_rescues += 1
                            seed = linear_solve(
                                fixed_lower,
                                fixed_upper,
                                presolve=True,
                                original_model=True,
                                time_cap=2.0,
                                fixed_pattern=True,
                            )
                            remaining()
                            if (
                                seed.status in (0, 1)
                                and seed.x is not None
                                and violation(np.asarray(seed.x, dtype=float)) <= _TOL
                                and self.reference_witness_validator(
                                    np.asarray(seed.x, dtype=float)
                                )
                            ):
                                remaining()
                                fixed_pattern_used = True
                                if self.budget is not None and self.stage == "reference_tracking":
                                    self.budget.reference_tracking_fixed_pattern_rescue = True
                                result = seed
                                # OptimizeResult is a mapping; this explicit
                                # branch status is not the LP's global status.
                                result["status"] = 1
                                result["fun"] = float(objective @ result.x)
                                result["mip_dual_bound"], result["mip_gap"] = None, None
                    if (
                        integer.any()
                        and result.status == 1
                        and result.x is None
                        and extend_unwitnessed_tracking()
                    ):
                        # Resume the ORIGINAL tracking objective. This is not a
                        # feasibility-only relaxation and earns no bound by itself.
                        result = mixed_solve(presolve=False)
                    rescue_bound = getattr(result, "mip_dual_bound", None)
                    if (
                        self.stage == "reference_tracking"
                        and integer.any()
                        and result.status == 1
                        and result.x is None
                    ):
                        remaining()
                        feasibility_rescues += 1
                        result = mixed_solve(presolve=False, feasibility_only=True)
                        if (
                            result.status in (0, 1)
                            and result.x is not None
                            and self.budget is not None
                        ):
                            self.budget.reference_tracking_feasibility_rescue = True
                    remaining()
                    if result.status not in (0, 1) or result.x is None:
                        if (
                            result.status == 2
                            and self.feasible_witness is not None
                            and violation(self.feasible_witness) <= _TOL
                        ):
                            raise _NoSolution(
                                "error",
                                "Solver rejected an independently verified feasible witness",
                            )
                        failed: ShadowStatus = "infeasible" if result.status == 2 else "limit"
                        if result.status not in (1, 2):
                            failed = "error"
                        raise _NoSolution(failed, str(result.message), numerical=result.status == 2)
                    x = np.asarray(result.x, dtype=float)
                    if x.shape != lower.shape or not np.isfinite(x).all():
                        raise _NoSolution("error", "Solver returned nonfinite or misshaped output")
                    if getattr(result, "fun", None) is None or not np.isfinite(result.fun):
                        raise _NoSolution("error", "Solver returned nonfinite output")
                    final_violation = violation(x)
                    if repair_integers is not None and final_violation > _TOL:
                        raise _NoSolution(
                            "error", "Physical cleanup failed original-model verification"
                        )
                    # A scaled MIP incumbent can have backend-tolerance continuous
                    # residuals in original W/Wh. It is NEVER returned directly: fix
                    # its verified integers and solve the continuous model accurately.
                    # Final original-unit checks below remain unchanged and mandatory.
                    integer_error = (
                        float(np.max(np.abs(x[integer] - np.round(x[integer]))))
                        if integer.any()
                        else 0.0
                    )
                    if not integer.any() and final_violation > _TOL:
                        original_refinements += 1
                        if self.budget is not None:
                            self.budget.cleanup_original_refinement_used = True
                        refined = linear_solve(lower, upper, presolve=False, original_model=True)
                        remaining()
                        if refined.status not in (0, 1) or refined.x is None:
                            raise _NoSolution(
                                "error", "Original-model LP refinement has no verified solution"
                            )
                        x = np.asarray(refined.x, dtype=float)
                        final_violation = violation(x)
                    if (not integer.any() and final_violation > _TOL) or integer_error > _TOL:
                        raise _NoSolution(
                            "error",
                            "Solver output failed independent feasibility verification",
                            numerical=True,
                        )
                    raw_bound = (
                        rescue_bound
                        if feasibility_rescues
                        else getattr(result, "mip_dual_bound", None)
                    )
                    lower_bound = (
                        float(raw_bound)
                        if raw_bound is not None and np.isfinite(raw_bound)
                        else None
                    )
                    gap = None if feasibility_rescues else getattr(result, "mip_gap", None)
                    success: Literal["optimal", "feasible"] = (
                        "optimal"
                        if result.status == 0
                        and not feasibility_rescues
                        and not original_refinements
                        and not physical_refinements
                        else "feasible"
                    )
                    if integer.any() and not fixed_pattern_used and repair_integers is None:
                        fixed_lower, fixed_upper = lower.copy(), upper.copy()
                        fixed_lower[integer] = fixed_upper[integer] = np.round(x[integer])
                        polished = linear_solve(fixed_lower, fixed_upper, presolve=True)
                        if polished.status == 2:
                            remaining()
                            retries += 1
                            polished = linear_solve(fixed_lower, fixed_upper, presolve=False)
                        remaining()
                        if polished.status not in (0, 1) or polished.x is None:
                            raise _NoSolution(
                                "limit",
                                "No verified integer witness after continuous cleanup",
                                numerical=polished.status == 2,
                                fixed_integers=fixed_lower[integer].copy(),
                            )
                        x = np.asarray(polished.x, dtype=float)
                        final_violation = violation(x)
                        if final_violation > _TOL:
                            original_refinements += 1
                            if self.budget is not None:
                                self.budget.cleanup_original_refinement_used = True
                            refined = linear_solve(
                                fixed_lower, fixed_upper, presolve=False, original_model=True
                            )
                            remaining()
                            if refined.status not in (0, 1) or refined.x is None:
                                raise _NoSolution(
                                    "error",
                                    "Original-model integer cleanup has no verified solution",
                                    numerical=True,
                                )
                            x = np.asarray(refined.x, dtype=float)
                            final_violation = violation(x)
                            success = "feasible"
                        if final_violation > _TOL:
                            raise _NoSolution(
                                "error",
                                "Cleaned integer witness failed feasibility verification",
                                numerical=True,
                            )
                        if polished.status != 0:
                            success = "feasible"
                    final_objective = float(objective @ x)
                    physical_error = self.physical_violation(
                        x, "physical_cleanup" if repair_integers is not None else "candidate"
                    )
                    remaining()
                    if physical_error > _TOL:
                        raise _NoSolution(
                            "error",
                            f"Joint physical verification failed: {self.physical_check_history[-1].metrics}",
                            numerical=True,
                            fixed_integers=np.round(x[integer]).copy(),
                        )
                    if repair_integers is not None and self.budget is not None:
                        self.budget.cleanup_physical_refinement_used = True
                    if not integer.any() and result.status == 0 and not original_refinements:
                        lower_bound, gap = final_objective, 0.0
                    elif lower_bound is not None:
                        gap = abs(final_objective - lower_bound) / max(abs(final_objective), 1e-12)
                    remaining()
                    status = success
                    return _Result(
                        x=x,
                        objective=final_objective,
                        # The original optimization bound stays diagnostic only after
                        # rescue/refinement. A fixed-integer LP cannot certify the
                        # global original MILP, and a feasibility objective is not EUR.
                        lower_bound=None
                        if original_refinements or feasibility_rescues or physical_refinements
                        else lower_bound,
                        gap=float(gap)
                        if gap is not None
                        and np.isfinite(gap)
                        and not original_refinements
                        and not feasibility_rescues
                        and not physical_refinements
                        else None,
                        status=success,
                        violation=final_violation,
                    )
                except _NoSolution as error:
                    if can_switch and error.numerical and verified_reference_available():
                        remaining()
                        if error.fixed_integers is not None and not physical_refinements:
                            pattern = error.fixed_integers
                            if (
                                pattern.shape == (int(integer.sum()),)
                                and np.isfinite(pattern).all()
                                and np.array_equal(pattern, np.round(pattern))
                                and np.all(pattern >= lower[integer])
                                and np.all(pattern <= upper[integer])
                            ):
                                repair_integers = pattern.copy()
                                physical_refinements += 1
                                if not use_condensed:
                                    representation_switches += 1
                                representation_switch_reason = str(error)
                                use_condensed = True
                                continue
                        elif not use_condensed:
                            representation_switches += 1
                            representation_switch_reason = str(error)
                            use_condensed = True
                            continue
                    raise

        except _NoSolution as error:
            status = error.status
            if self.budget is not None:
                self.budget.failed_stage = self.budget.failed_stage or self.stage
            raise
        finally:
            if self.budget is not None:
                self.budget.diagnostics.append(
                    ThermalSolveDiagnostics(
                        name=self.stage,
                        status=status,
                        elapsed_seconds=time.monotonic() - started,
                        solver_calls=calls,
                        presolve_retries=retries,
                        original_refinements=original_refinements,
                        feasibility_rescues=feasibility_rescues,
                        fixed_pattern_rescues=fixed_pattern_rescues,
                        fixed_pattern_hint_rejection=fixed_pattern_hint_rejection,
                        representation_switches=representation_switches,
                        representation_switch_reason=representation_switch_reason,
                        physical_refinements=physical_refinements,
                        physical_checks=tuple(self.physical_check_history[physical_history_start:]),
                        initial_allocation_seconds=seconds,
                        final_allocation_seconds=deadline - started,
                        adaptive_extensions=adaptive_extensions,
                        downstream_reserve_seconds=downstream_reserve,
                        condensed_state_variables=condensed_variables,
                        condensed_dynamics_rows=condensed_rows,
                        attempts=tuple(attempts),
                        objective=final_objective,
                        lower_bound=lower_bound,
                        violation=final_violation
                        if final_violation is not None and np.isfinite(final_violation)
                        else None,
                    )
                )


def _rc2_dynamics(
    loss: float, exchange: float, ratio: float, input_gain: float, seconds: int
) -> tuple[FloatArray, FloatArray, FloatArray]:
    from scipy.linalg import expm

    augmented = np.zeros((4, 4))
    augmented[:2, :2] = [[-loss - exchange, exchange], [exchange / ratio, -exchange / ratio]]
    augmented[0, 2] = input_gain / 1000
    augmented[0, 3] = loss
    if not np.isfinite(augmented).all():
        raise _NoSolution("unsupported", "RC2 coefficients are outside numerical support")
    transition = expm(augmented * seconds / 3600)
    if not np.isfinite(transition).all():
        raise _NoSolution("unsupported", "RC2 parameters produced nonfinite transitions")
    return transition[:2, :2], transition[:2, 2], transition[:2, 3]


def _dynamics(
    model: ThermalRC2Parameters, seconds: int
) -> tuple[FloatArray, FloatArray, FloatArray]:
    return _rc2_dynamics(
        model.room_loss_per_hour,
        model.room_mass_exchange_per_hour,
        model.mass_capacity_ratio,
        model.electric_gain_kelvin_per_kwh,
        seconds,
    )


def _planning_inputs(request: ThermalPlanningInputs) -> ThermalPlanningInputs:
    return ThermalPlanningInputs.model_validate(
        request.model_dump(
            include=set(ThermalPlanningInputs.model_fields), exclude_computed_fields=True
        )
    )


def _input_at_electric_floor(points: tuple[tuple[float, float], ...], floor: float) -> float:
    """Find the first supported thermal input reaching a steady electric floor.

    Equal-electricity plateaus use their lowest heat input. A requirement above
    the final documented point is rejected rather than extrapolated.
    """
    previous: tuple[float, float] | None = None
    for heat, power in points:
        if power >= floor:
            if previous is None:
                return heat
            q0, p0 = previous
            if power == p0:
                return q0
            return q0 + (heat - q0) * (floor - p0) / (power - p0)
        previous = heat, power
    raise ValueError("Configured minimum electricity exceeds the supported operating points")


def _pattern_polish(
    p: _Program,
    objective: FloatArray,
    request: ThermalPlanningInputs,
    source: _SourceHorizon,
    heat: list[int],
    thermal_integer_indices: list[int],
    budget: _PlanningBudget,
    seconds: float | None = None,
    x0: FloatArray | None = None,
    reserve: float = 12.0,
) -> FloatArray | None:
    """Exact local search on the source on/off pattern, accepted only on verified cost gain.

    The joint MILP gets no start and usually returns nothing within its limit, so the
    plan keeps the pattern of the cost seed (a rounded relaxation) or of the tracking
    witness. Rounding any positive relaxed heat up to the minimum power leaves short
    pauses and one-slot bursts that the verified cost model itself rates worse than
    continuous operation. Each move flips short blocks (pause <= 2 slots between runs
    -> run, run <= 2 slots between pauses -> pause), re-solves all continuous decisions
    as an LP with storage binaries held, re-checks the full original model and
    physical verification, and is kept only if the original objective falls. Nothing
    depends on clock times, prices or device type; it holds for any on/off source.
    """
    seconds = float(request.pattern_polish_seconds) if seconds is None else seconds
    budget.pattern_polish_enabled = True
    moves_before = budget.pattern_polish_moves
    n = len(heat)
    on_slots = p.on_slots
    if (
        seconds <= 0
        or (x0 is None and len(p.witness_values) != len(p.lower))
        or len(on_slots) != n
        or any(v is None for v in on_slots)
        or not thermal_integer_indices
    ):
        return None
    started = time.monotonic()
    deadline = started + seconds
    if getattr(p, "_matrix_store", None) is None:
        p._matrix_store = {}  # shared by the shallow copies: the row matrix is built once
    prefix = len(source.fixed_heat_prefix_w)
    thermal = set(thermal_integer_indices)
    storage_integers = [i for i, flag in enumerate(p.integer) if flag and i not in thermal]
    x = (
        np.array([p.witness_values[i] for i in range(len(p.lower))])
        if x0 is None
        else np.array(x0, dtype=float)
    )
    best = float(objective @ x)
    if x0 is None:
        budget.pattern_polish_start_eur = best

    def left() -> float:
        return min(deadline - time.monotonic(), budget.remaining("joint_polish") - reserve)

    def pattern(values: FloatArray) -> list[bool]:
        return [bool(values[v] > 0.5) for v in on_slots]  # type: ignore[index]

    def short_blocks(on: list[bool]) -> list[tuple[int, int, bool]]:
        found, i = [], 0
        while i < n:
            j = i
            while j < n and on[j] == on[i]:
                j += 1
            if i > 0 and j < n and j - i <= 2 and i >= prefix:
                found.append((i, j, on[i]))
            i = j
        return found

    def child() -> _Program:
        r = copy.copy(p)
        r.lower, r.upper = list(p.lower), list(p.upper)
        r.integer = [0] * len(p.integer)
        r.budget, r.stage = None, "joint_polish"
        r.witness_values = {}
        r.integer_hint_factory = None
        r.reference_witness_validator = None
        r.physical_check_history = []
        r.feasible_witness = None
        return r

    def evaluate(on: list[bool]) -> tuple[FloatArray, float] | None:
        budget.pattern_polish_evaluations += 1
        r = child()
        for i in storage_integers:
            r.lower[i] = r.upper[i] = float(round(x[i]))
        for k, v in enumerate(on_slots):
            if k >= prefix:
                r.lower[v] = r.upper[v] = 1.0 if on[k] else 0.0  # type: ignore[index]
        try:
            relaxed = r.solve(objective, max(0.2, min(left(), 4.0)))
            if relaxed.objective >= best - 1e-6:
                return None
            # Exact integers (segments, starts) from the proposed heat, then re-solve.
            proposal = np.clip(relaxed.x[heat], 0, source.max_power_w)
            proposal = np.where(proposal > _TOL, np.maximum(proposal, source.min_power_w), 0.0)
            proposal[:prefix] = source.fixed_heat_prefix_w
            hints, *_ = _thermal_program(request, source, witness_heat=proposal)
            r2 = child()
            for i in storage_integers:
                r2.lower[i] = r2.upper[i] = float(round(x[i]))
            for i in thermal_integer_indices:
                r2.lower[i] = r2.upper[i] = hints.witness_values[i]
            exact = r2.solve(objective, max(0.2, min(left(), 4.0)))
        except (_NoSolution, KeyError):
            return None
        if (
            exact.objective < best - 1e-6
            and p.witness_violation(exact.x) <= _TOL
            and p.physical_violation(exact.x, "pattern_polish") <= _TOL
        ):
            return exact.x, float(exact.objective)
        return None

    def flipped(on: list[bool], blocks: list[tuple[int, int, bool]]) -> list[bool]:
        new = list(on)
        for i, j, state in blocks:
            for k in range(i, j):
                new[k] = not state
        return new

    try:
        while left() > 1.0:
            on = pattern(x)
            blocks = short_blocks(on)
            if not blocks:
                break
            pauses = [b for b in blocks if not b[2]]
            runs = [b for b in blocks if b[2]]
            candidates: list[list[tuple[int, int, bool]]] = []
            if pauses:
                candidates.append(pauses)
            if runs:
                candidates.append(runs)
            candidates.extend([b] for b in pauses if len(pauses) > 1)
            candidates.extend([b] for b in runs if len(runs) > 1)
            improved = False
            for blocks_to_flip in candidates:
                if left() <= 1.0:
                    break
                outcome = evaluate(flipped(on, blocks_to_flip))
                if outcome is not None:
                    budget.pattern_polish_gain_eur += best - outcome[1]
                    x, best = outcome
                    budget.pattern_polish_moves += 1
                    p.witness_values = dict(enumerate(x))
                    budget.candidate_cost_seed_used = True
                    improved = True
                    break
            if not improved:
                break
    except _NoSolution:
        pass
    finally:
        used = time.monotonic() - started
        budget.pattern_polish_seconds_used += used
        budget.pattern_polish_extra_seconds += used
    return x if budget.pattern_polish_moves > moves_before else None


def _slot_power_bounds(
    source: _SourceHorizon,
    index: int,
    initial: FloatArray,
    band: ThermalComfortBand,
    *,
    enforce_lower: bool,
    previous_running: bool = False,
    end_upper_temperature_c: float | None = None,
) -> tuple[float, float]:
    """Return constant-power bounds satisfying every modeled minute of a slot."""
    low, high = 0.0, source.max_power_w[index]
    if source.total_electric_power_cap_w is not None:
        cap_value = (
            source.total_electric_power_cap_w
            - source.standby_power_w
            - source.running_auxiliary_power_w
            - (0.0 if previous_running else source.startup_energy_wh / 0.25)
        )
        if source.startup_warmup_s and not previous_running:
            # start slot electricity = base * (1 + warm-up/slot)
            cap_value /= 1.0 + source.startup_warmup_s / 900.0
        high = min(high, _input_at_electric_cap(source, index, cap_value))
    for minute, (a, b, e) in enumerate(_steps_at(source, index)):
        upper = (
            min(band.max_temperature_c, end_upper_temperature_c)
            if minute == 15 and end_upper_temperature_c is not None
            else band.max_temperature_c
        )
        free = float((a @ initial + e * source.outdoor_c[index])[0])
        gain = float(b[0] * source.power_gain_scales[index])
        if gain <= 0:
            if free > upper + _TOL or (enforce_lower and free < band.min_temperature_c - _TOL):
                return 1.0, 0.0
        else:
            high = min(high, (upper - free) / gain)
            if enforce_lower:
                low = max(low, (band.min_temperature_c - free) / gain)
        for j, (minimum, maximum) in enumerate(source.additional_temperature_bounds_c, start=2):
            free_extra = float((a @ initial + e * source.outdoor_c[index])[j])
            gain_extra = float(b[j] * source.power_gain_scales[index])
            if gain_extra > 0:
                low = max(low, (minimum - free_extra) / gain_extra)
                high = min(high, (maximum - free_extra) / gain_extra)
            elif free_extra < minimum - _TOL or free_extra > maximum + _TOL:
                return 1.0, 0.0
        node = source.source_temperature_state_index
        if node is not None:
            free_water = float((a @ initial + e * source.outdoor_c[index])[node])
            gain_water = float(b[node] * source.power_gain_scales[index])
            delta = source.delivery_temperature_c[index] - free_water
            # A hot store is allowed to coast with the compressor OFF. It must
            # not receive new heat from a colder declared supply temperature.
            if gain_water > 0:
                high = min(high, max(0.0, delta / gain_water))
            elif delta < -_TOL:
                high = min(high, 0.0)
            if source.source_flow_conductance_w_per_k:
                flow = source.source_flow_conductance_w_per_k[index]
                coefficient = source.power_gain_scales[index] * (1 + flow * b[node])
                high = min(high, max(0.0, flow * delta / coefficient))
    return low, high


def _allowed_slot_power(source: _SourceHorizon, index: int, low: float, high: float) -> bool:
    """Check the native source's OFF-or-modulating power interval."""
    return (low <= 0 <= high) or max(low, source.min_power_w[index]) <= high


def _prepare_recovery(
    request: ThermalPlanningInputs, source: _SourceHorizon
) -> tuple[ThermalPlanningInputs, _SourceHorizon, ThermalRecoveryReport | None]:
    """Build a common, target-limited cold recovery prefix.

    Only the initially cold prefix gets an explicit internal lower-bound
    exception. Upper comfort, source power bounds and all later comfort remain
    hard. A full quarter-hour is a modeled mean-power action, not a prediction
    of an indoor thermostat's sub-minute response.
    """
    first = request.comfort_bands[0]
    if (
        request.recovery is not None
        and request.recovery.allow_passive_cooldown
        and request.state.room_temperature_c > first.max_temperature_c
    ):
        return _prepare_passive_cooldown(request, source)
    if request.recovery is None or request.state.room_temperature_c >= first.min_temperature_c:
        return request, source, None
    initial = _initial_temperatures(request, source)

    def run_pass(
        rate: float | None,
    ) -> tuple[list[FloatArray], list[float], list[float], list[ThermalComfortBand], bool]:
        states = [initial]
        powers: list[float] = []
        deficits: list[float] = []
        bands = list(request.comfort_bands)
        limit = min(len(bands), request.recovery.max_duration_seconds // 900)
        completed = False
        for k in range(limit):
            band = request.comfort_bands[k]
            previous_running = powers[-1] > _TOL if powers else source.initial_running
            minimum, maximum = _slot_power_bounds(
                source,
                k,
                states[-1],
                band,
                enforce_lower=False,
                previous_running=previous_running,
                end_upper_temperature_c=(
                    request.comfort_bands[k + 1].max_temperature_c if k + 1 < len(bands) else None
                ),
            )
            maximum_power = max(0.0, min(source.max_power_w[k], maximum))
            if maximum < -_TOL or minimum > maximum + _TOL:
                break
            a_end, b_end, e_end = _steps_at(source, k)[15]
            passive_end = float((a_end @ states[-1] + e_end * source.outdoor_c[k])[0])
            gain_at_max = float((b_end * maximum_power * source.power_gain_scales[k])[0])
            desired_end = band.target_temperature_c
            if rate is not None:
                # Slowest admissible rise: start value plus rate times elapsed time.
                desired_end = min(desired_end, float(initial[0]) + rate * (k + 1) * 0.25)
            power = (
                min(
                    maximum_power,
                    max(0.0, (desired_end - passive_end) * maximum_power / gain_at_max),
                )
                if gain_at_max > _TOL
                else maximum_power
            )
            if 0 < power < source.min_power_w[k]:
                power = source.min_power_w[k] if source.min_power_w[k] <= maximum_power else 0.0

            def projected(power_w: float) -> tuple[list[float], FloatArray]:
                rooms = [
                    float(
                        (
                            a @ states[-1]
                            + b * power_w * source.power_gain_scales[k]
                            + e * source.outdoor_c[k]
                        )[0]
                    )
                    for a, b, e in _steps_at(source, k)
                ]
                return (
                    rooms,
                    a_end @ states[-1]
                    + b_end * power_w * source.power_gain_scales[k]
                    + e_end * source.outdoor_c[k],
                )

            minute_rooms, next_state = projected(power)
            if k + 1 < len(bands):
                low, high = _slot_power_bounds(
                    source,
                    k + 1,
                    next_state,
                    request.comfort_bands[k + 1],
                    enforce_lower=True,
                    previous_running=power > _TOL,
                )
                if not _allowed_slot_power(source, k + 1, low, high) and (
                    # A rate-limited recovery continues into its next allowed slot instead
                    # of jumping to the maximum; if the ramp does not get there in time the
                    # caller raises the rate or falls back to the unrestricted recovery.
                    rate is None
                ):
                    power = maximum_power
                    minute_rooms, next_state = projected(power)
            if max(minute_rooms) > band.max_temperature_c + _TOL:
                break
            bands[k] = ThermalComfortBand(
                min_temperature_c=min(band.min_temperature_c, min(minute_rooms)),
                target_temperature_c=band.target_temperature_c,
                max_temperature_c=band.max_temperature_c,
            )
            powers.append(power)
            deficits.extend(max(0.0, band.min_temperature_c - room) for room in minute_rooms[:-1])
            states.append(next_state)
            next_index = k + 1
            if next_index == len(bands):
                completed = states[-1][0] >= band.min_temperature_c - _TOL
            else:
                low, high = _slot_power_bounds(
                    source,
                    next_index,
                    states[-1],
                    request.comfort_bands[next_index],
                    enforce_lower=True,
                    previous_running=power > _TOL,
                )
                completed = _allowed_slot_power(source, next_index, low, high)
            if (
                rate is not None
                and completed
                and states[-1][0] < request.comfort_bands[0].min_temperature_c - _TOL
            ):
                # A later setback slot must not end a slow recovery below the minimum it
                # waived: the rise only counts as done once the initial minimum is reached.
                completed = False
            if completed:
                break
        return states, powers, deficits, bands, completed

    requested_rate = request.recovery.max_heating_rate_kelvin_per_hour
    used_rate = requested_rate
    states, powers, deficits, bands, completed = run_pass(requested_rate)
    if requested_rate is not None and not completed:
        # The slow rise would miss the comfort minimum in time: raise the rate as
        # little as needed. A rate that reaches every target at once is the
        # unrestricted behavior, so this option can never make a plan unsolvable.
        horizon = min(len(request.comfort_bands), request.recovery.max_duration_seconds // 900)
        unrestricted = (
            4.0
            * max(
                0.0,
                max(b.target_temperature_c for b in request.comfort_bands[:horizon])
                - float(initial[0]),
            )
            + 1.0
        )
        best = run_pass(unrestricted)
        used_rate = unrestricted
        if not best[4]:
            best = run_pass(None)  # exactly the unrestricted recovery of earlier versions
        else:
            low, high = requested_rate, unrestricted
            for _ in range(10):
                middle = 0.5 * (low + high)
                trial = run_pass(middle)
                if trial[4]:
                    high, best, used_rate = middle, trial, middle
                else:
                    low = middle
        states, powers, deficits, bands, completed = best
    report = ThermalRecoveryReport(
        completed=completed,
        end_datetime=request.fleet.start_datetime + timedelta(seconds=900 * len(powers)),
        heat_power_w=tuple(powers),
        room_temperature_c=tuple(float(state[0]) for state in states),
        mass_temperature_c=tuple(float(state[1]) for state in states),
        cold_minutes=sum(value > _TOL for value in deficits),
        cold_degree_hours=sum(deficits) / 60,
        maximum_cold_deficit_c=max(deficits, default=0.0),
        power_input_kind="thermal" if source.operating_points else "electrical",
        distribution_temperatures_c=_distribution_trajectories(source, np.asarray(states)),
        requested_heating_rate_kelvin_per_hour=requested_rate,
        heating_rate_kelvin_per_hour=used_rate,
        heating_rate_raised=(
            None if requested_rate is None else bool(used_rate > requested_rate * (1 + 1e-9))
        ),
    )
    return (
        request.model_copy(update={"comfort_bands": tuple(bands)}),
        replace(source, fixed_heat_prefix_w=tuple(powers)),
        report,
    )


def _prepare_passive_cooldown(
    request: ThermalPlanningInputs, source: _SourceHorizon
) -> tuple[ThermalPlanningInputs, _SourceHorizon, ThermalRecoveryReport]:
    """Fix heat to zero until normal minute-wise comfort is reachable.

    Only unavoidable initial excess is admitted, with its exact passive trace
    reported separately. Lower comfort and distribution bounds remain hard.
    No later tightening of upper comfort may be waived by this initial prefix.
    This models residual heat, not active cooling or a relaxed heating target.
    """
    if request.recovery is None:
        raise ValueError("Passive cooldown requires an explicit recovery policy")
    states = [_initial_temperatures(request, source)]
    powers: list[float] = []
    excesses: list[float] = []
    bands = list(request.comfort_bands)
    completed = False
    for k in range(min(len(bands), request.recovery.max_duration_seconds // 900)):
        band = request.comfort_bands[k]
        if band.max_temperature_c < request.comfort_bands[0].max_temperature_c:
            break
        trace = [a @ states[-1] + e * source.outdoor_c[k] for a, _, e in _steps_at(source, k)]
        rooms = [float(state[0]) for state in trace]
        if min(rooms) < band.min_temperature_c - _TOL:
            break
        if any(
            min(float(state[j]) for state in trace) < low - _TOL
            or max(float(state[j]) for state in trace) > high + _TOL
            for j, (low, high) in enumerate(source.additional_temperature_bounds_c, start=2)
        ):
            break
        bands[k] = band.model_copy(
            update={"max_temperature_c": max(band.max_temperature_c, max(rooms))}
        )
        powers.append(0.0)
        excesses.extend(max(0.0, room - band.max_temperature_c) for room in rooms[:-1])
        states.append(trace[-1])
        next_index = k + 1
        if next_index == len(bands):
            completed = (
                band.min_temperature_c - _TOL <= states[-1][0] <= band.max_temperature_c + _TOL
            )
        else:
            low, high = _slot_power_bounds(
                source,
                next_index,
                states[-1],
                request.comfort_bands[next_index],
                enforce_lower=True,
                previous_running=False,
            )
            completed = _allowed_slot_power(source, next_index, low, high)
        if completed:
            break
    report = ThermalRecoveryReport(
        direction="passive_cooldown",
        completed=completed,
        end_datetime=request.fleet.start_datetime + timedelta(seconds=900 * len(powers)),
        heat_power_w=tuple(powers),
        room_temperature_c=tuple(float(state[0]) for state in states),
        mass_temperature_c=tuple(float(state[1]) for state in states),
        cold_minutes=0,
        cold_degree_hours=0,
        maximum_cold_deficit_c=0,
        hot_minutes=sum(value > _TOL for value in excesses),
        hot_degree_hours=sum(excesses) / 60,
        maximum_hot_excess_c=max(excesses, default=0.0),
        power_input_kind="thermal" if source.operating_points else "electrical",
        distribution_temperatures_c=_distribution_trajectories(source, np.asarray(states)),
    )
    return (
        request.model_copy(update={"comfort_bands": tuple(bands)}),
        replace(source, fixed_heat_prefix_w=tuple(powers)),
        report,
    )


def _passive_upper_bound(source: _SourceHorizon, initial: FloatArray) -> float | None:
    """Prove a minute-grid maximum principle, or decline this optimization.

    If A>=0, E>=0, A*1+E=1 and B_j*G<=A[j,source], then q<=G*(VL-Tsource)
    implies Tnext_j<=M+(B_j*G-A[j,source])*(M-Tsource)<=M. OFF is passive.
    This is an induction on the exact 60s transitions, not an assumption that
    unsampled trajectories or arbitrary lumped networks are monotone.
    """
    node = source.source_temperature_state_index
    if node is None or not source.source_flow_conductance_w_per_k:
        return None
    for k, flow in enumerate(source.source_flow_conductance_w_per_k):
        a, b, e = _steps_at(source, k)[1]
        if (
            np.min(a) < 0
            or np.min(b) < 0
            or np.min(e) < 0
            or not np.allclose(a @ np.ones(len(b)) + e, 1, rtol=0, atol=1e-12)
            or np.any(b * flow > a[:, node] * (1 - 1e-12))
        ):
            return None
    return max(float(np.max(initial)), max(source.outdoor_c), max(source.delivery_temperature_c))


def _state_domains(
    request: ThermalPlanningInputs, source: _SourceHorizon
) -> tuple[FloatArray, FloatArray, float | None]:
    """Finite forward interval bounds, intersected with original hard constraints."""
    initial = _initial_temperatures(request, source)
    n, dimensions = len(request.comfort_bands), len(initial)
    lower, upper = np.empty((n + 1, dimensions)), np.empty((n + 1, dimensions))
    lower[0] = upper[0] = initial
    maximum = _passive_upper_bound(source, initial)
    for k, band in enumerate(request.comfort_bands):
        a, b, e = _steps_at(source, k)[15]
        gain = b * source.power_gain_scales[k] * source.max_power_w[k]
        lower[k + 1] = (
            np.maximum(a, 0) @ lower[k]
            + np.minimum(a, 0) @ upper[k]
            + e * source.outdoor_c[k]
            + np.minimum(gain, 0)
            - 1e-9
        )
        upper[k + 1] = (
            np.maximum(a, 0) @ upper[k]
            + np.minimum(a, 0) @ lower[k]
            + e * source.outdoor_c[k]
            + np.maximum(gain, 0)
            + 1e-9
        )
        low, high = band.min_temperature_c, band.max_temperature_c
        if k + 1 < n:
            low = max(low, request.comfort_bands[k + 1].min_temperature_c)
            high = min(high, request.comfort_bands[k + 1].max_temperature_c)
        lower[k + 1, 0] = max(lower[k + 1, 0], low)
        upper[k + 1, 0] = min(upper[k + 1, 0], high)
        for j, (lo, hi) in enumerate(source.additional_temperature_bounds_c, 2):
            lower[k + 1, j] = max(lower[k + 1, j], lo)
            upper[k + 1, j] = min(upper[k + 1, j], hi)
        if maximum is not None:
            upper[k + 1] = np.minimum(upper[k + 1], maximum)
        if np.any(lower[k + 1] > upper[k + 1]):
            raise _NoSolution("infeasible", f"No reachable thermal boundary after slot {k}")
    return lower, upper, maximum


def _thermal_program(
    request: ThermalPlanningInputs,
    source: _SourceHorizon,
    *,
    tracking: bool = False,
    budget: _PlanningBudget | None = None,
    witness_heat: FloatArray | None = None,
) -> tuple[
    _Program,
    list[int],
    list[list[int]],
    list[int],
    tuple[tuple[FloatArray, FloatArray, FloatArray], ...],
    list[int],
]:
    p = _Program(budget)
    n = len(request.comfort_bands)
    q = p.var(n, high=max(source.max_power_w))
    initial = _initial_temperatures(request, source)
    dimensions = len(initial)
    states = [p.var(n + 1, low=-np.inf) for _ in range(dimensions)]
    state_lower, state_upper, passive_maximum = _state_domains(request, source)
    for j in range(dimensions):
        for k in range(n + 1):
            p.lower[states[j][k]], p.upper[states[j][k]] = state_lower[k, j], state_upper[k, j]
    if witness_heat is not None:
        witness_states = _trajectory(request, source, witness_heat)
        p.set_witness(q, witness_heat)
        for j in range(dimensions):
            p.set_witness(states[j], witness_states[:, j])
    steps = source.steps
    slack = []
    electric = []
    previous_on: int | None = None
    p.on_slots = []
    thermal_recurrences: list[tuple[int, int]] = []
    for k, (outside, band) in enumerate(zip(source.outdoor_c, request.comfort_bands)):
        slot_steps = _steps_at(source, k)
        a, b, e = slot_steps[15]
        p.upper[q[k]] = source.max_power_w[k]
        if k < len(source.fixed_heat_prefix_w):
            p.lower[q[k]] = p.upper[q[k]] = source.fixed_heat_prefix_w[k]
        scale = source.power_gain_scales[k]
        for j in range(dimensions):
            thermal_recurrences.append((states[j][k + 1], len(p.rows)))
            p.eq(
                {
                    states[j][k + 1]: 1,
                    **{states[l][k]: -a[j, l] for l in range(dimensions)},
                    q[k]: -b[j] * scale,
                },
                float(e[j] * outside),
            )
        # Each half-open slot's band also constrains its continuous boundary
        # limit. Adjacent incompatible bands cannot create an instantaneous jump.
        source_node = source.source_temperature_state_index
        on: int | None = None
        if source.min_power_w[k]:
            on = p.var(1, high=1, binary=True)[0]
            if k < len(source.fixed_heat_prefix_w):
                p.lower[on] = p.upper[on] = float(source.fixed_heat_prefix_w[k] > 0)
            p.row({q[k]: 1, on: -source.max_power_w[k]}, high=0)
            p.row({q[k]: 1, on: -source.min_power_w[k]}, low=0)
            if witness_heat is not None:
                p.set_witness([on], [float(witness_heat[k] > _TOL)])
        big_ms: list[float] = []
        if source_node is not None:
            multiplier = (
                source.source_flow_conductance_w_per_k[k]
                if source.source_flow_conductance_w_per_k
                else 1.0
            )
            for am, _, em in slot_steps:
                # Only the OFF branch needs M: q=0 there. Interval arithmetic
                # gives a proven reachable OFF upper, not a desired trajectory.
                off_upper = float(
                    np.maximum(am[source_node], 0) @ state_upper[k]
                    + np.minimum(am[source_node], 0) @ state_lower[k]
                    + em[source_node] * outside
                )
                upper = min(
                    source.additional_temperature_bounds_c[source_node - 2][1],
                    off_upper + 1e-9,
                    passive_maximum if passive_maximum is not None else np.inf,
                )
                big_ms.append(max(0.0, multiplier * (upper - source.delivery_temperature_c[k])))
        active = None
        if any(big_ms):
            active = on if on is not None else p.var(1, high=1, binary=True)[0]
            if on is None:
                p.row({q[k]: 1, active: -source.max_power_w[k]}, high=0)
            if witness_heat is not None:
                p.set_witness([active], [float(witness_heat[k] > _TOL)])
        for minute, (am, bm, em) in enumerate(slot_steps):
            terms = {
                **{states[j][k]: float(am[0, j]) for j in range(dimensions)},
                q[k]: float(bm[0] * scale),
            }
            exog = float(em[0] * outside)
            if minute not in (0, 15):
                # Endpoint variable bounds enforce both adjacent bands exactly.
                p.row(terms, band.min_temperature_c - exog, band.max_temperature_c - exog)
            if (
                tracking
                and minute < 15
                and (not request.comfort_required or request.comfort_required[k])
            ):
                under, over = p.var(2)
                slack.extend([under, over])
                p.row({**terms, under: 1}, low=band.target_temperature_c - 0.05 - exog)
                p.row({**terms, over: -1}, high=band.target_temperature_c + 0.05 - exog)
            for j, (minimum, maximum) in enumerate(source.additional_temperature_bounds_c, start=2):
                if minute in (0, 15):
                    continue  # Identical state-variable bounds plus exact dynamics.
                extra_terms = {
                    **{states[l][k]: float(am[j, l]) for l in range(dimensions)},
                    q[k]: float(bm[j] * scale),
                }
                extra_exog = float(em[j] * outside)
                p.row(extra_terms, minimum - extra_exog, maximum - extra_exog)
            if source_node is not None:
                delivery = source.delivery_temperature_c[k]
                multiplier = (
                    source.source_flow_conductance_w_per_k[k]
                    if source.source_flow_conductance_w_per_k
                    else 1.0
                )
                big_m = big_ms[minute]
                source_terms = {
                    states[l][k]: float(multiplier * am[source_node, l]) for l in range(dimensions)
                }
                source_terms[q[k]] = float(multiplier * bm[source_node] * scale)
                if source.source_flow_conductance_w_per_k:
                    source_terms[q[k]] += scale
                if active is not None:
                    source_terms[active] = big_m
                # Express the identical flow inequality in kelvin rather than
                # watts. Stiff slab/water capacities otherwise make MILP
                # presolve wrongly reject an independently feasible witness.
                normalizer = max(multiplier, 1.0)
                p.row(
                    {index: value / normalizer for index, value in source_terms.items()},
                    high=(multiplier * (delivery - float(em[source_node] * outside)) + big_m)
                    / normalizer,
                )
        base = q[k]
        if source.operating_points:
            if on is None:
                raise _NoSolution(
                    "unsupported", "Operating-point segments require positive minimum heat"
                )
            points = source.operating_points[k]
            weights = p.var(len(points), high=1)
            p.eq({**dict.fromkeys(weights, 1), on: -1}, 0)
            p.eq({q[k]: 1, **{i: -point[0] for i, point in zip(weights, points)}}, 0)
            base = p.var(1, high=points[-1][1])[0]
            p.eq({base: 1, **{i: -point[1] for i, point in zip(weights, points)}}, 0)
            witness_segment = 0
            if witness_heat is not None:
                lambdas = np.zeros(len(points))
                power = witness_heat[k]
                if power > _TOL:
                    witness_segment = max(
                        0,
                        min(
                            len(points) - 2,
                            int(
                                np.searchsorted([point[0] for point in points], power, side="right")
                            )
                            - 1,
                        ),
                    )
                    if len(points) == 1:
                        lambdas[0] = 1
                    else:
                        fraction = (power - points[witness_segment][0]) / (
                            points[witness_segment + 1][0] - points[witness_segment][0]
                        )
                        lambdas[witness_segment], lambdas[witness_segment + 1] = (
                            1 - fraction,
                            fraction,
                        )
                p.set_witness(weights, lambdas)
                p.set_witness([base], [_base_electric_at_input(source, k, power)])
            if len(points) > 1:
                segments = p.var(len(points) - 1, high=1, binary=True)
                p.eq({**dict.fromkeys(segments, 1), on: -1}, 0)
                for j, weight in enumerate(weights):
                    adjacent = segments[max(0, j - 1) : min(len(segments), j + 1)]
                    p.row({weight: 1, **dict.fromkeys(adjacent, -1)}, high=0)
                if witness_heat is not None:
                    p.set_witness(
                        segments,
                        [
                            float(witness_heat[k] > _TOL and j == witness_segment)
                            for j in range(len(segments))
                        ],
                    )
        start: int | None = None
        warm: int | None = None
        if source.startup_modelled:
            if on is None:
                raise _NoSolution(
                    "unsupported", "Startup accounting requires known positive minimum modulation"
                )
            start = p.var(1, high=1, binary=True)[0]
            p.row({start: 1, on: -1}, high=0)
            if previous_on is None:
                p.row({start: 1, on: -1}, low=-int(source.initial_running))
                p.upper[start] = 1 - int(source.initial_running)
            else:
                p.row({start: 1, on: -1, previous_on: 1}, low=0)
                p.row({start: 1, previous_on: 1}, high=1)
            if witness_heat is not None:
                was_on = source.initial_running if k == 0 else witness_heat[k - 1] > _TOL
                p.set_witness([start], [float(witness_heat[k] > _TOL and not was_on)])
            if source.startup_warmup_s:
                # Exact linearisation of start * base (start is already a binary; no new binary):
                # warm = base if start else 0.
                bound = float(p.upper[base])
                if not np.isfinite(bound):
                    bound = float(source.max_power_w[k])
                warm = p.var(1, high=bound)[0]
                p.row({warm: 1, base: -1}, high=0)
                p.row({warm: 1, start: -bound}, high=0)
                p.row({warm: 1, base: -1, start: -bound}, low=-bound)
                if witness_heat is not None:
                    p.set_witness(
                        [warm],
                        [
                            _base_electric_at_input(source, k, float(witness_heat[k]))
                            if (witness_heat[k] > _TOL and not was_on)
                            else 0.0
                        ],
                    )
        previous_on = on
        p.on_slots.append(on)
        if (
            source.standby_power_w
            or source.running_auxiliary_power_w
            or source.startup_energy_wh
            or source.startup_warmup_s
        ):
            net = p.var(1)[0]
            terms = {net: 1.0, base: -1.0}
            if source.running_auxiliary_power_w:
                if on is None:
                    raise _NoSolution(
                        "unsupported",
                        "Auxiliary accounting requires known positive minimum modulation",
                    )
                terms[on] = -source.running_auxiliary_power_w
            if start is not None:
                terms[start] = -source.startup_energy_wh / 0.25
            if warm is not None:
                terms[warm] = -source.startup_warmup_s / 900.0
            p.eq(terms, source.standby_power_w)
            if witness_heat is not None:
                p.set_witness([net], [_source_electric_power(source, witness_heat)[k]])
        else:
            net = base
        if source.total_electric_power_cap_w is not None:
            p.upper[net] = min(p.upper[net], source.total_electric_power_cap_w)
        electric.append(net)
        if k > len(source.fixed_heat_prefix_w) and request.smoothing_seconds is not None:
            epoch = request.fleet.start_datetime.timestamp()
            current_block = int((epoch + k * 900) // request.smoothing_seconds)
            previous_block = int((epoch + (k - 1) * 900) // request.smoothing_seconds)
            if current_block == previous_block:
                p.eq({electric[k]: 1, electric[k - 1]: -1}, 0)
    if dimensions > 2:
        p.thermal_state_substitution = _ThermalStateSubstitution(
            initial=tuple(state[0] for state in states),
            inputs=tuple(q),
            recurrences=tuple(thermal_recurrences),
            # Keep the sparse tracking/reference formulation: its thousands
            # of local target slacks become dense after condensation. The
            # proven conditioning problem concerns the joint terminal/mean
            # model; only that stage uses the exact substitution.
            enabled_stages=("joint_candidate",),
        )
    return p, q, states, slack, steps, electric


def _source_electric_power(source: _SourceHorizon, base_power: FloatArray) -> FloatArray:
    """Independently reconstruct base plus declared electrical-only components."""
    running = base_power > _TOL
    previous = np.r_[source.initial_running, running[:-1]]
    starts = running & ~previous
    base_electric = np.array(
        [_base_electric_at_input(source, k, float(power)) for k, power in enumerate(base_power)]
    )
    return (
        base_electric
        + source.standby_power_w
        + running * source.running_auxiliary_power_w
        + starts * source.startup_energy_wh / 0.25
        + starts * (source.startup_warmup_s / 900.0) * base_electric
    )


def _trajectory(
    request: ThermalPlanningInputs, source: _SourceHorizon, heat: FloatArray
) -> FloatArray:
    values = [_initial_temperatures(request, source)]
    for k, (power, outside) in enumerate(zip(heat, source.outdoor_c)):
        a, b, e = _steps_at(source, k)[15]
        values.append(a @ values[-1] + b * power * source.power_gain_scales[k] + e * outside)
    return np.asarray(values)


def _minute_temperatures(
    request: ThermalPlanningInputs, source: _SourceHorizon, heat: FloatArray, trajectory: FloatArray
) -> tuple[FloatArray, float]:
    samples, violation = [], 0.0
    for k, band in enumerate(request.comfort_bands):
        for minute, (a, b, e) in enumerate(_steps_at(source, k)):
            room = float(
                (
                    a @ trajectory[k]
                    + b * heat[k] * source.power_gain_scales[k]
                    + e * source.outdoor_c[k]
                )[0]
            )
            violation = max(violation, band.min_temperature_c - room, room - band.max_temperature_c)
            if minute < 15:
                samples.append(room)
    return np.asarray(samples), max(0.0, violation)


def _distribution_violation(
    request: ThermalPlanningInputs,
    source: _SourceHorizon,
    heat: FloatArray,
    trajectory: FloatArray,
) -> float:
    """Independently verify every store and active source/supply constraint."""
    violation = 0.0
    for k, power in enumerate(heat):
        thermal = float(power * source.power_gain_scales[k])
        for a, b, e in _steps_at(source, k):
            values = a @ trajectory[k] + b * thermal + e * source.outdoor_c[k]
            for j, (minimum, maximum) in enumerate(source.additional_temperature_bounds_c, start=2):
                violation = max(violation, minimum - values[j], values[j] - maximum)
            node = source.source_temperature_state_index
            if node is not None and power > _TOL:
                difference = source.delivery_temperature_c[k] - values[node]
                violation = max(violation, -difference)
                if source.source_flow_conductance_w_per_k:
                    flow = source.source_flow_conductance_w_per_k[k]
                    violation = max(violation, (thermal - flow * difference) / max(flow, 1.0))
    return float(max(0.0, violation))


def _source_flow_violation_w(
    source: _SourceHorizon, heat: FloatArray, trajectory: FloatArray
) -> float:
    """Check absolute source heat-flow residuals in W, separately from kelvin.

    A small temperature-normalized residual alone is not a watt guarantee when
    conductance is large. OFF still permits a store warmer than the supply.
    """
    node = source.source_temperature_state_index
    if node is None or not source.source_flow_conductance_w_per_k:
        return 0.0
    violation = 0.0
    for k, power in enumerate(heat):
        if power <= _TOL:
            continue
        thermal = float(power * source.power_gain_scales[k])
        flow = source.source_flow_conductance_w_per_k[k]
        for a, b, e in _steps_at(source, k):
            values = a @ trajectory[k] + b * thermal + e * source.outdoor_c[k]
            supplied = flow * (source.delivery_temperature_c[k] - values[node])
            violation = max(violation, thermal - supplied)
    return max(0.0, float(violation))


def _reference(
    request: ThermalPlanningInputs, source: _SourceHorizon, *, budget: _PlanningBudget | None = None
) -> tuple[FloatArray, FloatArray, FloatArray, bool]:
    p, q, _, slack, _, electric = _thermal_program(request, source, tracking=True, budget=budget)
    p.stage = "reference_tracking"
    p.adaptive_stage_cap_seconds = request.fleet.solver_time_limit_seconds

    def integer_hints() -> FloatArray | None:
        hint_heat = np.array(source.max_power_w, dtype=float)
        hint_heat[: len(source.fixed_heat_prefix_w)] = source.fixed_heat_prefix_w
        if source.operating_points:
            # An all-maximum segment pattern excludes normal part-load operation.
            # Follow the target to propose segments; the constrained LP below
            # still verifies every state and bound before accepting any witness.
            current = _initial_temperatures(request, source)
            for k, band in enumerate(request.comfort_bands):
                a, b, e = _steps_at(source, k)[-1]
                free = a @ current + e * source.outdoor_c[k]
                gain = float(b[0] * source.power_gain_scales[k])
                if k >= len(source.fixed_heat_prefix_w) and gain > 0:
                    hint_target = (
                        band.target_temperature_c
                        if not request.comfort_required or request.comfort_required[k]
                        else band.min_temperature_c
                    )
                    wanted = max(0.0, (hint_target - float(free[0])) / gain)
                    hint_heat[k] = (
                        min(source.max_power_w[k], max(source.min_power_w[k], wanted))
                        if wanted > _TOL
                        else 0.0
                    )
                current = free + b * hint_heat[k] * source.power_gain_scales[k]
            if any(len(points) > 2 for points in source.operating_points):
                # A greedy target rollout can choose the wrong segment before
                # a future comfort transition. Relaxed whole-horizon planning
                # supplies a better INTEGER SEED, never an executable plan.
                # The original fixed-pattern LP and physical checks still decide.
                relaxed, relaxed_q, _, relaxed_slack, _, relaxed_electric = _thermal_program(
                    request,
                    source,
                    tracking=True,
                    budget=budget,
                )
                relaxed.stage = "reference_segment_hint_relaxation"
                relaxed.integer = [0] * len(relaxed.integer)
                objective = np.zeros(len(relaxed.lower))
                objective[relaxed_slack] = 1 / 60
                try:
                    seed = relaxed.solve(
                        objective, min(2.0, budget.remaining(relaxed.stage) if budget else 2.0)
                    )
                    hint_heat = np.clip(seed.x[relaxed_q], 0, source.max_power_w)
                    if request.smoothing_seconds is not None and not (
                        source.standby_power_w
                        or source.running_auxiliary_power_w
                        or source.startup_energy_wh
                        or source.startup_warmup_s
                    ):
                        # Smoothing constrains ELECTRICITY across slots. Choose
                        # segments from that shared electrical seed, otherwise
                        # relaxed convex mixtures can pick mutually disjoint
                        # electrical ranges despite similar thermal loads.
                        hint_heat = np.array(
                            [
                                _input_at_electric_cap(source, k, float(power))
                                for k, power in enumerate(seed.x[relaxed_electric])
                            ]
                        )
                    hint_heat = np.where(
                        hint_heat > _TOL, np.maximum(hint_heat, source.min_power_w), 0.0
                    )
                    hint_heat[: len(source.fixed_heat_prefix_w)] = source.fixed_heat_prefix_w
                except _NoSolution:
                    pass  # The bounded greedy seed remains available.
        hints, *_ = _thermal_program(request, source, tracking=True, witness_heat=hint_heat)
        # Only a complete integer pattern crosses this boundary. Maximum Q and
        # its temperature trajectory are untrusted and never used as a plan.
        if not (
            hints.lower == p.lower
            and hints.upper == p.upper
            and hints.integer == p.integer
            and hints.rows == p.rows
            and hints.row_lower == p.row_lower
            and hints.row_upper == p.row_upper
        ):
            return None
        indices = [index for index, flag in enumerate(p.integer) if flag]
        if any(index not in hints.witness_values for index in indices):
            return None
        return np.array([hints.witness_values[index] for index in indices])

    def validate_reference(values: FloatArray) -> bool:
        heat = np.clip(values[q], 0, source.max_power_w)
        states = _trajectory(request, source, heat)
        _, comfort = _minute_temperatures(request, source, heat, states)
        return (
            max(
                comfort,
                _distribution_violation(request, source, heat, states),
                _source_flow_violation_w(source, heat, states),
            )
            <= _TOL
        )

    p.integer_hint_factory = integer_hints
    p.reference_witness_validator = validate_reference
    tracking = np.zeros(len(p.lower))
    tracking[slack] = 1 / 60  # Degree-hours outside the +/-0.05 K target tube.
    first = p.solve(
        tracking,
        budget.allowance(request.fleet.solver_time_limit_seconds, 4)
        if budget
        else request.fleet.solver_time_limit_seconds,
    )
    p.row({i: 1 / 60 for i in slack}, high=first.objective + 1e-8)
    p.feasible_witness = first.x.copy()
    p.stage = "reference_heat_energy"
    energy = np.zeros(len(p.lower))
    energy[electric] = 0.25 / 1000
    # Refining reference kWh is secondary to the joint EUR objective.
    # Preserve the verified tracking witness and reserve time for that search
    # on multi-day horizons. Short horizons retain their comparison budget.
    try:
        second = p.solve(
            energy,
            min(
                2.0 if len(request.comfort_bands) > 96 else request.fleet.solver_time_limit_seconds,
                budget.allowance(request.fleet.solver_time_limit_seconds, 3),
            )
            if budget
            else request.fleet.solver_time_limit_seconds,
        )
    except _NoSolution as error:
        if error.status != "limit" or budget is None:
            raise
        second = p.verified_fallback(energy, first.x)
        budget.reference_energy_witness_reused = True
    heat = np.clip(second.x[q], 0, source.max_power_w)
    trajectory = _trajectory(request, source, heat)
    minutes, violation = _minute_temperatures(request, source, heat, trajectory)
    if max(violation, _distribution_violation(request, source, heat, trajectory)) > _TOL:
        raise _NoSolution("error", "Reference rollout failed minute comfort verification")
    if _source_flow_violation_w(source, heat, trajectory) > _TOL:
        raise _NoSolution("error", "Reference rollout failed source-flow watt verification")
    return heat, trajectory, minutes, first.status == second.status == "optimal"


def _verified_fallback_origin(budget: _PlanningBudget) -> str:
    """Name the verified plan origin: the cost seed when one was used, else the reference."""
    return (
        "verified_cost_seed_fallback"
        if budget.candidate_cost_seed_used
        else "verified_reference_fallback"
    )


def _economic_seed(
    p: _Program,
    objective: FloatArray,
    request: ThermalPlanningInputs,
    source: _SourceHorizon,
    heat: list[int],
    thermal_integer_indices: list[int],
    budget: _PlanningBudget,
) -> None:
    """Improve a witness using a cost relaxation, without restricting the search.

    The relaxation uses the full joint objective, comfort and terminal bounds.
    Its heat trajectory proposes source integers only. A separate restricted
    solve repairs that proposal and keeps all storage decisions free. The
    original program is never narrowed, and unverified proposals are discarded.
    No clock times or mandatory OFF slots enter this heuristic.
    """
    if budget.remaining("joint_seed") > 12 and thermal_integer_indices:
        relaxed = copy.deepcopy(p)
        relaxed.budget = budget
        relaxed.stage = "joint_cost_relaxation"
        relaxed.integer = [0] * len(p.integer)
        relaxed.physical_validator = None
        relaxed.integer_hint_factory = None
        relaxed.reference_witness_validator = None
        previous_failed_stage = budget.failed_stage
        try:
            relaxation_started = time.monotonic()
            try:
                relaxed_result = relaxed.solve(
                    objective,
                    min(
                        6.0 if request.pattern_polish_seconds > 0 else 3.0,
                        budget.remaining(relaxed.stage) * 0.1,
                    ),
                )
            finally:
                if request.pattern_polish_seconds > 0:
                    # Time beyond the old 3 s cap is taken from the joint MILP, which seldom
                    # returns anything, so the total duration stays what it was.
                    budget.pattern_polish_extra_seconds += max(
                        0.0, time.monotonic() - relaxation_started - 3.0
                    )
            hint_heat = np.clip(relaxed_result.x[heat], 0, source.max_power_w)
            hint_heat = np.where(hint_heat > _TOL, np.maximum(hint_heat, source.min_power_w), 0.0)
            hint_heat[: len(source.fixed_heat_prefix_w)] = source.fixed_heat_prefix_w
            hints, *_ = _thermal_program(request, source, witness_heat=hint_heat)
            repair = copy.deepcopy(p)
            repair.budget = budget
            repair.stage = "joint_cost_seed"
            for i in thermal_integer_indices:
                repair.lower[i] = repair.upper[i] = hints.witness_values[i]
            repaired = repair.solve(objective, min(10.0, budget.remaining(repair.stage) * 0.45))
            witness = (
                np.array([p.witness_values[i] for i in range(len(p.lower))])
                if len(p.witness_values) == len(p.lower)
                else None
            )
            if (
                p.witness_violation(repaired.x) <= _TOL
                and p.physical_violation(repaired.x, "economic_seed") <= _TOL
                and (witness is None or objective @ repaired.x < objective @ witness)
            ):
                p.witness_values = dict(enumerate(repaired.x))
                budget.candidate_cost_seed_used = True
        except _NoSolution:
            budget.failed_stage = previous_failed_stage
            pass  # Keep the verified reference; all original choices remain free.


def _joint(
    request: ThermalPlanningInputs,
    source: _SourceHorizon,
    terminal: FloatArray,
    reference_minutes: FloatArray,
    reference_storage: FleetSolution,
    *,
    budget: _PlanningBudget | None = None,
    reference_heat: FloatArray | None = None,
) -> tuple[FleetSolution, FloatArray, FloatArray, _Result]:
    if any(request.candidate_pause_slots):
        reference_heat = None  # A warm reference is not a feasible candidate-pause witness.
    p, heat, states, _, steps, electric = _thermal_program(
        request, source, budget=budget, witness_heat=reference_heat
    )
    if request.candidate_pause_slots:
        for k, pause in enumerate(request.candidate_pause_slots):
            if pause:
                p.upper[heat[k]] = 0.0
    thermal_integer_indices = [i for i, flag in enumerate(p.integer) if flag]
    p.stage = "joint_candidate"
    fleet, n, hours = request.fleet, len(heat), 0.25
    for j in range(len(terminal)):
        p.eq({states[j][-1]: 1}, float(terminal[j]))
    # Match average warmth separately for each identical comfort/target band.
    # Heating an empty low-target period cannot compensate colder occupied time.
    groups: dict[tuple[float, float, float], list[int]] = {}
    for k, band in enumerate(request.comfort_bands):
        if k < len(source.fixed_heat_prefix_w):
            # Initial state and every prefix power are identical, hence so is
            # every recovery temperature. Repeating its already-fixed mean as
            # an inequality only introduces a numerically degenerate equality.
            continue
        if request.comfort_required and not request.comfort_required[k]:
            continue  # Empty periods do not owe reference-average warmth.
        key = (band.min_temperature_c, band.target_temperature_c, band.max_temperature_c)
        groups.setdefault(key, []).append(k)
    for indices in groups.values():
        terms: dict[int, float] = {}
        exog, reference_total = 0.0, 0.0
        for k in indices:
            reference_total += float(reference_minutes[k * 15 : (k + 1) * 15].sum())
            for a, b, e in _steps_at(source, k)[:15]:
                for j in range(len(states)):
                    terms[states[j][k]] = terms.get(states[j][k], 0) + float(a[0, j])
                terms[heat[k]] = terms.get(heat[k], 0) + float(b[0] * source.power_gain_scales[k])
                exog += float(e[0] * source.outdoor_c[k])
        p.row(terms, low=reference_total - exog)

    # These electrical constraints mirror optimize_fleet. Keep parity tests for
    # min charging, discrete levels, targets, reserve, export and power groups.
    imports = p.var(n, high=fleet.import_limit_w)
    exports = p.var(n, high=fleet.export_limit_w)
    curtailed = p.var(n, high=max(fleet.pv_w))
    grid_direction = p.var(n, high=1, binary=True)
    if reference_heat is not None:
        p.set_witness(imports, reference_storage.import_w)
        p.set_witness(exports, reference_storage.export_w)
        p.set_witness(curtailed, reference_storage.curtailed_pv_w)
        p.set_witness(
            grid_direction, [float(value <= _TOL) for value in reference_storage.export_w]
        )
    balances = []
    cost_parts: dict[int, float] = {}
    for k in range(n):
        if fleet.export_allowed is not None and not fleet.export_allowed[k]:
            p.upper[exports[k]] = 0
        if p.upper[exports[k]] == 0 or (
            not fleet.allow_battery_export
            and fleet.pv_w[k] <= fleet.load_w[k] + source.standby_power_w
        ):
            # With export prohibited or insufficient PV and no battery export,
            # direction=import is WLOG, including zero grid flow. EVs cannot V2G.
            p.lower[grid_direction[k]] = p.upper[grid_direction[k]] = 1
        p.upper[curtailed[k]] = fleet.pv_w[k]
        cost_parts[imports[k]] = fleet.import_price_eur_per_kwh[k] * hours / 1000
        cost_parts[exports[k]] = -fleet.export_price_eur_per_kwh[k] * hours / 1000
        p.row({imports[k]: 1, grid_direction[k]: -fleet.import_limit_w}, high=0)
        p.row({exports[k]: 1, grid_direction[k]: fleet.export_limit_w}, high=fleet.export_limit_w)
        balances.append({imports[k]: 1, exports[k]: -1, curtailed[k]: -1, electric[k]: -1})
    device_vars = {}
    for device in fleet.devices:
        charge = p.var(n, high=device.max_charge_power_w)
        discharge = p.var(n, high=device.max_discharge_power_w)
        energy = p.var(
            n + 1,
            high=device.capacity_wh if device.max_energy_wh is None else device.max_energy_wh,
        )
        direction = p.var(n, high=1, binary=True)
        witness_device = reference_storage.devices[device.device_id]
        if reference_heat is not None:
            p.set_witness(charge, witness_device.charge_w)
            p.set_witness(discharge, witness_device.discharge_w)
            p.set_witness(energy, witness_device.energy_wh)
            p.set_witness(direction, [float(value <= _TOL) for value in witness_device.discharge_w])
        p.lower[energy[0]] = p.upper[energy[0]] = device.initial_energy_wh
        p.lower[energy[device.target_slot or n]] = max(
            p.lower[energy[device.target_slot or n]], device.target_energy_wh
        )
        # Native reference optimization determines the budget. Only the joint
        # comparison fixes that same endpoint; it is never forced to initial SoC.
        p.eq({energy[-1]: 1}, reference_storage.devices[device.device_id].energy_wh[-1])
        cost_parts[energy[-1]] = -device.terminal_value_eur_per_kwh / 1000
        for k in range(n):
            cost_parts[discharge[k]] = hours * device.discharge_cost_eur_per_kwh / 1000
            balances[k][charge[k]], balances[k][discharge[k]] = -1, 1
            if device.available is not None and not device.available[k]:
                p.upper[charge[k]] = p.upper[discharge[k]] = 0
            p.row({charge[k]: 1, direction[k]: -device.max_charge_power_w}, high=0)
            if device.min_charge_power_w:
                charging = p.var(1, high=1, binary=True)[0]
                p.row({charge[k]: 1, charging: -device.max_charge_power_w}, high=0)
                p.row({charge[k]: 1, charging: -device.min_charge_power_w}, low=0)
                if reference_heat is not None:
                    p.set_witness([charging], [float(witness_device.charge_w[k] > _TOL)])
            p.row(
                {discharge[k]: 1, direction[k]: device.max_discharge_power_w},
                high=device.max_discharge_power_w,
            )
            p.eq(
                {
                    energy[k + 1]: 1,
                    energy[k]: -1,
                    charge[k]: -hours * device.charge_efficiency,
                    discharge[k]: hours / device.discharge_efficiency,
                },
                0,
            )
            p.row(
                {
                    energy[k]: 1,
                    discharge[k]: -hours / device.discharge_efficiency,
                    direction[k]: device.capacity_wh,
                },
                low=device.reserve_wh,
            )
            if device.charge_levels_w is not None:
                choices = p.var(len(device.charge_levels_w), high=1, binary=True)
                p.eq(dict.fromkeys(choices, 1), 1)
                p.eq(
                    {
                        charge[k]: 1,
                        **{i: -level for i, level in zip(choices, device.charge_levels_w)},
                    },
                    0,
                )
                if reference_heat is not None:
                    closest = int(
                        np.argmin(
                            abs(np.array(device.charge_levels_w) - witness_device.charge_w[k])
                        )
                    )
                    p.set_witness(choices, [float(j == closest) for j in range(len(choices))])
        device_vars[device.device_id] = (charge, discharge, energy)
    if not fleet.allow_battery_export:
        for k in range(n):
            terms = {electric[k]: -1}
            for device in fleet.devices:
                charge, discharge, _ = device_vars[device.device_id]
                if device.kind == "battery":
                    terms[discharge[k]] = 1
                    p.row(
                        {discharge[k]: 1, grid_direction[k]: -device.max_discharge_power_w}, high=0
                    )
                else:
                    terms[charge[k]] = -1
            p.row(terms, high=fleet.load_w[k])
    for group in fleet.power_groups:
        direction = p.var(n, high=1, binary=True)
        if reference_heat is not None:
            p.set_witness(
                direction,
                [
                    float(
                        sum(
                            reference_storage.devices[d.device_id].discharge_w[k]
                            for d in fleet.devices
                            if d.power_group == group.group_id
                        )
                        <= _TOL
                    )
                    for k in range(n)
                ],
            )
        members = [
            device_vars[d.device_id] for d in fleet.devices if d.power_group == group.group_id
        ]
        for k in range(n):
            p.row(
                {**{c[k]: 1 for c, _, _ in members}, direction[k]: -group.max_charge_power_w},
                high=0,
            )
            p.row(
                {**{s[k]: 1 for _, s, _ in members}, direction[k]: group.max_discharge_power_w},
                high=group.max_discharge_power_w,
            )
    for k in range(n):
        p.eq(balances[k], fleet.load_w[k] - fleet.pv_w[k])
    objective = np.zeros(len(p.lower))
    for index, value in cost_parts.items():
        objective[index] = value

    def physical_errors(values: FloatArray) -> dict[str, float]:
        powers = np.clip(values[heat], 0, source.max_power_w)
        trajectory = _trajectory(request, source, powers)
        minutes, comfort = _minute_temperatures(request, source, powers, trajectory)
        source_electric = _source_electric_power(source, powers)
        mean_error = 0.0
        for indices in groups.values():
            samples = np.concatenate([minutes[k * 15 : (k + 1) * 15] for k in indices])
            expected = np.concatenate([reference_minutes[k * 15 : (k + 1) * 15] for k in indices])
            mean_error = max(mean_error, float(expected.mean() - samples.mean()))
        balance = values[imports] - values[exports] - values[curtailed] - source_electric
        balance += np.array(fleet.pv_w) - np.array(fleet.load_w)
        energy_error, storage_terminal = 0.0, 0.0
        cost = (
            float(
                np.dot(fleet.import_price_eur_per_kwh, values[imports])
                - np.dot(fleet.export_price_eur_per_kwh, values[exports])
            )
            * hours
            / 1000
        )
        for device in fleet.devices:
            charge, discharge, energy = device_vars[device.device_id]
            balance += values[discharge] - values[charge]
            delta = hours * (
                device.charge_efficiency * values[charge]
                - values[discharge] / device.discharge_efficiency
            )
            energy_error = max(energy_error, float(np.max(np.abs(np.diff(values[energy]) - delta))))
            storage_terminal = max(
                storage_terminal,
                abs(
                    float(
                        values[energy[-1]]
                        - reference_storage.devices[device.device_id].energy_wh[-1]
                    )
                ),
            )
            cost += (
                float(np.sum(values[discharge])) * hours * device.discharge_cost_eur_per_kwh / 1000
            )
            cost -= float(values[energy[-1]]) * device.terminal_value_eur_per_kwh / 1000
        return {
            "minute_comfort_violation_c": comfort,
            "distribution_violation_c": _distribution_violation(
                request, source, powers, trajectory
            ),
            "source_flow_violation_w": _source_flow_violation_w(source, powers, trajectory),
            "thermal_terminal_error_c": float(np.max(np.abs(trajectory[-1] - terminal))),
            "band_mean_deficit_c": max(0.0, mean_error),
            "source_electric_error_w": float(np.max(np.abs(source_electric - values[electric]))),
            "grid_balance_error_w": float(np.max(np.abs(balance))),
            "storage_energy_error_wh": energy_error,
            "storage_terminal_error_wh": storage_terminal,
            "cost_reconstruction_error_eur": abs(float(objective @ values) - cost),
        }

    p.physical_validator = physical_errors
    if budget is not None:
        _economic_seed(p, objective, request, source, heat, thermal_integer_indices, budget)
        if request.pattern_polish_seconds > 0:
            _pattern_polish(
                p,
                objective,
                request,
                source,
                heat,
                thermal_integer_indices,
                budget,
                seconds=0.6 * request.pattern_polish_seconds,
            )
    # A timeout must not throw away cost optimization entirely. The verified
    # reference supplies a legal integer pattern; optimize its continuous heat
    # and storage decisions under the ORIGINAL cost and physical constraints.
    # This is a feasible heuristic, never a certificate of global optimality.
    if len(p.witness_values) == len(p.lower):
        p.integer_hint_factory = lambda: np.array(
            [p.witness_values[i] for i, flag in enumerate(p.integer) if flag]
        )
        p.reference_witness_validator = lambda values: max(physical_errors(values).values()) <= _TOL
    try:
        result = p.solve(
            objective,
            budget.allowance(
                fleet.solver_time_limit_seconds
                - min(
                    budget.pattern_polish_extra_seconds + 0.4 * request.pattern_polish_seconds,
                    0.5 * fleet.solver_time_limit_seconds,
                ),
                1,
            )
            if budget
            else fleet.solver_time_limit_seconds,
        )
    except _NoSolution as error:
        if (
            error.status not in ("limit", "infeasible", "error")
            or budget is None
            or len(p.witness_values) != len(p.lower)
        ):
            raise
        witness = np.array([p.witness_values[i] for i in range(len(p.lower))])
        result = p.verified_fallback(objective, witness)
        budget.candidate_plan_origin = _verified_fallback_origin(budget)
        if error.status != "limit":
            budget.candidate_numerical_reference_fallback = True
    if (
        budget is not None
        and len(p.witness_values) == len(p.lower)
        and not budget.candidate_plan_origin.endswith("_fallback")
    ):
        witness = np.array([p.witness_values[i] for i in range(len(p.lower))])
        if (
            p.witness_violation(witness) <= _TOL
            and p.physical_violation(witness, "reference_witness") <= _TOL
            and float(objective @ witness) + 1e-8 < result.objective
        ):
            result = p.verified_fallback(objective, witness)
            budget.candidate_plan_origin = _verified_fallback_origin(budget)
            budget.candidate_worse_incumbent_replaced = True
    if (
        budget is not None
        and request.pattern_polish_seconds > 0
        and len(p.witness_values) == len(p.lower)
    ):
        # Second pass on whatever won (MILP incumbent, seed or witness): the MILP result keeps
        # short blocks too. Same rule: kept only if the verified cost falls.
        polished = _pattern_polish(
            p,
            objective,
            request,
            source,
            heat,
            thermal_integer_indices,
            budget,
            seconds=0.4 * request.pattern_polish_seconds,
            x0=result.x,
            reserve=4.0,
        )
        if polished is not None:
            result = p.verified_fallback(objective, polished)
            budget.candidate_plan_origin = _verified_fallback_origin(budget)
    heat_values = np.clip(result.x[heat], 0, source.max_power_w)
    net_electric = _source_electric_power(source, heat_values)
    if np.max(np.abs(net_electric - result.x[electric])) > _TOL:
        raise _NoSolution("error", "Source electricity failed independent component verification")
    parameters = fleet.model_copy(
        deep=True, update={"load_w": (np.array(fleet.load_w) + net_electric).tolist()}
    )
    solution = FleetSolution(
        status=result.status,
        message="Joint thermal shadow; " + result.status,
        start_datetime=fleet.start_datetime,
        interval_seconds=900,
        objective_eur=result.objective,
        relative_gap=result.gap,
        grid_cost_eur=float(sum(objective[i] * result.x[i] for i in imports + exports)),
        import_w=result.x[imports].tolist(),
        export_w=result.x[exports].tolist(),
        curtailed_pv_w=result.x[curtailed].tolist(),
        parameters=parameters,
        devices={
            key: FleetDevicePlan(
                charge_w=result.x[c].tolist(),
                discharge_w=result.x[s].tolist(),
                energy_wh=result.x[e].tolist(),
            )
            for key, (c, s, e) in device_vars.items()
        },
    )
    return solution, heat_values, _trajectory(request, source, heat_values), result


def _optimize_problem(
    request: ThermalPlanningInputs, source: _SourceHorizon, *, budget: _PlanningBudget | None = None
) -> ThermalShadowSolution:
    """Share one public-entry budget, including all preparation and verification."""
    budget = budget or _PlanningBudget(request.planning_time_limit_seconds)
    try:
        budget.remaining("core_preparation")
        result = _optimize_problem_inner(request, source, budget)
    except _NoSolution as error:
        budget.failed_stage = budget.failed_stage or budget.stage
        result = ThermalShadowSolution(status=error.status, reason=str(error))
    return _finish_planning(result, budget)


def _optimize_problem_inner(
    request: ThermalPlanningInputs, source: _SourceHorizon, budget: _PlanningBudget
) -> ThermalShadowSolution:
    """Solve common schedule inputs using an explicit numerical source horizon.

    Four stages share the one planning deadline. The FLEET limit caps each stage;
    a presolve retry and fixed-integer cleanup consume only that stage's/global
    remaining budget. No input or actuator I/O occurs here.
    """
    request = _planning_inputs(request)
    for k, (before, after) in enumerate(zip(request.comfort_bands, request.comfort_bands[1:])):
        if max(before.min_temperature_c, after.min_temperature_c) > min(
            before.max_temperature_c, after.max_temperature_c
        ):
            transition = request.fleet.start_datetime + timedelta(seconds=(k + 1) * 900)
            raise _NoSolution(
                "infeasible",
                f"Disjoint hard comfort bands at slots {k} and {k + 1}, "
                f"transition {transition.isoformat()}: "
                f"[{before.min_temperature_c}, {before.max_temperature_c}] versus "
                f"[{after.min_temperature_c}, {after.max_temperature_c}] C. "
                "Allow an explicit preheat or warm-coasting transition; temperature cannot jump.",
            )
    n = len(request.comfort_bands)
    vectors = (source.outdoor_c, source.power_gain_scales, source.min_power_w, source.max_power_w)
    if any(len(vector) != n for vector in vectors) or len(source.steps) != 16:
        raise ValueError("Source horizon must match every fleet slot and minute transition")
    if (
        not np.isfinite(vectors).all()
        or np.any(np.array(source.power_gain_scales) <= 0)
        or np.any(np.array(source.min_power_w) < 0)
        or np.any(np.array(source.max_power_w) < np.array(source.min_power_w))
    ):
        raise ValueError("Invalid source coupling or power horizon")
    dimensions = 2 + len(source.additional_initial_temperatures_c)
    if (
        len(source.additional_state_names) != dimensions - 2
        or len(source.additional_temperature_bounds_c) != dimensions - 2
        or len(set(source.additional_state_names)) != dimensions - 2
        or not np.isfinite(source.additional_initial_temperatures_c).all()
        or (source.slot_steps and len(source.slot_steps) != n)
    ):
        raise ValueError("Distribution dimensions, state names and initial values must agree")
    for j, (minimum, maximum) in enumerate(source.additional_temperature_bounds_c):
        if not np.isfinite([minimum, maximum]).all() or minimum > maximum:
            raise ValueError("Distribution temperature bounds must be finite and ordered")
        if not minimum <= source.additional_initial_temperatures_c[j] <= maximum:
            raise ValueError("Initial distribution state lies outside declared temperature bounds")
    for k in range(n):
        steps = _steps_at(source, k)
        if len(steps) != 16:
            raise ValueError("Every distribution slot needs sixteen minute transitions")
        for a, b, e in steps:
            if (
                a.shape != (dimensions, dimensions)
                or b.shape != (dimensions,)
                or e.shape != (dimensions,)
            ):
                raise ValueError("Distribution transition dimensions must match every state")
            if not all(np.isfinite(value).all() for value in (a, b, e)):
                raise ValueError("Distribution transitions must be finite")
    node = source.source_temperature_state_index
    if node is not None:
        if not 2 <= node < dimensions or len(source.delivery_temperature_c) != n:
            raise ValueError("Supply-temperature constraint needs a supported water store")
        if not np.isfinite(source.delivery_temperature_c).all():
            raise ValueError("Supply temperatures must be finite")
    if source.source_flow_conductance_w_per_k and (
        node is None
        or len(source.source_flow_conductance_w_per_k) != n
        or not np.isfinite(source.source_flow_conductance_w_per_k).all()
        or min(source.source_flow_conductance_w_per_k) < 0
    ):
        raise ValueError("Source flow conductance must be a nonnegative known water horizon")
    if source.operating_points:
        if len(source.operating_points) != n:
            raise ValueError("Operating-point slices must match every fleet slot")
        for k, points in enumerate(source.operating_points):
            if (
                not points
                or not np.isfinite(points).all()
                or any(q <= 0 or p <= 0 for q, p in points)
            ):
                raise ValueError("Operating points must be finite positive Q/P pairs")
            if any(q1 <= q0 or p1 < p0 for (q0, p0), (q1, p1) in zip(points, points[1:])):
                raise ValueError(
                    "Operating points require increasing heat and nondecreasing electricity"
                )
            if source.min_power_w[k] < points[0][0] or source.max_power_w[k] > points[-1][0]:
                raise ValueError("Source input bounds exceed their operating-point support")
    extras = (
        source.standby_power_w,
        source.running_auxiliary_power_w,
        source.startup_energy_wh,
        source.startup_warmup_s,
    )
    if not np.isfinite(extras).all() or min(extras) < 0:
        raise ValueError("Source electricity additions must be finite and nonnegative")
    if (
        source.running_auxiliary_power_w or source.startup_energy_wh or source.startup_warmup_s
    ) and min(source.min_power_w) <= _TOL:
        raise _NoSolution(
            "unsupported",
            "Source state accounting needs numerically supported positive minimum power",
        )
    if source.total_electric_power_cap_w is not None and (
        not np.isfinite(source.total_electric_power_cap_w) or source.total_electric_power_cap_w <= 0
    ):
        raise ValueError("Source total electric power cap must be positive and finite")
    request = request.model_copy(
        update={
            "fleet": request.fleet.model_copy(
                deep=True,
                update={"start_datetime": request.fleet.start_datetime.astimezone(timezone.utc)},
            )
        }
    )
    original_import = request.fleet.import_limit_w
    original_export = request.fleet.export_limit_w
    # Mathematical flow bounds, not inferred physical grid connection ratings.
    # They preserve every feasible schedule and tighten both reference and joint
    # formulations when a caller uses permissive, e.g. 1 MW, network bounds.
    import_bound = min(
        original_import,
        max(request.fleet.load_w)
        + (
            max(points[-1][1] for points in source.operating_points)
            if source.operating_points
            else max(source.max_power_w)
        )
        + source.standby_power_w
        + source.running_auxiliary_power_w
        + source.startup_energy_wh / 0.25
        + (source.startup_warmup_s / 900.0)
        * (
            max(points[-1][1] for points in source.operating_points)
            if source.operating_points
            else max(source.max_power_w)
        )
        + sum(device.max_charge_power_w for device in request.fleet.devices),
    )
    export_bound = min(
        original_export,
        max(request.fleet.pv_w)
        + sum(device.max_discharge_power_w for device in request.fleet.devices),
    )
    request = request.model_copy(
        update={
            "fleet": request.fleet.model_copy(
                deep=True, update={"import_limit_w": import_bound, "export_limit_w": export_bound}
            )
        }
    )
    start, state = request.fleet.start_datetime, request.state
    if state.estimated_at != start or start < request.issued_at:
        return ThermalShadowSolution(
            status="unsupported", reason="State must describe the prospective fleet start"
        )
    if (
        state.based_on_observed_at > request.issued_at
        or (request.issued_at - state.based_on_observed_at).total_seconds()
        > request.measurement_max_age_seconds
    ):
        return ThermalShadowSolution(
            status="unsupported", reason="Observation is stale or lies after issue time"
        )
    if request.issued_at + timedelta(seconds=request.plan_ttl_seconds) <= start:
        return ThermalShadowSolution(
            status="unsupported", reason="Plan lease expires before the horizon starts"
        )
    desired_bands = request.comfort_bands
    first = desired_bands[0]
    if (
        state.room_temperature_c > first.max_temperature_c
        and (request.recovery is None or not request.recovery.allow_passive_cooldown)
    ) or (state.room_temperature_c < first.min_temperature_c and request.recovery is None):
        return ThermalShadowSolution(
            status="infeasible",
            reason="Initial room is outside comfort; explicit recovery bands are required",
        )
    request, source, recovery = _prepare_recovery(request, source)
    budget.remaining("recovery_preparation")
    if recovery is not None and not recovery.completed:
        return ThermalShadowSolution(
            status="infeasible",
            reason="Common recovery did not reach a feasible comfort boundary within its duration",
            recovery=recovery,
        )
    started = time.monotonic()
    try:
        reference_heat, reference_states, reference_minutes, reference_optimal = _reference(
            request, source, budget=budget
        )
        reference_request = request.fleet.model_copy(
            deep=True,
            update={
                "load_w": (
                    np.array(request.fleet.load_w) + _source_electric_power(source, reference_heat)
                ).tolist()
            },
        )
        native_started = time.monotonic()
        reference_request.solver_time_limit_seconds = min(
            reference_request.solver_time_limit_seconds,
            budget.remaining("native_fleet_reference")
            / (4 if len(request.comfort_bands) > 96 else 2),
            10.0
            if len(request.comfort_bands) > 96
            else reference_request.solver_time_limit_seconds,
        )
        warm_start = _load_warm_start(reference_request)
        reference = (
            optimize_fleet(reference_request)
            if warm_start is None
            else optimize_fleet(reference_request, start=warm_start)
        )
        budget.native_fleet_reference_start_used = getattr(reference, "start_used", False)
        budget.diagnostics.append(
            ThermalSolveDiagnostics(
                name="native_fleet_reference",
                status=reference.status,
                elapsed_seconds=time.monotonic() - native_started,
                solver_calls=1,
                presolve_retries=0,
                objective=reference.objective_eur,
            )
        )
        reference = _native_fleet_reference_fallback(reference_request, reference, budget)
        if not budget.native_fleet_reference_idle_fallback:
            _store_warm_start(reference_request, reference)
        budget.remaining("native_fleet_verification")
        if reference.status not in ("optimal", "feasible") or reference.objective_eur is None:
            return ThermalShadowSolution(
                status=reference.status,
                reason="Electrical reference has no verified schedule",
                reference=reference,
            )
        candidate, candidate_heat, candidate_states, result = _joint(
            request,
            source,
            reference_states[-1],
            reference_minutes,
            reference,
            budget=budget,
            reference_heat=reference_heat,
        )
        budget.remaining("candidate_verification")
        minutes, comfort_violation = _minute_temperatures(
            request, source, candidate_heat, candidate_states
        )
        terminal_error = float(np.max(np.abs(candidate_states[-1] - reference_states[-1])))
        distribution_violation = _distribution_violation(
            request, source, candidate_heat, candidate_states
        )
        reference_flow_w = _source_flow_violation_w(source, reference_heat, reference_states)
        candidate_flow_w = _source_flow_violation_w(source, candidate_heat, candidate_states)
        if max(reference_flow_w, candidate_flow_w) > _TOL:
            raise _NoSolution(
                "error", "Source rollout failed absolute source-flow watt verification"
            )
        if max(comfort_violation, terminal_error, distribution_violation) > _TOL:
            raise _NoSolution(
                "error", "Candidate rollout failed comfort or thermal terminal verification"
            )
        reference_gap = (
            abs(reference.objective_eur) * reference.relative_gap
            if reference.relative_gap is not None
            else (0.0 if reference.status == "optimal" else None)
        )
        candidate_gap = (
            abs(result.objective - result.lower_bound) if result.lower_bound is not None else None
        )
        qualified = (
            reference_optimal
            and reference_gap is not None
            and candidate_gap is not None
            and reference_gap + candidate_gap <= request.max_absolute_gap_eur
            and len(source.fixed_heat_prefix_w) < n
        )
        endpoints_match = all(
            abs(
                reference.devices[d.device_id].energy_wh[-1]
                - candidate.devices[d.device_id].energy_wh[-1]
            )
            <= _TOL
            for d in request.fleet.devices
        )
        intervals = []
        for k, band in enumerate(desired_bands):
            # A predicted average is an observation, not a control objective.
            # Keep the requested comfort target; explicitly planned storage heat
            # may require a higher endpoint. Zero-power pauses remain controlled
            # by the existing source-power permission, never by a low proxy target.
            target = float(
                np.clip(
                    max(band.target_temperature_c, candidate_states[k + 1, 0])
                    if candidate_heat[k] > _TOL
                    else band.target_temperature_c,
                    band.min_temperature_c,
                    band.max_temperature_c,
                )
            )
            if recovery is not None and k < len(source.fixed_heat_prefix_w):
                # Ask the local controller for the recovery endpoint that the
                # model actually assumes, including unavoidable minimum output.
                target = (
                    float(
                        np.clip(
                            # With a rate limit the controller is asked for the rising
                            # endpoint the model assumes, not the final comfort target.
                            candidate_states[k + 1, 0]
                            if recovery.heating_rate_kelvin_per_hour is not None
                            else max(band.target_temperature_c, candidate_states[k + 1, 0]),
                            band.min_temperature_c,
                            band.max_temperature_c,
                        )
                    )
                    if recovery.direction == "heating"
                    else band.target_temperature_c
                )
            intervals.append(
                TemperatureInterval(
                    start_datetime=start + timedelta(seconds=k * 900),
                    end_datetime=start + timedelta(seconds=(k + 1) * 900),
                    min_temperature_c=band.min_temperature_c,
                    target_temperature_c=target,
                    max_temperature_c=band.max_temperature_c,
                )
            )
        horizon_end = start + timedelta(seconds=900 * len(intervals))
        plan = ThermalPlan(
            zone_id=request.zone_id,
            plan_id="thermal-shadow-" + source.fingerprint[:20],
            source="EOS thermal shadow",
            revision=request.revision,
            basis_control_revision=request.basis_control_revision,
            issued_at=request.issued_at,
            valid_until=min(
                horizon_end, request.issued_at + timedelta(seconds=request.plan_ttl_seconds)
            ),
            intervals=tuple(intervals),
        )
        return ThermalShadowSolution(
            status=result.status,
            reason=(
                (
                    "Conditional model comparison after identical passive cooldown"
                    if recovery.direction == "passive_cooldown"
                    else "Conditional model comparison after identical target-limited cold recovery"
                )
                if recovery is not None
                else "Conditional model comparison"
            )
            if qualified
            else "Solver uncertainty exceeds comparison budget",
            reference=reference,
            candidate=candidate,
            comparison_qualified=qualified,
            reference_heat_power_w=tuple(reference_heat),
            candidate_heat_power_w=tuple(candidate_heat),
            reference_source_electric_power_w=tuple(_source_electric_power(source, reference_heat)),
            candidate_source_electric_power_w=tuple(_source_electric_power(source, candidate_heat)),
            reference_room_temperature_c=tuple(reference_states[:, 0]),
            candidate_room_temperature_c=tuple(candidate_states[:, 0]),
            reference_mass_temperature_c=tuple(reference_states[:, 1]),
            candidate_mass_temperature_c=tuple(candidate_states[:, 1]),
            reference_distribution_temperatures_c=_distribution_trajectories(
                source, reference_states
            ),
            candidate_distribution_temperatures_c=_distribution_trajectories(
                source, candidate_states
            ),
            plan=plan,
            savings_objective_eur=reference.objective_eur - result.objective if qualified else None,
            savings_cash_eur=reference.grid_cost_eur - candidate.grid_cost_eur
            if qualified
            and endpoints_match
            and reference.grid_cost_eur is not None
            and candidate.grid_cost_eur is not None
            else None,
            objective_lower_bound_eur=result.lower_bound,
            absolute_gap_eur=candidate_gap,
            reference_absolute_gap_eur=reference_gap,
            recovery=recovery,
            checks={
                "max_constraint_violation": result.violation,
                "max_minute_comfort_violation_c": comfort_violation,
                "max_thermal_terminal_error_c": terminal_error,
                "max_distribution_minute_constraint_violation_c": distribution_violation,
                "reference_max_source_flow_violation_w": reference_flow_w,
                "candidate_max_source_flow_violation_w": candidate_flow_w,
                "all_distribution_terminal_states_match": terminal_error <= _TOL,
                "reference_heat_optimization_complete": reference_optimal,
                "storage_terminal_states_match": endpoints_match,
                "storage_terminal_budget_from_native_reference": True,
                "native_tracking_validated": False,
                "measured_bill_savings_proven": False,
                "energy_reduction_required": False,
                "elapsed_seconds": time.monotonic() - started,
                "original_import_limit_w": original_import,
                "original_export_limit_w": original_export,
                "derived_import_bound_w": import_bound,
                "derived_export_bound_w": export_bound,
                "grid_bounds_are_mathematical_not_physical": True,
                "model_input_kind": source.model_kind,
                "source_power_input_kind": "thermal" if source.operating_points else "electrical",
                "recovery_active": recovery is not None,
                **(
                    {
                        "recovery_heating_rate_kelvin_per_hour": recovery.heating_rate_kelvin_per_hour,
                        "recovery_heating_rate_raised": recovery.heating_rate_raised,
                    }
                    if recovery is not None and recovery.heating_rate_kelvin_per_hour is not None
                    else {}
                ),
                "recovery_prefix_identical": bool(
                    np.max(
                        np.abs(
                            candidate_heat[: len(source.fixed_heat_prefix_w)]
                            - reference_heat[: len(source.fixed_heat_prefix_w)]
                        ),
                        initial=0,
                    )
                    <= _TOL
                ),
                "post_recovery_comparison_slots": float(n - len(source.fixed_heat_prefix_w)),
                "passive_minute_maximum_bound_used": _passive_upper_bound(
                    source, _initial_temperatures(request, source)
                )
                is not None,
            },
        )
    except _NoSolution as error:
        budget.failed_stage = budget.failed_stage or budget.stage
        return ThermalShadowSolution(status=error.status, reason=str(error), recovery=recovery)


def optimize_thermal_shadow(request: ThermalShadowRequest) -> ThermalShadowSolution:
    """Run the unchanged legacy effective-electrical RC2 shadow contract.

    The generic heat-pump adapter uses a separate thermal building DTO; this
    function never accepts or multiplies a separate source COP.
    """
    budget = _PlanningBudget(request.planning_time_limit_seconds)
    try:
        request = ThermalShadowRequest.model_validate(request.model_dump())
        n = len(request.comfort_bands)
        source = _SourceHorizon(
            outdoor_c=request.outdoor_temperature_c,
            power_gain_scales=(1.0,) * n,
            min_power_w=(request.min_electric_power_w,) * n,
            max_power_w=(request.max_electric_power_w,) * n,
            steps=tuple(_dynamics(request.model, minute * 60) for minute in range(16)),
            fingerprint=hashlib.sha256(
                request.model_dump_json(exclude={"pattern_polish_seconds"}).encode()
            ).hexdigest(),
            model_kind="legacy_effective_electrical",
        )
        budget.remaining("legacy_source_preparation")
        result = _optimize_problem(request, source, budget=budget)
    except _NoSolution as error:
        result = ThermalShadowSolution(status=error.status, reason=str(error))
    return _finish_planning(result, budget)
