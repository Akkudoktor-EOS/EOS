from typing import Any, ClassVar, Dict, Optional, Union

import numpy as np
from numpydantic import NDArray, Shape
from pendulum import DateTime
from pydantic import ConfigDict, Field, computed_field, field_validator, model_validator
from typing_extensions import Self

from akkudoktoreos.core.coreabc import ConfigMixin, PredictionMixin, SingletonMixin
from akkudoktoreos.core.logging import get_logger
from akkudoktoreos.core.pydantic import ParametersBaseModel, PydanticBaseModel
from akkudoktoreos.devices.battery import Battery
from akkudoktoreos.devices.generic import HomeAppliance
from akkudoktoreos.devices.inverter import Inverter
from akkudoktoreos.utils.datetimeutil import to_datetime
from akkudoktoreos.utils.utils import NumpyEncoder

logger = get_logger(__name__)


class EnergieManagementSystemParameters(ParametersBaseModel):
    pv_prediction_wh: list[float] = Field(
        description="An array of floats representing the forecasted photovoltaic output in watts for different time intervals."
    )
    electricity_price_euro_per_wh: list[float] = Field(
        description="An array of floats representing the electricity price in euros per watt-hour for different time intervals."
    )
    feed_in_tariff_euro_per_wh: list[float] | float = Field(
        description="A float or array of floats representing the feed-in compensation in euros per watt-hour."
    )
    price_euro_per_wh_battery: float = Field(
        description="A float representing the cost of battery energy per watt-hour."
    )
    gesamtlast: list[float] = Field(
        description="An array of floats representing the total load (consumption) in watts for different time intervals."
    )

    @model_validator(mode="after")
    def validate_list_length(self) -> Self:
        pv_prognose_length = len(self.pv_prediction_wh)
        if (
            pv_prognose_length != len(self.electricity_price_euro_per_wh)
            or pv_prognose_length != len(self.gesamtlast)
            or (
                isinstance(self.feed_in_tariff_euro_per_wh, list)
                and pv_prognose_length != len(self.feed_in_tariff_euro_per_wh)
            )
        ):
            raise ValueError("Input lists have different lengths")
        return self


class SimulationResult(ParametersBaseModel):
    """This object contains the results of the simulation and provides insights into various parameters over the entire forecast period."""

    load_wh_per_hour: list[Optional[float]] = Field(description="TBD")
    ev_soc_per_hour: list[Optional[float]] = Field(
        description="The state of charge of the EV for each hour."
    )
    revenue_euro_per_hour: list[Optional[float]] = Field(
        description="The revenue from grid feed-in or other sources in euros per hour."
    )
    total_losses: float = Field(
        description="The total losses in watt-hours over the entire period."
    )
    total_balance_euro: float = Field(
        description="The total balance of revenues minus costs in euros."
    )
    total_revenue_euro: float = Field(description="The total revenues in euros.")
    total_costs_euro: float = Field(description="The total costs in euros.")
    Home_appliance_wh_per_hour: list[Optional[float]] = Field(
        description="The energy consumption of a household appliance in watt-hours per hour."
    )
    cost_euro_per_hour: list[Optional[float]] = Field(description="The costs in euros per hour.")
    grid_demand_wh_per_hour: list[Optional[float]] = Field(
        description="The grid energy drawn in watt-hours per hour."
    )
    grid_feed_in_wh_per_hour: list[Optional[float]] = Field(
        description="The energy fed into the grid in watt-hours per hour."
    )
    losses_per_hour: list[Optional[float]] = Field(description="The losses in watt-hours per hour.")
    battery_soc_per_hour: list[Optional[float]] = Field(
        description="The state of charge of the battery (not the EV) in percentage per hour."
    )
    Electricity_price: list[Optional[float]] = Field(
        description="Used Electricity Price, including predictions"
    )

    @field_validator(
        "load_wh_per_hour",
        "grid_feed_in_wh_per_hour",
        "battery_soc_per_hour",
        "grid_demand_wh_per_hour",
        "cost_euro_per_hour",
        "revenue_euro_per_hour",
        "ev_soc_per_hour",
        "losses_per_hour",
        "Home_appliance_wh_per_hour",
        "Electricity_price",
        mode="before",
    )
    def convert_numpy(cls, field: Any) -> Any:
        return NumpyEncoder.convert_numpy(field)[0]


