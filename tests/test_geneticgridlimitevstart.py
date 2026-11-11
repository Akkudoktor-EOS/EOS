"""Grid import limit together with the EV charge start of #1532.

The EV charge start (``charge_start_datetime``) lets the vehicle charge
neither before its charge start nor in the part of slot 0 that has passed
(``Battery.first_slot_share``). These tests need both pull requests and are
skipped until the EV charge start is available.
"""

import inspect
from typing import Any, Optional

import numpy as np
import pytest
from deap import creator

from akkudoktoreos.config.config import ConfigEOS
from akkudoktoreos.core.coreabc import get_ems
from akkudoktoreos.devices.genetic.battery import (
    Battery,
    ElectricVehicleParameters,
    SolarPanelBatteryParameters,
)
from akkudoktoreos.devices.genetic.inverter import Inverter, InverterParameters
from akkudoktoreos.optimization.genetic.genetic import (
    GeneticOptimization,
    GeneticSimulation,
)
from akkudoktoreos.optimization.genetic.geneticparams import (
    GeneticEnergyManagementParameters,
    GeneticOptimizationParameters,
)
from akkudoktoreos.utils.datetimeutil import to_datetime

pytestmark = pytest.mark.skipif(
    "charge_start_datetime" not in ElectricVehicleParameters.model_fields
    or "first_slot_share" not in inspect.signature(Battery.__init__).parameters,
    reason="needs the EV charge start (#1532)",
)

LIMIT_W = 8000.0
EV_POWER_W = 7000.0
LOAD_SLOT0_WH = 3000.0


def _simulate_slot0(
    first_slot_share: float, *, ac_factor: float = 0.0
) -> tuple[dict[str, Any], Battery]:
    """Hourly, three slots, 8 kW limit; 3 kW house load in slot 0, EV asked at 7 kW.

    The stationary battery is empty and held: it cannot shave the import.
    """
    slots = 3
    ev_options: dict[str, Any] = {"first_slot_share": first_slot_share}
    battery = Battery(
        SolarPanelBatteryParameters(
            device_id="battery1",
            capacity_wh=10000,
            initial_soc_percentage=0,
            min_soc_percentage=0,
            max_soc_percentage=100,
            charging_efficiency=1.0,
            discharging_efficiency=1.0,
            max_charge_power_w=5000,
        ),
        prediction_hours=slots,
    )
    inverter = Inverter(
        InverterParameters(
            device_id="inverter1",
            max_power_wh=10000,
            battery_id="battery1",
            ac_to_dc_efficiency=1.0,
            dc_to_ac_efficiency=1.0,
            max_grid_import_power_w=LIMIT_W,
        ),
        battery=battery,
    )
    ev = Battery(
        ElectricVehicleParameters(
            device_id="ev1",
            capacity_wh=60000,
            charging_efficiency=1.0,
            discharging_efficiency=1.0,
            max_charge_power_w=EV_POWER_W,
            initial_soc_percentage=0,
            min_soc_percentage=100,
            charge_rates=[0.0, 0.25, 0.5, 0.75, 1.0],
        ),
        prediction_hours=slots,
        # A keyword of #1532: passed this way to type-check without it.
        **ev_options,
    )
    simulation = GeneticSimulation()
    simulation.prepare(
        GeneticEnergyManagementParameters(
            pv_forecast_wh=[0.0] * slots,
            electricity_price_per_wh=[0.0003] * slots,
            feed_in_tariff_per_wh=0.00008,
            price_per_wh_battery=0.0,
            total_load=[LOAD_SLOT0_WH, 1000.0, 1000.0],
        ),
        optimization_hours=slots,
        prediction_hours=slots,
        inverter=inverter,
        ev=ev,
    )
    simulation.reset()
    simulation.ac_charge_hours = np.array([ac_factor, 0.0, 0.0])
    simulation.dc_charge_hours = np.ones(slots)
    simulation.bat_discharge_hours = np.zeros(slots)
    simulation.ev_charge_hours = np.array([1.0, 1.0, 1.0])
    return simulation.simulate(0), ev


class TestFirstSlotShare:
    @pytest.mark.parametrize("share", [1.0, 0.5, 0.25])
    def test_ev_rate_of_slot0_fits_the_limit_in_the_part_ahead(self, config_eos, share):
        """The EV charges only in the part of slot 0 ahead; the limit is a power.

        3 kW load plus 7 kW EV exceed the 8 kW limit in that part, whatever
        its length; only the half rate fits (3 + 3.5 kW). The slot is
        simulated again at the lower rate with the share kept.
        """
        result, ev = _simulate_slot0(share)

        assert result["ev_grid_limited_charge_factors"] == {0: 0.5}
        assert getattr(ev, "first_slot_share") == share
        # 3.5 kW over the part of slot 0 ahead.
        assert result["EAuto_SoC_pro_Stunde"][1] == pytest.approx(
            0.5 * EV_POWER_W * share / 60000 * 100
        )
        assert result["Netzbezug_Wh_pro_Stunde"][0] == pytest.approx(
            LOAD_SLOT0_WH + 0.5 * EV_POWER_W * share
        )

    @pytest.mark.parametrize("share", [1.0, 0.25])
    def test_ac_charge_of_slot0_leaves_room_for_the_ev_power(self, config_eos, share):
        """AC charging in slot 0 fills only what load and EV power leave of the limit.

        Load 3 kW and EV 3.5 kW leave 1.5 kW of the 8 kW limit for the battery.
        """
        result, _ = _simulate_slot0(share, ac_factor=1.0)

        assert result["grid_limited_ac_charge_wh"] == {0: pytest.approx(1500.0)}


