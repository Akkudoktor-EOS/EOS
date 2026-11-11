"""Earliest EV charge start for the GENETIC optimizer.

``charge_start_datetime`` is the earliest moment the vehicle may charge, for
example its arrival; ``min_soc_deadline_datetime`` only sets when the target
state of charge is due. Before the charge start the vehicle neither charges nor
discharges, and its state of charge stays at ``initial_soc_percentage``. Most
tests use an arriving vehicle as the example.
"""

from collections.abc import Callable
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock

import numpy as np
import pytest
from pydantic import ValidationError

from akkudoktoreos.config.config import ConfigEOS
from akkudoktoreos.core.coreabc import get_ems
from akkudoktoreos.core.emplan import FRBCInstruction
from akkudoktoreos.devices.genetic.battery import (
    Battery,
    ElectricVehicleParameters,
    SolarPanelBatteryParameters,
)
from akkudoktoreos.devices.settings.batterysettings import BatteriesCommonSettings
from akkudoktoreos.optimization.genetic import configrequest
from akkudoktoreos.optimization.genetic.configrequest import ConfigOptimizationRequest
from akkudoktoreos.optimization.genetic.genetic import (
    GeneticOptimization,
    GeneticSimulation,
)
from akkudoktoreos.optimization.genetic.geneticparams import (
    GeneticEnergyManagementParameters,
    GeneticOptimizationParameters,
)
from akkudoktoreos.optimization.genetic.geneticsolution import GeneticSolution
from akkudoktoreos.utils.datetimeutil import to_datetime

# ---------------------------------------------------------------------------
# Settings and request plumbing
# ---------------------------------------------------------------------------


def test_charge_start_defaults_to_none_everywhere() -> None:
    settings = BatteriesCommonSettings(device_id="car")
    assert settings.charge_start_datetime is None
    assert settings.to_genetic_ev_bat_param().charge_start_datetime is None
    params = ElectricVehicleParameters(device_id="car", capacity_wh=10000)
    assert params.charge_start_datetime is None


def test_charge_start_roundtrips_from_settings_to_genetic_ev_parameters() -> None:
    settings = BatteriesCommonSettings.model_validate(
        {
            "device_id": "car",
            "capacity_wh": 10000,
            "charge_start_datetime": "2026-09-16T20:00:00+02:00",
            "min_soc_deadline_datetime": "2026-09-17T05:00:00+02:00",
        }
    )
    params = settings.to_genetic_ev_bat_param()
    assert params.charge_start_datetime is not None
    assert params.charge_start_datetime.timestamp() == (
        to_datetime("2026-09-16T18:00:00Z").timestamp()
    )
    # Only the GENETIC EV carries it: not the stationary battery, not GENETIC0.
    assert "charge_start_datetime" not in settings.to_genetic_pv_bat_param().model_dump()
    assert "charge_start_datetime" not in settings.to_genetic0_ev_bat_param().model_dump()
    assert "charge_start_datetime" not in SolarPanelBatteryParameters.model_fields


@pytest.mark.parametrize("host_timezone", ["UTC", "Europe/Berlin"])
def test_naive_charge_start_is_local_time(
    set_other_timezone: Callable[[str], str], host_timezone: str
) -> None:
    set_other_timezone(host_timezone)
    for model in (ElectricVehicleParameters, BatteriesCommonSettings):
        params = model.model_validate(
            {"device_id": "car", "capacity_wh": 10000, "charge_start_datetime": "2026-09-16T20:00"}
        )
        assert params.charge_start_datetime is not None
        local = params.charge_start_datetime.in_timezone(host_timezone)
        assert (local.hour, local.minute) == (20, 0)


def test_invalid_charge_start_is_rejected() -> None:
    for model in (ElectricVehicleParameters, BatteriesCommonSettings):
        with pytest.raises(ValidationError):
            model.model_validate(
                {"device_id": "car", "capacity_wh": 10000, "charge_start_datetime": "not a date"}
            )


def test_charge_start_in_direct_genetic_parameters() -> None:
    """GeneticOptimizationParameters (the GENETIC run input) accept the field on the EV."""
    params = GeneticOptimizationParameters.model_validate(
        {
            "ems": {
                "pv_prognose_wh": [0.0],
                "gesamtlast": [0.0],
                "strompreis_euro_pro_wh": [0.0003],
                "einspeiseverguetung_euro_pro_wh": [0.0001],
                "preis_euro_pro_wh_akku": 0.0,
            },
            "pv_battery": None,
            "inverter": None,
            "ev": {
                "device_id": "car",
                "capacity_wh": 10000,
                "charge_start_datetime": "2026-09-16T20:00:00+02:00",
            },
        }
    )
    assert params.ev is not None
    assert params.ev.charge_start_datetime is not None
    restored = GeneticOptimizationParameters.model_validate_json(params.model_dump_json())
    assert restored.ev is not None and restored.ev.charge_start_datetime is not None
    assert restored.ev.charge_start_datetime.timestamp() == (
        params.ev.charge_start_datetime.timestamp()
    )


