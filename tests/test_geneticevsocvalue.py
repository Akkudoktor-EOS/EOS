"""Value of EV energy above the target SoC (``soc_value_steps``) for GENETIC.

Without a value GENETIC charges an EV only up to ``min_soc_percentage``:
energy beyond it costs money and is worth nothing in the fitness, and a
vehicle that already reached its target is not planned at all. With
``soc_value_steps`` every kWh stored between the target and a step's SoC (at
most ``max_soc_percentage``) is credited with the step's amount at the moment
the target is checked (the deadline, or else the end of the control horizon).
The optimizer then charges beyond the target wherever that costs less than the
value - and never at the expense of the target, which keeps its penalty.
"""

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from pydantic import ValidationError

from akkudoktoreos.config.config import ConfigEOS, SettingsEOS
from akkudoktoreos.core.coreabc import get_ems
from akkudoktoreos.core.emplan import FRBCInstruction
from akkudoktoreos.devices.devicesabc import validate_soc_value_steps
from akkudoktoreos.devices.genetic.battery import ElectricVehicleParameters
from akkudoktoreos.devices.settings.batterysettings import BatteriesCommonSettings
from akkudoktoreos.optimization.genetic import configrequest
from akkudoktoreos.optimization.genetic.configrequest import ConfigOptimizationRequest
from akkudoktoreos.optimization.genetic.genetic import GeneticOptimization
from akkudoktoreos.optimization.genetic.geneticparams import (
    GeneticOptimizationParameters,
)
from akkudoktoreos.optimization.genetic.geneticsolution import GeneticSolution
from akkudoktoreos.utils.datetimeutil import to_datetime

DIR_TESTDATA = Path(__file__).parent / "testdata" / "genetic"

HOURS = 6
SLOTS = HOURS * 4
EXPENSIVE = 0.0004  # 0.40 per kWh
CHEAP = 0.0001  # 0.10 per kWh
CHEAP_SLOTS = list(range(4, 12))  # 8 slots, 1 kWh stored per slot at full power
START = "2026-10-29T00:00:00"


# ---------------------------------------------------------------------------
# Settings, validation and request plumbing
# ---------------------------------------------------------------------------


def test_soc_value_defaults_to_none_everywhere() -> None:
    settings = BatteriesCommonSettings(device_id="car")
    assert settings.soc_value_steps is None
    assert settings.to_genetic_ev_bat_param().soc_value_steps is None
    params = ElectricVehicleParameters(device_id="car", capacity_wh=10000)
    assert params.soc_value_steps is None


def test_soc_value_reaches_the_genetic_ev_parameters_only() -> None:
    settings = BatteriesCommonSettings.model_validate(
        {"device_id": "car", "capacity_wh": 10000, "soc_value_steps": [[60, 0.3], [80, 0.2]]}
    )
    params = settings.to_genetic_ev_bat_param()
    assert params.soc_value_steps == [(60.0, 0.3), (80.0, 0.2)]
    # Not the stationary battery, not GENETIC0.
    assert "soc_value_steps" not in settings.to_genetic_pv_bat_param().model_dump()
    assert "soc_value_steps" not in settings.to_genetic0_ev_bat_param().model_dump()


def test_soc_value_round_trips_through_settings_and_configuration(config_eos: ConfigEOS) -> None:
    """Serialised and loaded again, device settings and configuration keep the steps."""
    steps = [[60, 0.3], [80, 0.2]]
    settings = BatteriesCommonSettings.model_validate(
        {"device_id": "car", "capacity_wh": 10000, "soc_value_steps": steps}
    )
    reloaded = BatteriesCommonSettings.model_validate_json(settings.model_dump_json())
    assert reloaded.soc_value_steps == [(60.0, 0.3), (80.0, 0.2)]

    config_eos.merge_settings_from_dict(
        {
            "devices": {
                "max_electric_vehicles": 1,
                "electric_vehicles": {"car": {"capacity_wh": 10000, "soc_value_steps": steps}},
            }
        }
    )
    dumped = SettingsEOS.model_validate(config_eos.model_dump()).model_dump_json()
    config_reloaded = SettingsEOS.model_validate_json(dumped)
    assert config_reloaded.devices is not None
    assert config_reloaded.devices.electric_vehicles is not None
    assert config_reloaded.devices.electric_vehicles["car"].soc_value_steps == [
        (60.0, 0.3),
        (80.0, 0.2),
    ]


def test_soc_value_in_direct_genetic_parameters_survives_json() -> None:
    params = parameters(soc_value_steps=[[80, 0.3]])
    assert params.ev is not None
    assert params.ev.soc_value_steps == [(80.0, 0.3)]
    restored = GeneticOptimizationParameters.model_validate_json(params.model_dump_json())
    assert restored.ev is not None
    assert restored.ev.soc_value_steps == [(80.0, 0.3)]