class EnergieManagementSystem(SingletonMixin, ConfigMixin, PredictionMixin, PydanticBaseModel):
    # Disable validation on assignment to speed up simulation runs.
    model_config = ConfigDict(
        validate_assignment=False,
    )

    # Start datetime.
    _start_datetime: ClassVar[Optional[DateTime]] = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def start_datetime(self) -> DateTime:
        """The starting datetime of the current or latest energy management."""
        if EnergieManagementSystem._start_datetime is None:
            EnergieManagementSystem.set_start_datetime()
        return EnergieManagementSystem._start_datetime

    @classmethod
    def set_start_datetime(cls, start_datetime: Optional[DateTime] = None) -> DateTime:
        if start_datetime is None:
            start_datetime = to_datetime()
        cls._start_datetime = start_datetime.set(minute=0, second=0, microsecond=0)
        return cls._start_datetime

    # -------------------------
    # TODO: Take from prediction
    # -------------------------

    load_energy_array: Optional[NDArray[Shape["*"], float]] = Field(
        default=None,
        description="An array of floats representing the total load (consumption) in watts for different time intervals.",
    )
    pv_prediction_wh: Optional[NDArray[Shape["*"], float]] = Field(
        default=None,
        description="An array of floats representing the forecasted photovoltaic output in watts for different time intervals.",
    )
    elect_price_hourly: Optional[NDArray[Shape["*"], float]] = Field(
        default=None,
        description="An array of floats representing the electricity price in euros per watt-hour for different time intervals.",
    )
    elect_revenue_per_hour_arr: Optional[NDArray[Shape["*"], float]] = Field(
        default=None,
        description="An array of floats representing the feed-in compensation in euros per watt-hour.",
    )

    # -------------------------
    # TODO: Move to devices
    # -------------------------

    battery: Optional[Battery] = Field(default=None, description="TBD.")
    ev: Optional[Battery] = Field(default=None, description="TBD.")
    home_appliance: Optional[HomeAppliance] = Field(default=None, description="TBD.")
    inverter: Optional[Inverter] = Field(default=None, description="TBD.")

    # -------------------------
    # TODO: Move to devices
    # -------------------------

    ac_charge_hours: Optional[NDArray[Shape["*"], float]] = Field(default=None, description="TBD")
    dc_charge_hours: Optional[NDArray[Shape["*"], float]] = Field(default=None, description="TBD")
    ev_charge_hours: Optional[NDArray[Shape["*"], float]] = Field(default=None, description="TBD")

    def set_parameters(
        self,
        parameters: EnergieManagementSystemParameters,
        ev: Optional[Battery] = None,
        home_appliance: Optional[HomeAppliance] = None,
        inverter: Optional[Inverter] = None,
    ) -> None:
        self.load_energy_array = np.array(parameters.gesamtlast, float)
        self.pv_prediction_wh = np.array(parameters.pv_prediction_wh, float)
        self.elect_price_hourly = np.array(parameters.electricity_price_euro_per_wh, float)
        self.elect_revenue_per_hour_arr = (
            parameters.feed_in_tariff_euro_per_wh
            if isinstance(parameters.feed_in_tariff_euro_per_wh, list)
            else np.full(len(self.load_energy_array), parameters.feed_in_tariff_euro_per_wh, float)
        )
        if inverter is not None:
            self.battery = inverter.battery
        else:
            self.battery = None
        self.ev = ev
        self.home_appliance = home_appliance
        self.inverter = inverter
        self.ac_charge_hours = np.full(self.config.prediction_hours, 0.0)
        self.dc_charge_hours = np.full(self.config.prediction_hours, 1.0)
        self.ev_charge_hours = np.full(self.config.prediction_hours, 0.0)

    def set_akku_discharge_hours(self, ds: np.ndarray) -> None:
        if self.battery is not None:
            self.battery.set_discharge_per_hour(ds)

    def set_akku_ac_charge_hours(self, ds: np.ndarray) -> None:
        self.ac_charge_hours = ds

    def set_akku_dc_charge_hours(self, ds: np.ndarray) -> None:
        self.dc_charge_hours = ds

    def set_ev_charge_hours(self, ds: np.ndarray) -> None:
        self.ev_charge_hours = ds

    def set_home_appliance_start(self, ds: int, global_start_hour: int = 0) -> None:
        if self.home_appliance is not None:
            self.home_appliance.set_starting_time(ds, global_start_hour=global_start_hour)

    def reset(self) -> None:
        if self.ev:
            self.ev.reset()
        if self.battery:
            self.battery.reset()

    def run(
        self,
        start_hour: Optional[int] = None,
        force_enable: Optional[bool] = False,
        force_update: Optional[bool] = False,
    ) -> None:
        """Run energy management.

        Sets `start_datetime` to current hour, updates the configuration and the prediction, and
        starts simulation at current hour.

        Args:
            start_hour (int, optional): Hour to take as start time for the energy management. Defaults
            to now.
            force_enable (bool, optional): If True, forces to update even if disabled. This
            is mostly relevant to prediction providers.
            force_update (bool, optional): If True, forces to update the data even if still cached.
        """
        self.set_start_hour(start_hour=start_hour)
        self.config.update()

        # Check for run definitions
        if self.start_datetime is None:
            error_msg = "Start datetime unknown."
            logger.error(error_msg)
            raise ValueError(error_msg)
        if self.config.prediction_hours is None:
            error_msg = "Prediction hours unknown."
            logger.error(error_msg)
            raise ValueError(error_msg)
        if self.config.optimisation_hours is None:
            error_msg = "Optimisation hours unknown."
            logger.error(error_msg)
            raise ValueError(error_msg)

        self.prediction.update_data(force_enable=force_enable, force_update=force_update)
        # TODO: Create optimisation problem that calls into devices.update_data() for simulations.

    def set_start_hour(self, start_hour: Optional[int] = None) -> None:
        """Sets start datetime to given hour.

        Args:
            start_hour (int, optional): Hour to take as start time for the energy management. Defaults
            to now.
        """
        if start_hour is None:
            self.set_start_datetime()
        else:
            start_datetime = to_datetime().set(hour=start_hour, minute=0, second=0, microsecond=0)
            self.set_start_datetime(start_datetime)

    def simulate_start_now(self) -> dict[str, Any]:
        start_hour = to_datetime().now().hour
        return self.simulate(start_hour)

    def simulate(self, start_hour: int) -> dict[str, Any]:
        """hour.

        battery_soc_per_hour begin of the hour, initial hour state!
        last_wh_pro_stunde integral of  last hour (end state)
        """
        # Check for simulation integrity
        missing_data = []

        if self.load_energy_array is None:
            missing_data.append("Load Curve")
        if self.pv_prediction_wh is None:
            missing_data.append("PV Forecast")
        if self.elect_price_hourly is None:
            missing_data.append("Electricity Price")
        if self.ev_charge_hours is None:
            missing_data.append("EV Charge Hours")
        if self.ac_charge_hours is None:
            missing_data.append("AC Charge Hours")
        if self.dc_charge_hours is None:
            missing_data.append("DC Charge Hours")
        if self.elect_revenue_per_hour_arr is None:
            missing_data.append("Feed-in Tariff")

        if missing_data:
            error_msg = "Mandatory data missing - " + ", ".join(missing_data)
            logger.error(error_msg)
            raise ValueError(error_msg)
        else:
            # make mypy happy
            assert self.load_energy_array is not None
            assert self.pv_prediction_wh is not None
            assert self.elect_price_hourly is not None
            assert self.ev_charge_hours is not None
            assert self.ac_charge_hours is not None
            assert self.dc_charge_hours is not None
            assert self.elect_revenue_per_hour_arr is not None

        load_energy_array = self.load_energy_array

        if not (
            len(load_energy_array) == len(self.pv_prediction_wh) == len(self.elect_price_hourly)
        ):
            error_msg = f"Array sizes do not match: Load Curve = {len(load_energy_array)}, PV Forecast = {len(self.pv_prediction_wh)}, Electricity Price = {len(self.elect_price_hourly)}"
            logger.error(error_msg)
            raise ValueError(error_msg)

        # Optimized total hours calculation
        end_hour = len(load_energy_array)
        total_hours = end_hour - start_hour

        # Pre-allocate arrays for the results, optimized for speed
        loads_energy_per_hour = np.full((total_hours), np.nan)
        feedin_energy_per_hour = np.full((total_hours), np.nan)
        consumption_energy_per_hour = np.full((total_hours), np.nan)
        costs_per_hour = np.full((total_hours), np.nan)
        revenue_per_hour = np.full((total_hours), np.nan)
        soc_per_hour = np.full((total_hours), np.nan)  # Hour End State
        soc_ev_per_hour = np.full((total_hours), np.nan)
        losses_wh_per_hour = np.full((total_hours), np.nan)
        home_appliance_wh_per_hour = np.full((total_hours), np.nan)
        electricity_price_per_hour = np.full((total_hours), np.nan)

        # Set initial state
        if self.battery:
            soc_per_hour[0] = self.battery.current_soc_percentage()
        if self.ev:
            soc_ev_per_hour[0] = self.ev.current_soc_percentage()

        for hour in range(start_hour, end_hour):
            hour_since_now = hour - start_hour

            # save begin states
            if self.battery:
                soc_per_hour[hour_since_now] = self.battery.current_soc_percentage()
            else:
                soc_per_hour[hour_since_now] = 0.0
            if self.ev:
                soc_ev_per_hour[hour_since_now] = self.ev.current_soc_percentage()

            # Accumulate loads and PV generation
            consumption = self.load_energy_array[hour]
            losses_wh_per_hour[hour_since_now] = 0.0

            # Home appliances
            if self.home_appliance:
                ha_load = self.home_appliance.get_load_for_hour(hour)
                consumption += ha_load
                home_appliance_wh_per_hour[hour_since_now] = ha_load

            # EV handling
            if self.ev:
                if self.ev_charge_hours[hour] > 0:
                    loaded_energy_ev, ev_losses = self.ev.charge_energy(
                        None, hour, relative_power=self.ev_charge_hours[hour]
                    )
                    consumption += loaded_energy_ev
                    losses_wh_per_hour[hour_since_now] += ev_losses

            # Process inverter logic
            energy_feedin_grid_actual, energy_consumption_grid_actual, losses, eigenverbrauch = (
                0.0,
                0.0,
                0.0,
                0.0,
            )
            if self.battery:
                self.battery.set_charge_allowed_for_hour(self.dc_charge_hours[hour], hour)
            if self.inverter:
                energy_produced = self.pv_prediction_wh[hour]
                (
                    energy_feedin_grid_actual,
                    energy_consumption_grid_actual,
                    losses,
                    eigenverbrauch,
                ) = self.inverter.process_energy(energy_produced, consumption, hour)

            # AC PV Battery Charge
            if self.battery and self.ac_charge_hours[hour] > 0.0:
                self.battery.set_charge_allowed_for_hour(1, hour)
                battery_charged_energy_actual, battery_losses_actual = self.battery.charge_energy(
                    None, hour, relative_power=self.ac_charge_hours[hour]
                )
                # print(hour, " ", battery_charged_energy_actual, " ",self.ac_charge_hours[hour]," ",self.battery.current_soc_percentage())
                consumption += battery_charged_energy_actual
                consumption += battery_losses_actual
                energy_consumption_grid_actual += battery_charged_energy_actual
                energy_consumption_grid_actual += battery_losses_actual
                losses_wh_per_hour[hour_since_now] += battery_losses_actual

            feedin_energy_per_hour[hour_since_now] = energy_feedin_grid_actual
            consumption_energy_per_hour[hour_since_now] = energy_consumption_grid_actual
            losses_wh_per_hour[hour_since_now] += losses
            loads_energy_per_hour[hour_since_now] = consumption
            electricity_price_per_hour[hour_since_now] = self.elect_price_hourly[hour]

            # Financial calculations
            costs_per_hour[hour_since_now] = (
                energy_consumption_grid_actual * self.elect_price_hourly[hour]
            )
            revenue_per_hour[hour_since_now] = (
                energy_feedin_grid_actual * self.elect_revenue_per_hour_arr[hour]
            )

        # Total cost and return
        gesamtkosten_euro = np.nansum(costs_per_hour) - np.nansum(revenue_per_hour)

        # Prepare output dictionary
        out: Dict[str, Union[np.ndarray, float]] = {
            "load_wh_per_hour": loads_energy_per_hour,
            "grid_feed_in_wh_per_hour": feedin_energy_per_hour,
            "grid_demand_wh_per_hour": consumption_energy_per_hour,
            "cost_euro_per_hour": costs_per_hour,
            "battery_soc_per_hour": soc_per_hour,
            "revenue_euro_per_hour": revenue_per_hour,
            "total_balance_euro": gesamtkosten_euro,
            "ev_soc_per_hour": soc_ev_per_hour,
            "total_revenue_euro": np.nansum(revenue_per_hour),
            "total_costs_euro": np.nansum(costs_per_hour),
            "losses_per_hour": losses_wh_per_hour,
            "total_losses": np.nansum(losses_wh_per_hour),
            "Home_appliance_wh_per_hour": home_appliance_wh_per_hour,
            "Electricity_price": electricity_price_per_hour,
        }

        return out


# Initialize the Energy Management System, it is a singleton.
ems = EnergieManagementSystem()


def get_ems() -> EnergieManagementSystem:
    """Gets the EOS Energy Management System."""
    return ems