@pytest.mark.asyncio
async def test_config_request_carries_configured_charge_start(
    config_eos: ConfigEOS, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POST /v1/optimize takes the EV from configuration, including its charge start."""
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
                        "min_soc_percentage": 80,
                        "charge_start_datetime": "2026-09-12T20:00:00+00:00",
                        "min_soc_deadline_datetime": "2026-09-13T05:00:00+00:00",
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
            "soc": {"car": 40},
            "forecasts": {
                "pv_forecast_wh": [0.0] * 96,
                "total_load": [0.0] * 96,
                "electricity_price_per_wh": [0.0003] * 96,
                "feed_in_tariff_per_wh": [0.0] * 96,
            },
        }
    )
    parameters = await request.resolve()
    assert parameters.ev is not None
    assert parameters.ev.initial_soc_percentage == 40
    assert parameters.ev.charge_start_datetime is not None
    assert parameters.ev.charge_start_datetime.timestamp() == (
        to_datetime("2026-09-12T20:00:00Z").timestamp()
    )


# ---------------------------------------------------------------------------
# Slot alignment
# ---------------------------------------------------------------------------


def configure_grid(
    config: ConfigEOS,
    *,
    interval_sec: int,
    hours: int,
    start: str,
    timezone: str = "Europe/Berlin",
) -> GeneticOptimization:
    """Configure the GENETIC grid and return an optimizer with its run start in place."""
    config.merge_settings_from_dict(
        {
            "prediction": {"hours": max(48, hours)},
            "optimization": {
                "algorithm": "GENETIC",
                "genetic": {
                    "horizon_hours": hours,
                    "tail_horizon_hours": 0,
                    "interval_sec": interval_sec,
                    "terminal_value_mode": "FIXED",
                },
            },
        }
    )
    get_ems(init=True).set_start_datetime(to_datetime(start, in_timezone=timezone))
    optimizer = GeneticOptimization(fixed_seed=1)
    optimizer._slot0_datetime = optimizer.ems.start_datetime
    return optimizer


def ev_only_parameters(**ev_extra: Any) -> GeneticOptimizationParameters:
    return GeneticOptimizationParameters(
        ems=GeneticEnergyManagementParameters(
            pv_forecast_wh=[0.0],
            total_load=[0.0],
            electricity_price_per_wh=[0.0003],
            feed_in_tariff_per_wh=[0.0],
            price_per_wh_battery=0.0,
        ),
        pv_battery=None,
        inverter=None,
        ev=ElectricVehicleParameters(
            device_id="car", capacity_wh=40000, min_soc_percentage=80, **ev_extra
        ),
    )


@pytest.mark.parametrize(
    "charge_start,expected",
    [
        (None, 0),
        ("2026-09-16T10:00:00+02:00", 0),  # exactly the run start
        ("2026-09-16T08:00:00+02:00", 0),  # in the past: available now
        ("2026-09-16T15:00:00+02:00", 5),  # on a slot boundary: that slot is usable
        ("2026-09-16T13:00:00Z", 5),  # the same instant in another zone
        ("2026-09-16T14:59:59+02:00", 5),
        ("2026-09-16T15:00:01+02:00", 6),  # inside slot 5: slot 5 is not usable
        ("2026-09-16T15:30:00+02:00", 6),
        ("2026-09-16T14:30:00+01:00", 6),  # 15:30 local, given with another offset
        ("2026-09-16T15:30:00", 6),  # naive: local time of the host (Europe/Berlin)
        ("2026-09-19T10:00:00+02:00", 48),  # beyond the horizon: the control end
    ],
)
def test_charge_start_slot_on_hourly_grid(
    config_eos: ConfigEOS,
    set_other_timezone: Callable[[str], str],
    charge_start: str | None,
    expected: int,
) -> None:
    set_other_timezone("Europe/Berlin")
    optimizer = configure_grid(config_eos, interval_sec=3600, hours=48, start="2026-09-16T10:00:00")
    parameters = ev_only_parameters(charge_start_datetime=charge_start)
    assert optimizer._ev_charge_start_slot(parameters) == expected


@pytest.mark.parametrize(
    "charge_start,expected",
    [
        ("2026-09-16T11:00:00+02:00", 4),
        ("2026-09-16T11:05:00+02:00", 5),
        ("2026-09-16T11:15:00+02:00", 5),
        ("2026-09-16T11:15:01+02:00", 6),
        ("2026-09-16T09:15:00Z", 5),  # 11:15 local
        ("2026-09-16T09:50:00+02:00", 0),
    ],
)
def test_charge_start_slot_on_quarter_hour_grid(
    config_eos: ConfigEOS, charge_start: str, expected: int
) -> None:
    optimizer = configure_grid(config_eos, interval_sec=900, hours=24, start="2026-09-16T10:00:00")
    parameters = ev_only_parameters(charge_start_datetime=charge_start)
    assert optimizer._ev_charge_start_slot(parameters) == expected


@pytest.mark.parametrize(
    "charge_start,expected",
    [
        # The run started at 10:07 in the slot 10:00-10:15. A vehicle that
        # arrived at 10:05 is already there: the current slot is usable.
        ("2026-09-16T10:05:00+02:00", 0),
        ("2026-09-16T10:07:00+02:00", 0),
        # One that arrives at 10:10 is not there yet and misses part of the slot.
        ("2026-09-16T10:10:00+02:00", 1),
    ],
)
def test_charge_start_inside_the_current_slot_compares_with_now(
    config_eos: ConfigEOS, charge_start: str, expected: int
) -> None:
    optimizer = configure_grid(config_eos, interval_sec=900, hours=24, start="2026-09-16T10:07:00")
    assert optimizer._slot0_datetime == to_datetime("2026-09-16T10:00:00+02:00")
    parameters = ev_only_parameters(charge_start_datetime=charge_start)
    assert optimizer._ev_charge_start_slot(parameters) == expected


def kept_deadline(
    optimizer: GeneticOptimization, parameters: GeneticOptimizationParameters
) -> tuple[int, float]:
    """Deadline slot and the share of it before the deadline; fails if relaxed."""
    deadline = optimizer._ev_deadline(parameters)
    assert deadline is not None
    return deadline


@pytest.mark.parametrize(
    "charge_start,deadline,expected_deadline",
    [
        # Departure after arrival: the deadline is kept.
        ("2026-09-16T20:00:00+02:00", "2026-09-17T05:00:00+02:00", (19, 0.0)),
        # Departure before arrival (e.g. the previous morning's departure was
        # not updated yet): no plan can charge before it. The deadline is
        # relaxed and the target is required at the end of the horizon.
        ("2026-09-16T20:00:00+02:00", "2026-09-16T18:00:00+02:00", None),
        ("2026-09-16T20:00:00+02:00", "2026-09-16T08:00:00+02:00", None),
        # Without a charge start a past deadline is still due right now.
        (None, "2026-09-16T08:00:00+02:00", (0, 0.0)),
        # A charge start and a later deadline that have both passed: the
        # target is due right now, too.
        ("2026-09-16T07:00:00+02:00", "2026-09-16T08:00:00+02:00", (0, 0.0)),
        # A deadline at or before the charge start stays relaxed after the
        # charge start was reached: no plan can meet it at any time.
        ("2026-09-16T08:00:00+02:00", "2026-09-16T08:00:00+02:00", None),
        ("2026-09-16T09:00:00+02:00", "2026-09-16T08:00:00+02:00", None),
    ],
)
def test_deadline_before_charge_start_is_relaxed(
    config_eos: ConfigEOS,
    charge_start: str | None,
    deadline: str,
    expected_deadline: tuple[int, float] | None,
) -> None:
    optimizer = configure_grid(config_eos, interval_sec=3600, hours=24, start="2026-09-16T10:00:00")
    parameters = ev_only_parameters(
        charge_start_datetime=charge_start, min_soc_deadline_datetime=deadline
    )
    assert optimizer._ev_deadline(parameters) == expected_deadline
    expected_slot = None if expected_deadline is None else expected_deadline[0]
    assert optimizer._ev_deadline_slot(parameters) == expected_slot


@pytest.mark.parametrize(
    "interval_sec,charge_start,deadline,expected_deadline",
    [
        # Arrival 01:07, departure 02:50. On the hourly grid 02:00-03:00 is the
        # only slot after the charge start; its first 50 minutes lie before the
        # departure, so 50/60 of its charge counts towards the target.
        (3600, "01:07", "02:50", (2, 50 / 60)),
        # The same window on the 15-minute grid: full slots plus a share of the last.
        (900, "01:07", "02:50", (11, 5 / 15)),
        # A departure on a slot boundary leaves no share of the next slot.
        (3600, "01:07", "03:00", (3, 0.0)),
        # Charge start on a slot boundary, departure inside that slot.
        (3600, "01:00", "01:50", (1, 50 / 60)),
        (900, "01:00", "01:05", (4, 5 / 15)),
        # No time to charge: the deadline is still kept (and nothing is planned
        # for the target), it does not move to the end of the horizon.
        # Charge start and departure inside the same slot, which begins before
        # the charge start and is therefore not used:
        (3600, "01:07", "01:50", (1, 50 / 60)),
        (900, "01:02", "01:12", (4, 12 / 15)),
        # Departure on the boundary of the first usable slot:
        (3600, "01:07", "02:00", (2, 0.0)),
    ],
)
def test_deadline_inside_a_slot_counts_its_share(
    config_eos: ConfigEOS,
    set_other_timezone: Callable[[str], str],
    interval_sec: int,
    charge_start: str,
    deadline: str,
    expected_deadline: tuple[int, float],
) -> None:
    """A consistent pair of charge start and deadline never loses its deadline.

    The target is checked at the deadline itself. The slot the deadline falls
    in counts with the part of it before the deadline; the charge start keeps
    its rule (a slot that begins before it is not used).
    """
    set_other_timezone("Europe/Berlin")
    optimizer = configure_grid(
        config_eos, interval_sec=interval_sec, hours=6, start="2026-09-16T00:00:00"
    )
    parameters = ev_only_parameters(
        charge_start_datetime=f"2026-09-16T{charge_start}:00",
        min_soc_deadline_datetime=f"2026-09-16T{deadline}:00",
    )
    deadline_slot, share = kept_deadline(optimizer, parameters)
    assert deadline_slot == expected_deadline[0]
    assert share == pytest.approx(expected_deadline[1])


def test_deadline_without_time_to_charge_is_kept_with_a_warning(
    config_eos: ConfigEOS, caplog: pytest.LogCaptureFixture
) -> None:
    """Arrival 01:07, departure 01:50 on the hourly grid: the vehicle can not charge
    before its departure (01:00-02:00 begins before the arrival). The warning names
    the given moments, not slot times."""
    optimizer = configure_grid(config_eos, interval_sec=3600, hours=6, start="2026-09-16T00:00:00")
    parameters = ev_only_parameters(
        charge_start_datetime="2026-09-16T01:07:00+02:00",
        min_soc_deadline_datetime="2026-09-16T01:50:00+02:00",
    )
    with caplog.at_level("WARNING"):
        assert optimizer._ev_deadline_slot(parameters) == 1
    assert "01:07" in caplog.text and "01:50" in caplog.text
    assert "can not charge before its deadline" in caplog.text


def test_relaxation_warning_names_the_given_moments(
    config_eos: ConfigEOS, caplog: pytest.LogCaptureFixture
) -> None:
    """Departure 00:50 before arrival 01:07: the warning quotes both, not 00:00/02:00."""
    optimizer = configure_grid(config_eos, interval_sec=3600, hours=6, start="2026-09-16T00:00:00")
    parameters = ev_only_parameters(
        charge_start_datetime="2026-09-16T01:07:00+02:00",
        min_soc_deadline_datetime="2026-09-16T00:50:00+02:00",
    )
    with caplog.at_level("WARNING"):
        assert optimizer._ev_deadline(parameters) is None
    assert "00:50" in caplog.text and "01:07" in caplog.text
    assert "is not after its charge start" in caplog.text


@pytest.mark.parametrize(
    "charge_start,deadline,expected_deadline",
    [
        # Reached during the current slot: the vehicle can charge in slot 0,
        # and a third of slot 1 lies before the deadline.
        ("2026-09-16T10:05:00+02:00", "2026-09-16T10:20:00+02:00", (1, 1 / 3)),
        # Charge start still ahead in the current slot: slot 0 is not usable,
        # the first third of slot 1 is.
        ("2026-09-16T10:10:00+02:00", "2026-09-16T10:20:00+02:00", (1, 1 / 3)),
    ],
)
def test_deadline_with_a_charge_start_inside_the_current_slot(
    config_eos: ConfigEOS,
    charge_start: str,
    deadline: str,
    expected_deadline: tuple[int, float],
) -> None:
    optimizer = configure_grid(config_eos, interval_sec=900, hours=24, start="2026-09-16T10:07:00")
    parameters = ev_only_parameters(
        charge_start_datetime=charge_start, min_soc_deadline_datetime=deadline
    )
    deadline_slot, share = kept_deadline(optimizer, parameters)
    assert deadline_slot == expected_deadline[0]
    assert share == pytest.approx(expected_deadline[1])


@pytest.mark.parametrize(
    "run_start,expected_slot",
    [
        # Before the charge start: 13:00-14:00 begins before 13:05, the first
        # half of 14:00-15:00 lies before the deadline.
        ("2026-09-16T12:00:00", 2),
        # Inside the charge start's slot, after it: 13:00-14:00 is usable now.
        ("2026-09-16T13:07:00", 1),
        # In the deadline's slot: the current slot is usable until 14:30.
        ("2026-09-16T14:10:00", 0),
    ],
)
def test_deadline_inside_a_slot_is_kept_in_every_run(
    config_eos: ConfigEOS,
    set_other_timezone: Callable[[str], str],
    run_start: str,
    expected_slot: int,
) -> None:
    """Charge start 13:05, deadline 14:30 on an hourly grid, planned three times.

    Every run keeps the deadline, with or without the charge start: the half of
    14:00-15:00 before the deadline counts towards the target in all of them.
    """
    set_other_timezone("Europe/Berlin")
    optimizer = configure_grid(config_eos, interval_sec=3600, hours=24, start=run_start)
    deadline = "2026-09-16T14:30:00+02:00"
    parameters = ev_only_parameters(
        charge_start_datetime="2026-09-16T13:05:00+02:00", min_soc_deadline_datetime=deadline
    )
    without = ev_only_parameters(min_soc_deadline_datetime=deadline)
    for params in (parameters, without):
        deadline_slot, share = kept_deadline(optimizer, params)
        assert (deadline_slot, share) == (expected_slot, pytest.approx(0.5))


def test_deadline_share_across_the_end_of_daylight_saving_time(
    config_eos: ConfigEOS, set_other_timezone: Callable[[str], str]
) -> None:
    """25.10.2026, clocks go back at 03:00 CEST: 02:10 CEST and 02:40 CET lie 90
    minutes apart. Slots count elapsed time, so the hourly slot 03:00 (the
    second 02:00-03:00, CET) is usable and its first 40 minutes count."""
    set_other_timezone("Europe/Berlin")
    optimizer = configure_grid(config_eos, interval_sec=3600, hours=6, start="2026-10-25T00:00:00")
    parameters = ev_only_parameters(
        charge_start_datetime="2026-10-25T02:10:00+02:00",
        min_soc_deadline_datetime="2026-10-25T02:40:00+01:00",
    )
    assert optimizer._ev_charge_start_slot(parameters) == 3
    deadline_slot, share = kept_deadline(optimizer, parameters)
    assert deadline_slot == 3
    assert share == pytest.approx(40 / 60)


# ---------------------------------------------------------------------------
# Simulation with the real EV device
# ---------------------------------------------------------------------------


def test_simulation_keeps_ev_idle_before_charge_start() -> None:
    """Hand-calculated: 4 kWh EV, 4 kW charger, 90 % efficiency, 15-min slots.

    Every slot requests full power. Slots 0 and 1 are before the charge start
    (slot 2), so the EV stays at 10 % (400 Wh). From slot 2 on each slot draws
    4000 W * 0.25 h = 1000 Wh from the grid side and stores 900 Wh (10 % losses).
    """
    ev = Battery(
        ElectricVehicleParameters(
            device_id="car",
            capacity_wh=4000,
            charging_efficiency=0.9,
            max_charge_power_w=4000,
            initial_soc_percentage=10,
            min_soc_percentage=100,
        ),
        prediction_hours=5,
        slot_duration_h=0.25,
    )
    simulation = GeneticSimulation()
    simulation.prepare(
        parameters=GeneticEnergyManagementParameters(
            pv_forecast_wh=[0.0] * 5,
            total_load=[0.0] * 5,
            electricity_price_per_wh=[0.0003] * 5,
            feed_in_tariff_per_wh=[0.0] * 5,
            price_per_wh_battery=0.0,
        ),
        optimization_hours=5,
        prediction_hours=5,
        ev=ev,
        ev_charge_start_slot=2,
    )
    simulation.ev_charge_hours = np.full(5, 1.0)
    # The GENETIC EV never discharges, but the simulation must clear any
    # discharge control before the charge start as well and keep it after.
    simulation.ev_discharge_hours = np.full(5, 1)
    result = simulation.simulate(0)

    # SoC at the beginning of each slot: 400, 400, 400, 1300, 2200 Wh.
    assert result["EAuto_SoC_pro_Stunde"].tolist() == pytest.approx([10.0, 10.0, 10.0, 32.5, 55.0])
    assert result["Last_Wh_pro_Stunde"].tolist() == pytest.approx(
        [0.0, 0.0, 1000.0, 1000.0, 1000.0]
    )
    assert result["Verluste_Pro_Stunde"].tolist() == pytest.approx([0.0, 0.0, 100.0, 100.0, 100.0])
    assert ev.soc_wh == pytest.approx(400.0 + 3 * 900.0)
    # The executed (and reported) charge plan is the simulated one.
    assert ev.charge_array.tolist() == [0.0, 0.0, 1.0, 1.0, 1.0]
    assert simulation.ev_charge_hours.tolist() == [0.0, 0.0, 1.0, 1.0, 1.0]
    assert ev.discharge_array.tolist() == [0, 0, 1, 1, 1]


# ---------------------------------------------------------------------------
# Real optimizer runs
# ---------------------------------------------------------------------------


@pytest.fixture(params=["UTC", "Europe/Berlin"])
def local_clock(request: pytest.FixtureRequest, set_other_timezone: Callable[[str], str]) -> str:
    """Run the same local-wall-clock scenario in UTC and a DST-observing zone."""
    return set_other_timezone(request.param)


def configure_run(config: ConfigEOS, *, hours: int = 4, interval_sec: int = 900) -> None:
    config.merge_settings_from_dict(
        {
            "prediction": {"hours": 48},
            "optimization": {
                "algorithm": "GENETIC",
                "genetic": {
                    "horizon_hours": hours,
                    "tail_horizon_hours": 0,
                    "interval_sec": interval_sec,
                    "individuals": 12,
                    "generations": 10,
                    "terminal_value_mode": "FIXED",
                },
            },
            "feedintariff": {"direct_marketing_enabled": False},
        }
    )
    get_ems(init=True).set_start_datetime(to_datetime("2026-09-16T00:00:00"))


def run_parameters(*, hours: int = 4, **ev_extra: Any) -> GeneticOptimizationParameters:
    """EV of 4 kWh with a 4 kW charger: one full-power quarter hour stores 25 %.

    Electricity is cheap during the first hour (0.01 ct/Wh), expensive (0.1 ct/Wh)
    afterwards. No PV, no household load.
    """
    slots = hours * 4
    return GeneticOptimizationParameters.model_validate(
        {
            "ems": {
                "pv_prognose_wh": [0.0] * slots,
                "gesamtlast": [0.0] * slots,
                "strompreis_euro_pro_wh": [0.00001] * 4 + [0.001] * (slots - 4),
                "einspeiseverguetung_euro_pro_wh": [0.0] * slots,
                "preis_euro_pro_wh_akku": 0.0,
            },
            "inverter": {"device_id": "inv", "max_power_wh": 10000},
            "forecast_interval_seconds": 900,
            "pv_battery": None,
            "ev": {
                "device_id": "car",
                "capacity_wh": 4000,
                "initial_soc_percentage": 0,
                "min_soc_percentage": 25,
                "charging_efficiency": 1.0,
                "max_charge_power_w": 4000,
                "charge_rates": [0.0, 1.0],
                **ev_extra,
            },
        }
    )


def run(params: GeneticOptimizationParameters) -> tuple[GeneticOptimization, GeneticSolution]:
    optimizer = GeneticOptimization(fixed_seed=42)
    solution = optimizer.optimize_ems(params, ngen=3, individuals=12)
    return optimizer, solution


def assert_charged_after_arrival(solution: GeneticSolution, first_slot: int = 4) -> None:
    """No EV charging before ``first_slot``; the target is reached; costs add up.

    Every planned quarter hour charges 1000 Wh (25 %) at 0.001 EUR/Wh after the
    first hour. A full EV stops charging, so at most 4 slots are executed.
    """
    rates = solution.ev_charge_hours_float
    assert rates is not None
    assert rates[:first_slot] == [0.0] * first_slot
    charged_slots = sum(rates[first_slot:])
    assert 1 <= charged_slots <= 4
    assert solution.result.ev_soc_per_hour[: first_slot + 1] == [0.0] * (first_slot + 1)
    assert sum(solution.result.grid_consumption_wh_per_hour[:first_slot]) == 0.0
    assert sum(solution.result.grid_consumption_wh_per_hour) == pytest.approx(
        1000.0 * charged_slots
    )
    assert solution.result.total_costs == pytest.approx(1.0 * charged_slots)


def ev_genes(optimizer: GeneticOptimization, solution: GeneticSolution) -> list[float]:
    assert solution.start_solution is not None
    end = optimizer.control_end_slot
    return list(solution.start_solution[end : 2 * end])


def test_real_run_without_charge_start_uses_the_cheap_early_slots(
    config_eos: ConfigEOS, local_clock: str
) -> None:
    """Reference: without a charge start the EV charges in the cheap first hour."""
    configure_run(config_eos)
    _, solution = run(run_parameters())
    assert solution.ev_charge_hours_float is not None
    assert sum(solution.ev_charge_hours_float[:4]) == 1.0
    assert sum(solution.ev_charge_hours_float[4:]) == 0.0
    # 1000 Wh at 0.00001 EUR/Wh.
    assert solution.result.total_costs == pytest.approx(0.01)


def test_real_run_charges_only_after_charge_start(config_eos: ConfigEOS, local_clock: str) -> None:
    """The vehicle arrives at 01:00 local: the cheap first hour is not usable.

    The cheapest valid plan charges one quarter hour at full power after
    arrival: 1000 Wh at 0.001 EUR/Wh = 1.00 EUR.
    """
    configure_run(config_eos)
    optimizer, solution = run(run_parameters(charge_start_datetime="2026-09-16T01:00:00"))
    assert_charged_after_arrival(solution)
    # Plan, genes and simulated SoC agree: idle at the arrival SoC until 01:00.
    assert ev_genes(optimizer, solution)[:4] == [0] * 4
    # This seed finds the cheapest valid plan.
    assert solution.result.ev_soc_per_hour[-1] == 25.0
    assert solution.result.total_costs == pytest.approx(1.0)
    assert optimizer._ev_first_charge_slot == 4


@pytest.mark.parametrize("charge_rates", [[0.0, 1.0], [0.5, 1.0]])
def test_exported_plan_has_no_charge_instruction_before_charge_start(
    config_eos: ConfigEOS, local_clock: str, charge_rates: list[float]
) -> None:
    """The plan a client executes keeps the EV idle until the charge start at 01:00.

    Also without a zero charge rate, where the genes before the charge start
    still name a rate: the plan is built from the simulated charge array.
    """
    configure_run(config_eos)
    _, solution = run(
        run_parameters(charge_start_datetime="2026-09-16T01:00:00", charge_rates=charge_rates)
    )
    charge_start = to_datetime("2026-09-16T01:00:00")
    ev_items = [
        item for item in solution.energy_management_plan().instructions if item.resource_id == "car"
    ]
    instructions = [item for item in ev_items if isinstance(item, FRBCInstruction)]
    assert instructions
    assert len(instructions) == len(ev_items)
    assert instructions[0].execution_time == to_datetime("2026-09-16T00:00:00")
    for item in instructions:
        if item.execution_time < charge_start:
            assert str(item.operation_mode_id) == "IDLE"
    charging = [item for item in instructions if str(item.operation_mode_id) != "IDLE"]
    assert charging
    assert min(item.execution_time for item in charging) >= charge_start


def test_real_run_drops_charging_from_a_warm_start_before_charge_start(
    config_eos: ConfigEOS, local_clock: str
) -> None:
    """A previous plan that charged before arrival must not survive as warm start."""
    configure_run(config_eos)
    _, first = run(run_parameters())
    assert first.ev_charge_hours_float is not None
    assert sum(first.ev_charge_hours_float[:4]) == 1.0

    followup = run_parameters(charge_start_datetime="2026-09-16T01:00:00")
    followup.start_solution = first.start_solution
    followup.start_solution_datetime = first.start_solution_datetime
    optimizer, second = run(followup)
    assert_charged_after_arrival(second)
    assert ev_genes(optimizer, second)[:4] == [0] * 4
    assert second.result.ev_soc_per_hour[-1] >= 25.0


def test_genome_operators_never_charge_before_charge_start(
    config_eos: ConfigEOS, local_clock: str
) -> None:
    configure_run(config_eos)
    optimizer, _ = run(run_parameters(charge_start_datetime="2026-09-16T01:00:00"))
    end = optimizer.control_end_slot
    for _ in range(200):
        individual = optimizer.create_individual()
        assert individual[end : end + 4] == [0] * 4
        (mutated,) = optimizer.mutate(individual)
        assert mutated[end : end + 4] == [0] * 4
    # The seeding heuristic plans the 25 % target in the cheapest slots after arrival.
    for prefer_pv in (False, True):
        schedule = optimizer._heuristic_ev_schedule(prefer_pv=prefer_pv)
        assert schedule[:4] == [0] * 4
        assert sum(schedule[4:]) == 1


def test_evaluation_reports_the_genome_it_simulated(
    config_eos: ConfigEOS, local_clock: str
) -> None:
    """A charge gene before arrival is reset, so the genome matches the plan."""
    configure_run(config_eos)
    optimizer, _ = run(run_parameters(charge_start_datetime="2026-09-16T01:00:00"))
    end = optimizer.control_end_slot
    individual = [0] * end + [1, 0, 0, 0, 1] + [0] * (end - 5)
    result = optimizer.evaluate_inner(individual)
    assert individual[end:] == [0, 0, 0, 0, 1] + [0] * (end - 5)
    assert result["EAuto_SoC_pro_Stunde"].tolist()[:6] == [0.0] * 5 + [25.0]


def test_real_run_without_zero_charge_rate_still_idles_before_charge_start(
    config_eos: ConfigEOS, local_clock: str
) -> None:
    """Without a 0.0 charge rate every gene charges; arrival still blocks the slots.

    The EV then charges at 50 % or 100 % in every slot after arrival until full:
    4000 Wh in total, all at 0.001 EUR/Wh = 4.00 EUR.
    """
    configure_run(config_eos)
    optimizer, solution = run(
        run_parameters(charge_start_datetime="2026-09-16T01:00:00", charge_rates=[0.5, 1.0])
    )
    rates = solution.ev_charge_hours_float
    assert rates is not None
    assert rates[:4] == [0.0] * 4
    assert solution.result.ev_soc_per_hour[:5] == [0.0] * 5
    assert sum(solution.result.grid_consumption_wh_per_hour[:4]) == 0.0
    # Without a zero rate the genes of the slots before the charge start can
    # not be reset: they still name a charge rate, but the executed (reported)
    # plan is 0.0 there. From the charge start on, genes and plan agree up to
    # the slot where the full EV stops charging.
    genes = ev_genes(optimizer, solution)
    assert all(optimizer.ev_possible_charge_values[int(gene)] > 0.0 for gene in genes[:4])
    first_idle = next((slot for slot in range(4, 16) if rates[slot] == 0.0), 16)
    assert [optimizer.ev_possible_charge_values[int(gene)] for gene in genes[4:first_idle]] == (
        rates[4:first_idle]
    )
    assert sum(solution.result.grid_consumption_wh_per_hour) == pytest.approx(4000.0)
    assert solution.result.total_costs == pytest.approx(4.0)


def test_real_run_relaxes_a_deadline_before_charge_start(
    config_eos: ConfigEOS, local_clock: str
) -> None:
    """Departure 00:30 before arrival 01:00: target at the end of the horizon instead."""
    configure_run(config_eos)
    optimizer, solution = run(
        run_parameters(
            charge_start_datetime="2026-09-16T01:00:00",
            min_soc_deadline_datetime="2026-09-16T00:30:00",
        )
    )
    assert_charged_after_arrival(solution)
    assert solution.result.ev_soc_per_hour[-1] >= 25.0
    assert optimizer._ev_soc_deadline_slot is None


@pytest.mark.parametrize("run_start", ["2026-09-16T00:00:00", "2026-09-16T01:00:00"])
def test_relaxed_deadline_stays_relaxed_once_the_charge_start_is_reached(
    config_eos: ConfigEOS, local_clock: str, run_start: str
) -> None:
    """Arrival 01:00 with a departure before it, planned before and after the arrival.

    The run at 01:00 must not turn the deadline into a target due right now
    (which no plan can meet, so nothing would be charged); like the run at
    00:00 it requires the target at the end of the horizon.
    """
    configure_run(config_eos)
    get_ems(init=True).set_start_datetime(to_datetime(run_start))
    # The forecasts start at midnight; five hours cover both runs.
    optimizer, solution = run(
        run_parameters(
            hours=5,
            charge_start_datetime="2026-09-16T01:00:00",
            min_soc_deadline_datetime="2026-09-16T00:30:00",
        )
    )
    assert optimizer._ev_soc_deadline_slot is None
    assert solution.ev_charge_hours_float is not None
    assert sum(solution.ev_charge_hours_float) >= 1.0
    assert solution.result.ev_soc_per_hour[-1] >= 25.0
    assert solution.ev_obj is not None
    assert solution.ev_obj.soc_wh >= 1000.0 - 1e-6


def test_real_run_keeps_a_deadline_after_charge_start(
    config_eos: ConfigEOS, local_clock: str
) -> None:
    """Arrival 01:00, departure 01:30: only slots 4 and 5 count for the target.

    Prices from 01:30 on are cheaper than 01:00-01:30, which the optimizer must
    ignore: 1000 Wh at 0.001 EUR/Wh = 1.00 EUR in slot 4 or 5.
    """
    configure_run(config_eos)
    params = run_parameters(
        charge_start_datetime="2026-09-16T01:00:00",
        min_soc_deadline_datetime="2026-09-16T01:30:00",
    )
    params.ems.electricity_price_per_wh[6:] = [0.0001] * 10
    optimizer, solution = run(params)
    assert solution.ev_charge_hours_float is not None
    assert solution.ev_charge_hours_float[:4] == [0.0] * 4
    assert sum(solution.ev_charge_hours_float[4:6]) == 1.0
    assert solution.result.ev_soc_per_hour[6] == 25.0
    assert solution.result.total_costs == pytest.approx(1.0)
    assert (optimizer._ev_first_charge_slot, optimizer._ev_soc_deadline_slot) == (4, 6)


def ev_soc_at_deadline(optimizer: GeneticOptimization, solution: GeneticSolution) -> float:
    """SoC the target was checked against (at the deadline, inside its slot)."""
    return optimizer._ev_soc_at_deadline(
        {"EAuto_SoC_pro_Stunde": solution.result.ev_soc_per_hour}, 0
    )


def test_real_run_on_the_hourly_grid_charges_before_a_departure_inside_a_slot(
    config_eos: ConfigEOS, local_clock: str
) -> None:
    """Arrival 01:07, departure 02:50 on the hourly grid.

    02:00-03:00 is the only hour after the arrival, and the vehicle leaves 50
    minutes into it. The hour 03:00-04:00 is cheaper but after the departure:
    the plan has to charge in 02:00-03:00, of which 5/6 count (4 kWh stored in
    the hour, 3.33 kWh by 02:50).
    """
    configure_run(config_eos, interval_sec=3600)
    params = run_parameters(
        charge_start_datetime="2026-09-16T01:07:00",
        min_soc_deadline_datetime="2026-09-16T02:50:00",
    )
    params.ems.electricity_price_per_wh[12:] = [0.00001] * 4
    optimizer, solution = run(params)
    assert optimizer._ev_first_charge_slot == 2
    assert (optimizer._ev_soc_deadline_slot, optimizer._ev_soc_deadline_share) == (
        2,
        pytest.approx(5 / 6),
    )
    assert solution.ev_charge_hours_float == [0.0, 0.0, 1.0, 0.0]
    assert ev_soc_at_deadline(optimizer, solution) == pytest.approx(100.0 * 5 / 6)


def test_real_run_on_the_quarter_hour_grid_charges_before_a_departure_inside_a_slot(
    config_eos: ConfigEOS, local_clock: str
) -> None:
    """The same window on the 15-minute grid: slots 5-10 and 5 minutes of slot 11."""
    configure_run(config_eos)
    params = run_parameters(
        charge_start_datetime="2026-09-16T01:07:00",
        min_soc_deadline_datetime="2026-09-16T02:50:00",
    )
    params.ems.electricity_price_per_wh[12:] = [0.00001] * 4
    optimizer, solution = run(params)
    assert (optimizer._ev_first_charge_slot, optimizer._ev_soc_deadline_slot) == (5, 11)
    rates = solution.ev_charge_hours_float
    assert rates is not None
    assert sum(rates[:5]) == 0.0
    assert sum(rates[12:]) == 0.0
    assert ev_soc_at_deadline(optimizer, solution) >= 25.0


def test_real_run_counts_the_minutes_before_a_departure_in_the_first_usable_slot(
    config_eos: ConfigEOS, local_clock: str
) -> None:
    """Arrival 01:00, departure 01:05: only a third of slot 4 lies in between.

    The target (25 %, one full quarter hour) can not be reached, but the five
    minutes are used; the cheaper slots after the departure are not.
    """
    configure_run(config_eos)
    params = run_parameters(
        charge_start_datetime="2026-09-16T01:00:00",
        min_soc_deadline_datetime="2026-09-16T01:05:00",
    )
    params.ems.electricity_price_per_wh[5:] = [0.0001] * 11
    optimizer, solution = run(params)
    assert (optimizer._ev_soc_deadline_slot, optimizer._ev_soc_deadline_share) == (
        4,
        pytest.approx(1 / 3),
    )
    assert solution.ev_charge_hours_float == [0.0] * 4 + [1.0] + [0.0] * 11
    assert ev_soc_at_deadline(optimizer, solution) == pytest.approx(25.0 / 3)


def test_real_run_without_time_to_charge_before_the_departure_plans_nothing(
    config_eos: ConfigEOS, local_clock: str
) -> None:
    """Arrival 01:07, departure 01:50 on the hourly grid: 01:00-02:00 begins before
    the arrival and is not used, so the vehicle can not charge before it leaves.
    The deadline is kept; charging after the departure would earn nothing."""
    configure_run(config_eos, interval_sec=3600)
    params = run_parameters(
        charge_start_datetime="2026-09-16T01:07:00",
        min_soc_deadline_datetime="2026-09-16T01:50:00",
    )
    params.ems.electricity_price_per_wh[8:] = [0.00001] * 8
    optimizer, solution = run(params)
    assert optimizer._ev_soc_deadline_slot == 1
    assert solution.ev_charge_hours_float == [0.0] * 4
    assert solution.result.total_costs == 0.0


def test_real_run_with_charge_start_after_the_horizon_plans_no_ev_charging(
    config_eos: ConfigEOS, local_clock: str
) -> None:
    """The vehicle is not there within the control horizon: nothing to optimize."""
    configure_run(config_eos)
    optimizer, solution = run(run_parameters(charge_start_datetime="2026-09-16T06:00:00"))
    assert solution.ev_charge_hours_float is None
    assert solution.result.ev_soc_per_hour == [0.0] * 16
    assert solution.result.total_costs == 0.0
    assert not optimizer.optimize_ev
    assert optimizer._ev_first_charge_slot == optimizer.control_end_slot


@pytest.mark.parametrize("charge_start", ["2026-09-16T00:00:00", "2026-09-15T18:00:00"])
def test_reached_charge_start_gives_the_identical_plan(
    config_eos: ConfigEOS, local_clock: str, charge_start: str
) -> None:
    """A charge start at or before the run start changes nothing, bit for bit."""
    configure_run(config_eos)
    _, reference = run(run_parameters())
    _, solution = run(run_parameters(charge_start_datetime=charge_start))
    assert solution.start_solution == reference.start_solution
    assert solution.ev_charge_hours_float == reference.ev_charge_hours_float
    assert solution.result.total_costs == reference.result.total_costs
    assert solution.result.ev_soc_per_hour == reference.result.ev_soc_per_hour
    assert solution.extra_data == reference.extra_data