@pytest.mark.parametrize(
    "steps",
    [
        [[80, -0.1]],  # negative amount
        [[0, 0.3]],  # SoC must be above 0 %
        [[-10, 0.3]],
        [[101, 0.3]],  # SoC above 100 %
        [[80, 0.2], [60, 0.3]],  # SoC not ascending
        [[60, 0.3], [60, 0.2]],  # SoC not strictly ascending
        [[60, 0.2], [80, 0.3]],  # amount rising with the SoC
        [[80, float("nan")]],
        [[80, float("inf")]],
        [[float("nan"), 0.3]],
        [[float("inf"), 0.3]],
        [[80]],  # not a pair
        [[80, 0.3, 1.0]],
        [["many", 0.3]],
        [[80, None]],
    ],
)
def test_invalid_soc_value_steps_are_rejected(steps: Any) -> None:
    for model in (ElectricVehicleParameters, BatteriesCommonSettings):
        with pytest.raises(ValidationError):
            model.model_validate(
                {"device_id": "car", "capacity_wh": 10000, "soc_value_steps": steps}
            )


def test_value_steps_whose_credit_overflows_are_rejected() -> None:
    """Finite amounts that make the credit for the whole vehicle infinite would
    turn every fitness into -inf."""
    with pytest.raises(ValidationError):
        ElectricVehicleParameters(
            device_id="car", capacity_wh=60000, soc_value_steps=[(80.0, 1e308)]
        )
    settings = BatteriesCommonSettings(
        device_id="car", capacity_wh=60000, soc_value_steps=[(80.0, 1e308)]
    )
    with pytest.raises(ValidationError):
        settings.to_genetic_ev_bat_param()


def test_empty_soc_value_steps_mean_no_value() -> None:
    for model in (ElectricVehicleParameters, BatteriesCommonSettings):
        params = model.model_validate(
            {"device_id": "car", "capacity_wh": 10000, "soc_value_steps": []}
        )
        assert params.soc_value_steps is None


def test_validator_accepts_equal_amounts_and_returns_floats() -> None:
    steps = validate_soc_value_steps([(50, 1), (100, 1)])
    assert steps == [(50.0, 1.0), (100.0, 1.0)]
    assert all(type(value) is float for step in steps for value in step)
    assert validate_soc_value_steps([(100, 0)]) == [(100.0, 0.0)]
    assert validate_soc_value_steps(None) is None