EV_SLOTS = 24  # six hours of quarter hours from 00:00
EV_CHARGE_START = 8  # 02:00
EV_DEADLINE = 16  # 04:00
EV_LIMIT_W = 9000.0
EV_CHEAP_SLOTS = range(4, 12)  # 01:00-03:00, half of them before the charge start


def _optimizer(config_eos: ConfigEOS, *, charge_start: Optional[str]) -> GeneticOptimization:
    """Quarter hours, 1 kW load, EV 11 kW at 0 / 0.5 / 1, 18 % (10.8 kWh) by 04:00.

    At the 9 kW limit only the half rate fits next to the load. The battery
    is at its minimum SoC and cannot shave.
    """
    prices = [0.0003] * EV_SLOTS
    for slot in EV_CHEAP_SLOTS:
        prices[slot] = 0.00015
    config_eos.merge_settings_from_dict(
        {
            "prediction": {"hours": 48},
            "optimization": {
                "algorithm": "GENETIC",
                "genetic": {
                    "horizon_hours": EV_SLOTS // 4,
                    "tail_horizon_hours": 0,
                    "interval_sec": 900,
                    "terminal_value_mode": "FIXED",
                    "terminal_value_euro_per_kwh": 0.0,
                },
            },
            "feedintariff": {"direct_marketing_enabled": False},
        }
    )
    get_ems(init=True).set_start_datetime(to_datetime("2026-11-05T00:00:00"))
    parameters = GeneticOptimizationParameters.model_validate(
        {
            "ems": {
                "pv_forecast_wh": [0.0] * EV_SLOTS,
                "total_load": [250.0] * EV_SLOTS,
                "electricity_price_per_wh": prices,
                "feed_in_tariff_per_wh": 0.0,
                "price_per_wh_battery": 0.0,
            },
            "pv_battery": {
                "device_id": "battery1",
                "capacity_wh": 5000,
                "max_charge_power_w": 2500,
                "initial_soc_percentage": 10,
                "min_soc_percentage": 10,
            },
            "inverter": {
                "device_id": "inverter1",
                "max_power_wh": 20000,
                "battery_id": "battery1",
                "max_ac_charge_power_w": 0,
                "max_grid_import_power_w": EV_LIMIT_W,
            },
            "ev": {
                "device_id": "car",
                "capacity_wh": 60000,
                "initial_soc_percentage": 0,
                "min_soc_percentage": 18,
                "max_soc_percentage": 100,
                "charging_efficiency": 1.0,
                "max_charge_power_w": 11000,
                "charge_rates": [0.0, 0.5, 1.0],
                "min_soc_deadline_datetime": "2026-11-05T04:00:00",
                "charge_start_datetime": charge_start,
            },
            "forecast_interval_seconds": 900,
        }
    )
    optimizer = GeneticOptimization(fixed_seed=1)
    optimizer.optimize_ems(parameters, ngen=1, individuals=10)
    return optimizer


def _incumbent(optimizer: GeneticOptimization) -> list[int]:
    """Battery idle, EV at the full rate in the four cheap slots after the charge start.

    The limit lowers each of them to the half rate (5.5 of 11 kWh charged).
    """
    rates = [1.0 if EV_CHARGE_START <= slot < 12 else 0.0 for slot in range(EV_SLOTS)]
    ev_genes = np.array(
        [optimizer.ev_possible_charge_values.index(rate) for rate in rates], dtype=np.int_
    )
    return optimizer.merge_individual(
        np.zeros(optimizer.control_end_slot, dtype=np.int_), ev_genes, None
    )


def _ev_rates(optimizer: GeneticOptimization, genome: list[int]) -> list[float]:
    _, ev_genes, _ = optimizer.split_individual(genome)
    assert ev_genes is not None
    return [optimizer.ev_possible_charge_values[index] for index in ev_genes]


class TestChargeStart:
    def test_limit_search_moves_no_charge_before_the_charge_start(self, config_eos):
        """The cut energy goes to slots after the charge start, not to cheaper ones before it."""
        optimizer = _optimizer(config_eos, charge_start="2026-11-05T02:00:00")
        incumbent = _incumbent(optimizer)

        candidates = [
            _ev_rates(optimizer, genome)
            for genome in optimizer._grid_limit_ev_shift_candidates(incumbent)
        ]

        assert candidates
        for rates in candidates:
            assert rates[:EV_CHARGE_START] == [0.0] * EV_CHARGE_START
        # Slot 8 down to the half rate, slot 12 (the cheapest slot with room
        # from the charge start on) up to it.
        expected = [0.0] * EV_SLOTS
        expected[8] = 0.5
        expected[9:12] = [1.0, 1.0, 1.0]
        expected[12] = 0.5
        assert expected in candidates

    def test_limit_search_reaches_the_target_after_the_charge_start(self, config_eos):
        optimizer = _optimizer(config_eos, charge_start="2026-11-05T02:00:00")
        incumbent = creator.Individual(_incumbent(optimizer))
        cut = optimizer.evaluate_inner(list(incumbent))
        assert cut["EAuto_SoC_pro_Stunde"][EV_DEADLINE] < 18.0

        best, _, improvements, initial, final = optimizer._locally_improve_grid_export(
            incumbent, max_evaluations=96
        )

        result = optimizer.evaluate_inner(list(best))
        assert improvements > 0
        assert final < initial
        assert max(result["Netzbezug_Wh_pro_Stunde"]) <= EV_LIMIT_W / 4 + 1e-6
        assert result["EAuto_SoC_pro_Stunde"][EV_DEADLINE] >= 18.0 - 1e-6
        rates = _ev_rates(optimizer, best)
        assert rates[:EV_CHARGE_START] == [0.0] * EV_CHARGE_START
        assert all(rate == 0.0 for rate in rates[EV_DEADLINE:])