@pytest.mark.asyncio
async def test_config_request_carries_the_configured_value(
    config_eos: ConfigEOS, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POST /v1/optimize takes the EV from configuration, including its value steps."""
    config_eos.merge_settings_from_dict(
        {
            "prediction": {"hours": 24},
            "optimization": {
                "algorithm": "GENETIC",
                "genetic": {
                    "interval_sec": 900,
                    "horizon_hours": 1,
                    "tail_horizon_hours": 0,
                    "terminal_value_mode": "FIXED",
                },
            },
            "devices": {
                "max_batteries": 0,
                "max_electric_vehicles": 1,
                "max_inverters": 1,
                "max_home_appliances": 0,
                "batteries": {},
                "electric_vehicles": {
                    "car": {
                        "capacity_wh": 40000,
                        "min_soc_percentage": 50,
                        "max_soc_percentage": 80,
                        "soc_value_steps": [[65, 0.31], [80, 0.27]],
                    }
                },
                "inverters": {"inverter": {"max_power_w": 10000}},
                "home_appliances": {},
            },
        }
    )
    start = to_datetime("2026-09-12T10:15:00Z", in_timezone="UTC")
    ems = SimpleNamespace(
        start_datetime=start, observation_datetime=start, genetic_solution=lambda: None
    )
    monkeypatch.setattr(configrequest, "get_ems", lambda: ems)
    monkeypatch.setattr(
        ConfigOptimizationRequest,
        "measurement",
        Mock(key_to_lists=AsyncMock(return_value=([], []))),
    )
    request = ConfigOptimizationRequest.model_validate(
        {
            "soc": {"car": 55},
            "forecasts": {
                "pv_forecast_wh": [0.0] * 96,
                "total_load": [0.0] * 96,
                "electricity_price_per_wh": [0.0003] * 96,
                "feed_in_tariff_per_wh": [0.0] * 96,
            },
        }
    )
    params = await request.resolve()
    assert params.ev is not None
    assert params.ev.soc_value_steps == [(65.0, 0.31), (80.0, 0.27)]
    assert params.ev.initial_soc_percentage == 55


@pytest.mark.asyncio
async def test_rest_configuration_sets_and_rejects_value_steps(
    config_eos: ConfigEOS, monkeypatch: pytest.MonkeyPatch
) -> None:
    from akkudoktoreos.server import eos

    config_eos.merge_settings_from_dict(
        {
            "devices": {
                "max_electric_vehicles": 1,
                "electric_vehicles": {"ev": {"capacity_wh": 10000}},
            }
        }
    )
    monkeypatch.setattr(eos, "get_config", lambda: config_eos)
    path = "/v1/config/devices/electric_vehicles/ev/soc_value_steps"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=eos.app), base_url="http://test"
    ) as client:
        updated = await client.put(path, json=[[70, 0.25], [90, 0.2]])
        assert updated.status_code == 200, updated.text
        assert config_eos.devices.electric_vehicles is not None
        assert config_eos.devices.electric_vehicles["ev"].soc_value_steps == [
            (70.0, 0.25),
            (90.0, 0.2),
        ]
        rejected = await client.put(path, json=[[70, 0.2], [90, 0.25]])
        assert rejected.status_code != 200
    assert config_eos.devices.electric_vehicles["ev"].soc_value_steps == [
        (70.0, 0.25),
        (90.0, 0.2),
    ]


# ---------------------------------------------------------------------------
# Small real runs: 6 h on a 15-minute grid, 10 kWh vehicle, 4 kW charger
# ---------------------------------------------------------------------------


def configure(config: ConfigEOS, *, hours: int = HOURS, interval: int = 900) -> None:
    config.merge_settings_from_dict(
        {
            "prediction": {"hours": 48},
            "optimization": {
                "algorithm": "GENETIC",
                "genetic": {
                    "horizon_hours": hours,
                    "tail_horizon_hours": 0,
                    "interval_sec": interval,
                    "terminal_value_mode": "FIXED",
                },
            },
            "feedintariff": {"direct_marketing_enabled": False},
        }
    )
    get_ems(init=True).set_start_datetime(to_datetime(START))


def parameters(
    prices: Optional[list[float]] = None, pv: Optional[list[float]] = None, **ev: Any
) -> GeneticOptimizationParameters:
    """Expensive grid except for eight cheap slots; 1 kWh stored per full-power slot."""
    if prices is None:
        prices = [EXPENSIVE] * SLOTS
        for slot in CHEAP_SLOTS:
            prices[slot] = CHEAP
    return GeneticOptimizationParameters.model_validate(
        {
            "ems": {
                "pv_prognose_wh": pv if pv is not None else [0.0] * SLOTS,
                "gesamtlast": [0.0] * SLOTS,
                "strompreis_euro_pro_wh": prices,
                "einspeiseverguetung_euro_pro_wh": [0.0] * SLOTS,
                "preis_euro_pro_wh_akku": 0.0,
            },
            "inverter": {"device_id": "inv", "max_power_wh": 10000},
            "forecast_interval_seconds": 900,
            "pv_battery": None,
            "ev": {
                "device_id": "car",
                "capacity_wh": 10000,
                "initial_soc_percentage": 50,
                "min_soc_percentage": 40,
                "max_soc_percentage": 80,
                "charging_efficiency": 1.0,
                "max_charge_power_w": 4000,
                "charge_rates": [0.0, 0.5, 1.0],
                **ev,
            },
        }
    )


def run(
    params: GeneticOptimizationParameters, *, ngen: int = 1, individuals: int = 12, seed: int = 7
) -> tuple[GeneticOptimization, GeneticSolution]:
    optimizer = GeneticOptimization(fixed_seed=seed)
    solution = optimizer.optimize_ems(params, ngen=ngen, individuals=individuals)
    return optimizer, solution


def charged_slots(solution: GeneticSolution) -> list[int]:
    rates = solution.ev_charge_hours_float or []
    return [slot for slot, rate in enumerate(rates) if rate > 0.0]


def final_soc(solution: GeneticSolution) -> float:
    assert solution.ev_obj is not None
    return solution.ev_obj.soc_wh / solution.ev_obj.capacity_wh * 100.0


def test_credit_counts_only_the_energy_between_target_and_steps(config_eos: ConfigEOS) -> None:
    configure(config_eos)
    optimizer, _ = run(parameters(max_soc_percentage=100, soc_value_steps=[[60, 0.3], [80, 0.2]]))
    credit = optimizer._ev_soc_value_credit
    # 10 kWh vehicle, target 40 %: 10 % = 1 kWh.
    assert credit(30.0) == 0.0
    assert credit(40.0) == 0.0
    assert credit(50.0) == pytest.approx(1.0 * 0.3)
    assert credit(70.0) == pytest.approx(2.0 * 0.3 + 1.0 * 0.2)
    assert credit(80.0) == pytest.approx(2.0 * 0.3 + 2.0 * 0.2)
    # Above the last step nothing more is credited.
    assert credit(95.0) == pytest.approx(2.0 * 0.3 + 2.0 * 0.2)


def test_credit_ends_at_the_maximum_soc(config_eos: ConfigEOS) -> None:
    configure(config_eos)
    optimizer, _ = run(parameters(max_soc_percentage=70, soc_value_steps=[[60, 0.3], [80, 0.2]]))
    credit = optimizer._ev_soc_value_credit
    assert credit(70.0) == pytest.approx(2.0 * 0.3 + 1.0 * 0.2)
    # An initial SoC above the maximum earns nothing beyond it.
    assert credit(90.0) == pytest.approx(credit(70.0))
    assert optimizer._ev_soc_value_per_wh(69.0) == pytest.approx(0.2 / 1000.0)
    assert optimizer._ev_soc_value_per_wh(70.0) == 0.0


def test_steps_below_the_target_earn_nothing(config_eos: ConfigEOS) -> None:
    configure(config_eos)
    optimizer, _ = run(
        parameters(
            initial_soc_percentage=50,
            min_soc_percentage=65,
            soc_value_steps=[[60, 0.3], [80, 0.2]],
        )
    )
    credit = optimizer._ev_soc_value_credit
    assert credit(60.0) == 0.0
    assert credit(65.0) == 0.0
    assert credit(70.0) == pytest.approx(0.5 * 0.2)
    # Below the target the next kWh is worth only the target penalty.
    assert optimizer._ev_soc_value_per_wh(50.0) == pytest.approx(0.2 / 1000.0)


def test_without_value_an_ev_at_its_target_is_not_charged(config_eos: ConfigEOS) -> None:
    """Reference (unchanged behaviour): nothing above the target, even in cheap slots."""
    configure(config_eos)
    optimizer, solution = run(parameters())
    assert optimizer.optimize_ev is False
    assert charged_slots(solution) == []


@pytest.mark.parametrize(
    ("ev", "planned"),
    [
        ({"soc_value_steps": [[80, 0.3]]}, True),
        # At its maximum: nothing to gain.
        ({"initial_soc_percentage": 80, "soc_value_steps": [[80, 0.3]]}, False),
        # Only worthless steps.
        ({"soc_value_steps": [[60, 0.0], [80, 0.0]]}, False),
        # Every valued step lies below the current SoC.
        ({"soc_value_steps": [[45, 0.3], [80, 0.0]]}, False),
        # Every valued step lies at or below the target.
        ({"min_soc_percentage": 60, "initial_soc_percentage": 60,
          "soc_value_steps": [[60, 0.3]]}, False),
        # A step above the maximum still values the energy up to the maximum ...
        ({"soc_value_steps": [[100, 0.3]]}, True),
        # ... but not beyond it.
        ({"initial_soc_percentage": 80, "soc_value_steps": [[100, 0.3]]}, False),
    ],
)
def test_a_vehicle_at_its_target_is_planned_only_with_value_to_gain(
    config_eos: ConfigEOS, ev: dict[str, Any], planned: bool
) -> None:
    configure(config_eos)
    optimizer, _ = run(parameters(**ev))
    assert optimizer.optimize_ev is planned


def test_value_above_the_price_charges_to_the_maximum_in_the_cheap_slots(
    config_eos: ConfigEOS,
) -> None:
    configure(config_eos)
    optimizer, solution = run(parameters(soc_value_steps=[[80, 0.3]]), ngen=20, individuals=30)
    assert optimizer.optimize_ev is True
    # 50 -> 80 % = 3 kWh = three full-power cheap slots, nothing in expensive ones.
    assert set(charged_slots(solution)) <= set(CHEAP_SLOTS)
    assert final_soc(solution) == pytest.approx(80.0)
    assert max(solution.result.ev_soc_per_hour) <= 80.0 + 1e-9
    assert solution.result.total_costs == pytest.approx(3000 * CHEAP)


def test_value_below_the_price_does_not_charge(config_eos: ConfigEOS) -> None:
    configure(config_eos)
    _, solution = run(parameters(soc_value_steps=[[80, 0.05]]), ngen=20, individuals=30)
    assert charged_slots(solution) == []
    assert final_soc(solution) == pytest.approx(50.0)


def test_steps_stop_where_the_value_falls_below_the_price(config_eos: ConfigEOS) -> None:
    """First step worth charging at 0.10, the second not: the plan stops at 60 %."""
    configure(config_eos)
    _, solution = run(parameters(soc_value_steps=[[60, 0.3], [80, 0.05]]), ngen=20, individuals=30)
    assert set(charged_slots(solution)) <= set(CHEAP_SLOTS)
    assert final_soc(solution) == pytest.approx(60.0)


def test_the_target_stays_a_target_with_a_value(config_eos: ConfigEOS) -> None:
    """Deadline before the cheap slots: the target is charged at the expensive price,
    and the value of energy beyond it (checked at the deadline) does not pay there."""
    configure(config_eos)
    params = parameters(
        initial_soc_percentage=20,
        min_soc_percentage=40,
        min_soc_deadline_datetime="2026-10-29T01:00:00",
        soc_value_steps=[[80, 0.3]],
    )
    optimizer, solution = run(params, ngen=20, individuals=30)
    deadline = optimizer._ev_soc_deadline_slot
    assert deadline == 4
    assert solution.result.ev_soc_per_hour[deadline] == pytest.approx(40.0)
    # Charging after the departure earns nothing: no charging in the cheap slots.
    assert all(slot < deadline for slot in charged_slots(solution))


def test_value_is_credited_at_the_deadline_not_after_it(config_eos: ConfigEOS) -> None:
    """Cheap slots before the deadline are used up to the maximum; the even cheaper
    slots after the departure are not, although they would store more energy."""
    configure(config_eos)
    prices = [EXPENSIVE] * SLOTS
    for slot in CHEAP_SLOTS:
        prices[slot] = CHEAP
    for slot in range(16, SLOTS):
        prices[slot] = CHEAP / 10.0
    params = parameters(
        prices=prices,
        min_soc_deadline_datetime="2026-10-29T03:00:00",
        soc_value_steps=[[80, 0.3]],
    )
    optimizer, solution = run(params, ngen=20, individuals=30)
    deadline = optimizer._ev_soc_deadline_slot
    assert deadline == 12
    assert solution.result.ev_soc_per_hour[deadline] == pytest.approx(80.0)
    assert set(charged_slots(solution)) <= set(CHEAP_SLOTS)


def test_value_respects_the_earliest_charge_start(config_eos: ConfigEOS) -> None:
    """Arrival at 02:00: only the cheap slots 8-11 are left before the vehicle is at
    its maximum - two of them are needed with 2 kWh missing."""
    configure(config_eos)
    params = parameters(
        initial_soc_percentage=60,
        charge_start_datetime="2026-10-29T02:00:00",
        soc_value_steps=[[80, 0.3]],
    )
    optimizer, solution = run(params, ngen=20, individuals=30)
    assert optimizer._ev_first_charge_slot == 8
    assert set(charged_slots(solution)) <= {8, 9, 10, 11}
    assert final_soc(solution) == pytest.approx(80.0)


def test_value_counts_only_the_energy_charged_before_a_departure_inside_a_slot(
    config_eos: ConfigEOS,
) -> None:
    """Hourly grid, arrival 01:07, departure 02:50: only 02:00-03:00 is usable, and only
    its first 50 minutes count. The cheap hour 03:00-04:00 (below the value) lies after
    the departure and earns nothing, so it is not used; the credit covers the
    3.33 kWh stored by 02:50, not the 4 kWh of the whole hour."""
    configure(config_eos, interval=3600)
    prices = [EXPENSIVE] * 12 + [CHEAP] * 4 + [EXPENSIVE] * 8
    params = parameters(
        prices=prices,
        initial_soc_percentage=20,
        charge_start_datetime="2026-10-29T01:07:00",
        min_soc_deadline_datetime="2026-10-29T02:50:00",
        soc_value_steps=[[80, 0.3]],
    )
    optimizer, solution = run(params, ngen=20, individuals=30)
    assert (optimizer._ev_first_charge_slot, optimizer._ev_soc_deadline_slot) == (2, 2)
    assert charged_slots(solution) == [2]
    soc = optimizer._ev_soc_at_deadline({"EAuto_SoC_pro_Stunde": solution.result.ev_soc_per_hour}, 0)
    assert soc == pytest.approx(20.0 + 40.0 * 5 / 6)
    assert optimizer._ev_soc_value_credit(soc) == pytest.approx((soc - 40.0) / 10.0 * 0.3)


def test_exported_plan_matches_the_simulation_and_stops_at_the_maximum(
    config_eos: ConfigEOS,
) -> None:
    """The plan a client executes charges exactly where the simulation stored energy,
    and is IDLE once the vehicle is at its maximum SoC."""
    configure(config_eos)
    _, solution = run(parameters(soc_value_steps=[[80, 0.3]]), ngen=20, individuals=30)
    rates = solution.ev_charge_hours_float
    assert rates is not None
    soc = solution.result.ev_soc_per_hour
    for slot, rate in enumerate(rates):
        stored = (soc[slot + 1] if slot + 1 < len(soc) else final_soc(solution)) - soc[slot]
        if rate > 0.0:
            assert stored > 0.0, f"slot {slot} is planned to charge but stores nothing"
        else:
            assert stored == pytest.approx(0.0)
        if soc[slot] >= 80.0 - 1e-9:
            assert rate == 0.0, f"slot {slot} charges at the maximum SoC"

    start = to_datetime(START)
    ev_items = [
        item
        for item in solution.energy_management_plan().instructions
        if item.resource_id == "car"
    ]
    instructions = [item for item in ev_items if isinstance(item, FRBCInstruction)]
    assert instructions and len(instructions) == len(ev_items)
    # Replay the instructions slot by slot: the mode in force in a slot is the
    # last instruction executed at or before its start.
    for slot, rate in enumerate(rates):
        slot_start = start.add(minutes=15 * slot)
        active = [item for item in instructions if item.execution_time <= slot_start][-1]
        assert (str(active.operation_mode_id) != "IDLE") == (rate > 0.0), slot
        if rate > 0.0:
            assert active.operation_mode_factor == pytest.approx(rate)


def test_seed_fills_cheap_slots_up_to_the_valued_soc(config_eos: ConfigEOS) -> None:
    configure(config_eos)
    optimizer, _ = run(parameters(soc_value_steps=[[80, 0.3]]))
    schedule = optimizer._heuristic_ev_schedule(prefer_pv=False)
    ev = optimizer.simulation.ev
    assert ev is not None
    stored = sum(
        optimizer.ev_possible_charge_values[gene] * ev.max_charge_power_w * 0.25
        for gene in schedule
    )
    assert stored == pytest.approx(3000.0)
    assert {slot for slot, gene in enumerate(schedule) if gene != 0} <= set(CHEAP_SLOTS)


def test_seed_does_not_fill_where_the_price_exceeds_the_value(config_eos: ConfigEOS) -> None:
    configure(config_eos)
    optimizer, _ = run(parameters(soc_value_steps=[[80, 0.05]]))
    assert optimizer._heuristic_ev_schedule(prefer_pv=False) == [0] * optimizer.control_end_slot


def test_pv_seed_counts_the_import_needed_beyond_the_surplus(config_eos: ConfigEOS) -> None:
    """A small surplus at a zero feed-in tariff does not make a full-power slot cheap:
    the rest of its energy is imported at the expensive price."""
    configure(config_eos)
    pv = [0.0] * SLOTS
    pv[2] = 100.0  # 0.1 kWh of a 1 kWh slot
    pv[3] = 1000.0  # the whole slot
    optimizer, _ = run(parameters(prices=[EXPENSIVE] * SLOTS, pv=pv, soc_value_steps=[[80, 0.3]]))
    schedule = optimizer._heuristic_ev_schedule(prefer_pv=True)
    assert {slot for slot, gene in enumerate(schedule) if gene != 0} == {3}


@pytest.mark.parametrize(("direct_marketing", "seeded"), [(False, {3}), (True, set())])
def test_pv_seed_with_a_negative_feed_in_tariff(
    config_eos: ConfigEOS, direct_marketing: bool, seeded: set[int]
) -> None:
    """Without direct marketing, storing surplus saves the negative feed-in charge.
    With direct marketing that export is curtailed anyway: the surplus is free but
    saves nothing, so a value of 0.05 does not pay for the import of the rest of the
    slot (0.6 kWh surplus, 0.4 kWh at 0.40)."""
    configure(config_eos)
    config_eos.merge_settings_from_dict(
        {"feedintariff": {"direct_marketing_enabled": direct_marketing}}
    )
    pv = [0.0] * SLOTS
    pv[3] = 600.0
    params = parameters(prices=[EXPENSIVE] * SLOTS, pv=pv, soc_value_steps=[[80, 0.05]])
    params.ems.feed_in_tariff_per_wh = [0.0] * 3 + [-0.0003] + [0.0] * (SLOTS - 4)
    optimizer, _ = run(params)
    schedule = optimizer._heuristic_ev_schedule(prefer_pv=True)
    assert {slot for slot, gene in enumerate(schedule) if gene != 0} == seeded


def test_pv_seed_takes_the_cheapest_slot_first(config_eos: ConfigEOS) -> None:
    """Little room left (70 -> 80 % = 1 kWh): the PV seed uses the slot that is all
    surplus, not the one with a cheaper feed-in tariff whose energy comes mostly
    from the grid."""
    configure(config_eos)
    pv = [0.0] * SLOTS
    pv[2] = 100.0  # 0.1 kWh at a feed-in tariff of 0.01, the rest at 0.30
    pv[3] = 1000.0  # 1 kWh at a feed-in tariff of 0.02
    params = parameters(
        prices=[0.0003] * SLOTS,
        pv=pv,
        initial_soc_percentage=70,
        soc_value_steps=[[80, 0.3]],
    )
    params.ems.feed_in_tariff_per_wh = [0.00001] * 3 + [0.00002] + [0.00001] * (SLOTS - 4)
    optimizer, _ = run(params)
    schedule = optimizer._heuristic_ev_schedule(prefer_pv=True)
    assert {slot for slot, gene in enumerate(schedule) if gene != 0} == {3}


def test_seed_without_value_is_unchanged(config_eos: ConfigEOS) -> None:
    configure(config_eos)
    optimizer, _ = run(
        parameters(initial_soc_percentage=0, min_soc_percentage=50, max_soc_percentage=100)
    )
    schedule = optimizer._heuristic_ev_schedule(prefer_pv=False)
    ev = optimizer.simulation.ev
    assert ev is not None
    stored = sum(
        optimizer.ev_possible_charge_values[gene] * ev.max_charge_power_w * 0.25
        for gene in schedule
    )
    assert stored == pytest.approx(5000.0)


@pytest.mark.parametrize("empty", [None, []])
def test_explicit_none_or_empty_steps_give_the_identical_plan(
    config_eos: ConfigEOS, empty: Optional[list[Any]]
) -> None:
    common: dict[str, Any] = {
        "initial_soc_percentage": 10,
        "min_soc_percentage": 50,
        "max_soc_percentage": 80,
    }
    configure(config_eos)
    _, without = run(parameters(**common), ngen=5, individuals=20)
    configure(config_eos)
    _, with_empty = run(parameters(**common, soc_value_steps=empty), ngen=5, individuals=20)
    assert without.start_solution == with_empty.start_solution
    assert without.fitness_history == with_empty.fitness_history
    assert without.ev_charge_hours_float == with_empty.ev_charge_hours_float


# ---------------------------------------------------------------------------
# Real price and PV data (tests/testdata/genetic/optimize_input_2.json)
# ---------------------------------------------------------------------------


def real_parameters(*, with_pv: bool = True, **ev: Any) -> GeneticOptimizationParameters:
    """Hourly day-ahead import prices, PV forecast and household load of two days."""
    data = json.loads((DIR_TESTDATA / "optimize_input_2.json").read_text())
    ems = data["ems"]
    pv = ems["pv_prognose_wh"] if with_pv else [0.0] * len(ems["pv_prognose_wh"])
    return GeneticOptimizationParameters.model_validate(
        {
            "ems": {
                "pv_prognose_wh": pv,
                "gesamtlast": ems["gesamtlast"],
                "strompreis_euro_pro_wh": ems["strompreis_euro_pro_wh"],
                "einspeiseverguetung_euro_pro_wh": ems["einspeiseverguetung_euro_pro_wh"],
                "preis_euro_pro_wh_akku": 0.0,
            },
            "inverter": {"device_id": "inv", "max_power_wh": 10000},
            "forecast_interval_seconds": 3600,
            "pv_battery": None,
            "ev": {
                "device_id": "car",
                "capacity_wh": 60000,
                "initial_soc_percentage": 40,
                "min_soc_percentage": 40,
                "max_soc_percentage": 80,
                "charging_efficiency": 0.9,
                "max_charge_power_w": 11000,
                "charge_rates": [round(0.1 * i, 1) for i in range(11)],
                **ev,
            },
        }
    )


def test_real_prices_charge_beyond_the_target_in_the_cheap_hours_only(
    config_eos: ConfigEOS,
) -> None:
    """Energy above the target is worth 0.25 per stored kWh, i.e. 0.225 per kWh from the
    grid at 90 % charging efficiency. The import price is 18.4-22.1 ct in the hours 11-16
    and 22.7-33.8 ct in all others: the car charges in the cheap hours only and reaches
    its maximum, 24 kWh above its initial SoC. PV is left out, so that the price alone
    decides (PV surplus would make some of the dearer hours pay as well)."""
    configure(config_eos, hours=24, interval=3600)
    params = real_parameters(with_pv=False, soc_value_steps=[[80, 0.25]])
    prices = params.ems.electricity_price_per_wh
    cheap_hours = {
        hour for hour, price in enumerate(prices[:24]) if price / 0.9 < 0.25 / 1000.0
    }
    assert cheap_hours == set(range(11, 17))
    _, solution = run(params, ngen=40, individuals=60, seed=3)
    assert charged_slots(solution)
    assert set(charged_slots(solution)) <= cheap_hours
    assert final_soc(solution) == pytest.approx(80.0)

    # Without a value the car stays at its target, which it has already reached.
    configure(config_eos, hours=24, interval=3600)
    _, reference = run(real_parameters(with_pv=False), ngen=40, individuals=60, seed=3)
    assert charged_slots(reference) == []
    assert final_soc(reference) == pytest.approx(40.0)


def test_real_pv_surplus_is_stored_at_a_value_below_the_import_price(
    config_eos: ConfigEOS,
) -> None:
    """No target, energy worth 0.10 per stored kWh: PV surplus (feed-in 7 ct, i.e.
    7.8 ct per stored kWh) pays, grid energy (at least 18.4 ct) does not. The car
    charges only in hours with PV surplus."""
    configure(config_eos, hours=24, interval=3600)
    params = real_parameters(
        initial_soc_percentage=20, min_soc_percentage=0, soc_value_steps=[[80, 0.10]]
    )
    surplus_hours = {
        hour
        for hour, (pv, load) in enumerate(
            zip(params.ems.pv_forecast_wh[:24], params.ems.total_load[:24])
        )
        if pv > load
    }
    _, solution = run(params, ngen=40, individuals=60, seed=3)
    assert charged_slots(solution)
    assert set(charged_slots(solution)) <= surplus_hours
    assert final_soc(solution) > 20.0


# ---------------------------------------------------------------------------
# Charging losses: the value is per stored kWh
# ---------------------------------------------------------------------------


def lossy_prices(cheap: float) -> list[float]:
    prices = [EXPENSIVE] * SLOTS
    for slot in CHEAP_SLOTS:
        prices[slot] = cheap
    return prices


@pytest.mark.parametrize(("cheap", "charges"), [(0.00009, True), (0.0001, False)])
def test_value_is_per_stored_kwh_with_charging_losses(
    config_eos: ConfigEOS, cheap: float, charges: bool
) -> None:
    """At 80 % charging efficiency a stored kWh worth 0.12 pays at an import price of
    0.09 (0.1125 per stored kWh), not at 0.10 (0.125). Credited per kWh from the grid,
    the value would be 0.15 per stored kWh and 0.10 would pay as well."""
    configure(config_eos)
    params = parameters(
        prices=lossy_prices(cheap), charging_efficiency=0.8, soc_value_steps=[[80, 0.12]]
    )
    _, solution = run(params, ngen=20, individuals=30)
    if charges:
        assert set(charged_slots(solution)) <= set(CHEAP_SLOTS)
        assert final_soc(solution) == pytest.approx(80.0)
    else:
        assert charged_slots(solution) == []
        assert final_soc(solution) == pytest.approx(50.0)


@pytest.mark.parametrize(("cheap", "slots"), [(0.00009, 4), (0.0001, 0)])
def test_seed_fill_counts_the_charging_losses(
    config_eos: ConfigEOS, cheap: float, slots: int
) -> None:
    """The seed compares the import price with the value of the energy a slot stores:
    at 80 % efficiency a full slot stores 0.8 kWh, so 3 kWh to the maximum take four
    slots at 0.09 and none at 0.10."""
    configure(config_eos)
    params = parameters(
        prices=lossy_prices(cheap), charging_efficiency=0.8, soc_value_steps=[[80, 0.12]]
    )
    optimizer, _ = run(params)
    schedule = optimizer._heuristic_ev_schedule(prefer_pv=False)
    filled = [slot for slot, gene in enumerate(schedule) if gene != 0]
    assert len(filled) == slots
    assert set(filled) <= set(CHEAP_SLOTS)


def test_seed_fills_beyond_the_target_after_the_target_energy(config_eos: ConfigEOS) -> None:
    """20 % -> target 40 % takes two full slots, 40 -> 80 % four more: six in total.
    Counting the fill from the initial SoC would add two more."""
    configure(config_eos)
    params = parameters(
        initial_soc_percentage=20,
        min_soc_percentage=40,
        max_soc_percentage=80,
        soc_value_steps=[[80, 0.3]],
    )
    optimizer, _ = run(params)
    schedule = optimizer._heuristic_ev_schedule(prefer_pv=False)
    full_index = len(optimizer.ev_possible_charge_values) - 1
    filled = [slot for slot, gene in enumerate(schedule) if gene != 0]
    assert len(filled) == 6
    assert all(schedule[slot] == full_index for slot in filled)
    assert set(filled) <= set(CHEAP_SLOTS)


# ---------------------------------------------------------------------------
# With a house battery: its stored energy has a value too
# ---------------------------------------------------------------------------


def battery_parameters(value: float) -> GeneticOptimizationParameters:
    """Grid at 0.40 in every slot, a full 10 kWh house battery whose energy is worth
    0.20 per kWh at the end of the horizon, and a car at its target."""
    params = parameters(prices=[EXPENSIVE] * SLOTS, soc_value_steps=[[80, value]])
    data = params.model_dump(mode="json")
    data["pv_battery"] = {
        "device_id": "battery",
        "capacity_wh": 10000,
        "initial_soc_percentage": 80,
        "min_soc_percentage": 10,
        "max_charge_power_w": 5000,
        "charging_efficiency": 0.95,
        "discharging_efficiency": 0.95,
    }
    data["inverter"]["battery_id"] = "battery"
    data["ems"]["price_per_wh_battery"] = 0.0002
    return GeneticOptimizationParameters.model_validate(data)


@pytest.mark.parametrize(("value", "charges"), [(0.15, False), (0.35, True)])
def test_value_against_the_value_of_the_house_battery(
    config_eos: ConfigEOS, value: float, charges: bool
) -> None:
    """Grid energy (0.40) never pays. Energy from the house battery is worth 0.20 per
    kWh kept: moving it into the car pays at 0.35 per stored kWh, not at 0.15. The value
    of the car and the value of the battery are not counted twice."""
    configure(config_eos)
    _, solution = run(battery_parameters(value), ngen=30, individuals=40)
    assert sum(solution.result.grid_consumption_wh_per_hour) == pytest.approx(0.0, abs=1.0)
    battery_soc = solution.result.battery_soc_per_hour
    if charges:
        assert final_soc(solution) == pytest.approx(80.0)
        assert min(battery_soc) < 80.0
    else:
        assert charged_slots(solution) == []
        assert final_soc(solution) == pytest.approx(50.0)
        assert battery_soc == pytest.approx([80.0] * len(battery_soc))
