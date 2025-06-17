"""GENETIC algorithm paramters.

This module defines the Pydantic-based configuration and input parameter models
used in the energy optimization routines, including photovoltaic forecasts,
electricity pricing, and system component parameters.

It also provides a method to assemble these parameters from predictions,
forecasts, and fallback defaults, preparing them for optimization runs.
"""

from typing import Optional, Union

from loguru import logger
from pydantic import Field, field_validator, model_validator
from typing_extensions import Self

from akkudoktoreos.core.coreabc import (
    ConfigMixin,
    MeasurementMixin,
    PredictionMixin,
)
from akkudoktoreos.optimization.geneticabc import GeneticParametersBaseModel
from akkudoktoreos.optimization.geneticdevices import (
    ElectricVehicleParameters,
    HomeApplianceParameters,
    InverterParameters,
    SolarPanelBatteryParameters,
)
from akkudoktoreos.utils.datetimeutil import to_duration

# Do not import directly from akkudoktoreos.core.coreabc
# EnergyManagementSystemMixin - Creates circular dependency with ems.py
# StartMixin                  - Creates circular dependency with ems.py


class GeneticEnergyManagementParameters(GeneticParametersBaseModel):
    """Encapsulates energy-related forecasts and costs used in GENETIC optimization."""

    pv_prognose_wh: list[float] = Field(
        description="An array of floats representing the forecasted photovoltaic output in watts for different time intervals."
    )
    strompreis_euro_pro_wh: list[float] = Field(
        description="An array of floats representing the electricity price in euros per watt-hour for different time intervals."
    )
    einspeiseverguetung_euro_pro_wh: Union[list[float], float] = Field(
        description="A float or array of floats representing the feed-in compensation in euros per watt-hour."
    )
    preis_euro_pro_wh_akku: float = Field(
        description="A float representing the cost of battery energy per watt-hour."
    )
    gesamtlast: list[float] = Field(
        description="An array of floats representing the total load (consumption) in watts for different time intervals."
    )

    @model_validator(mode="after")
    def validate_list_length(self) -> Self:
        """Validate that all input lists are of the same length.

        Raises:
            ValueError: If input list lengths differ.
        """
        pv_prognose_length = len(self.pv_prognose_wh)
        if (
            pv_prognose_length != len(self.strompreis_euro_pro_wh)
            or pv_prognose_length != len(self.gesamtlast)
            or (
                isinstance(self.einspeiseverguetung_euro_pro_wh, list)
                and pv_prognose_length != len(self.einspeiseverguetung_euro_pro_wh)
            )
        ):
            raise ValueError("Input lists have different lengths")
        return self


class GeneticOptimizationParameters(
    ConfigMixin,
    MeasurementMixin,
    PredictionMixin,
    # EnergyManagementSystemMixin, # Creates circular dependency with ems.py
    # StartMixin,                  # Creates circular dependency with ems.py
    GeneticParametersBaseModel,
):
    """Main parameter class for running the genetic energy optimization.

    Collects all model and configuration parameters necessary to run the
    optimization process, such as forecasts, pricing, battery and appliance models.
    """

    ems: GeneticEnergyManagementParameters
    pv_akku: Optional[SolarPanelBatteryParameters]
    inverter: Optional[InverterParameters]
    eauto: Optional[ElectricVehicleParameters]
    dishwasher: Optional[HomeApplianceParameters] = None
    temperature_forecast: Optional[list[Optional[float]]] = Field(
        default=None,
        description="An array of floats representing the temperature forecast in degrees Celsius for different time intervals.",
    )
    start_solution: Optional[list[float]] = Field(
        default=None, description="Can be `null` or contain a previous solution (if available)."
    )

    @model_validator(mode="after")
    def validate_list_length(self) -> Self:
        """Ensure that temperature forecast list matches the PV forecast length.

        Raises:
            ValueError: If list lengths mismatch.
        """
        arr_length = len(self.ems.pv_prognose_wh)
        if self.temperature_forecast is not None and arr_length != len(self.temperature_forecast):
            raise ValueError("Input lists have different lengths")
        return self

    @field_validator("start_solution")
    def validate_start_solution(
        cls, start_solution: Optional[list[float]]
    ) -> Optional[list[float]]:
        """Validate that the starting solution has at least two elements.

        Args:
            start_solution (list[float]): Optional list of solution values.

        Returns:
            list[float]: Validated list.

        Raises:
            ValueError: If the solution is too short.
        """
        if start_solution is not None and len(start_solution) < 2:
            raise ValueError("Requires at least two values.")
        return start_solution

    @classmethod
    def prepare(cls) -> "Optional[GeneticOptimizationParameters]":
        """Prepare optimization parameters from config and forecast data.

        Fills in values needed for optimization from available configuration and
        predictions. If some data is missing, default or demo values are used.

        Returns:
            GeneticOptimizationParameters: The fully prepared optimization parameters.

        Raises:
            ValueError: If required configuration values like start time are missing.
        """
        # Avoid circular dependency
        from akkudoktoreos.core.ems import get_ems

        ems = get_ems()

        # The optimization paramters
        oparams: "Optional[GeneticOptimizationParameters]" = None

        # Check for run definitions
        if ems.start_datetime is None:
            error_msg = "Start datetime unknown."
            logger.error(error_msg)
            raise ValueError(error_msg)
        # Check for general predictions conditions
        if cls.config.general.latitude is None:
            default_latitude = 52.52
            logger.error(f"Latitude unknown - defaulting to {default_latitude}.")
            cls.config.general.latitude = default_latitude
        if cls.config.general.longitude is None:
            default_longitude = 13.405
            logger.error(f"Longitude unknown - defaulting to {default_longitude}.")
            cls.config.general.longitude = default_longitude
        if cls.config.prediction.hours is None:
            logger.error("Prediction hours unknown - defaulting to 48 hours.")
            cls.config.prediction.hours = 48
        if cls.config.prediction.historic_hours is None:
            logger.error("Prediction historic hours unknown - defaulting to 24 hours.")
            cls.config.prediction.historic_hours = 24
        # Check optimization definitions
        if cls.config.optimization.hours is None:
            logger.error("Optimization hours unknown - defaulting to 48 hours.")
            cls.config.optimization.hours = 48
        if cls.config.optimization.interval is None:
            logger.error("Optimization interval unknown - defaulting to 3600 seconds.")
            cls.config.optimization.interval = 3600
        if cls.config.optimization.interval != 3600:
            logger.error(
                "Optimization interval '{}' seconds not supported - forced to 3600 seconds."
            )
            cls.config.optimization.interval = 3600
        # Check genetic algorithm definitions
        if cls.config.optimization.genetic is None:
            logger.error(
                "Genetic optimization configuration not configured - defaulting to demo config."
            )
            cls.config.optimization.genetic = {
                "individuals": 300,
                "generations": 400,
                "seed": None,
                "penalties": {
                    "ev_soc_miss": 10,
                },
            }
        if cls.config.optimization.genetic.individuals is None:
            logger.error("Genetic individuals unknown - defaulting to 300.")
            cls.config.optimization.genetic.individuals = 300
        if cls.config.optimization.genetic.generations is None:
            logger.error("Genetic generations unknown - defaulting to 400.")
            cls.config.optimization.genetic.generations = 400
        if cls.config.optimization.genetic.penalties is None:
            logger.error("Genetic penalties unknown - defaulting to demo config.")
            cls.config.optimization.genetic.penalties = {"ev_soc_miss": 10}
        if "ev_soc_miss" not in cls.config.optimization.genetic.penalties:
            logger.error("ev_soc_miss penalty function parameter unknown - defaulting to 100.")
            cls.config.optimization.genetic.penalties["ev_soc_miss"] = 10

        # Add forecast and device data
        interval = to_duration(cls.config.optimization.interval)
        power_to_energy_per_interval_factor = cls.config.optimization.interval / 3600
        prediction_end_datetime = ems.start_datetime.add(hours=cls.config.prediction.hours)
        max_retries = 10

        for attempt in range(1, max_retries + 1):
            # Collect all the data for optimisation, but do not exceed max retries
            if attempt > max_retries:
                error_msg = f"Maximum retries {max_retries} for parameter collection exceeded. Parameter preparation attempt {attempt}."
                logger.error(error_msg)
                raise ValueError(error_msg)

            # Assure predictions are uptudate
            cls.prediction.update_data()

            try:
                pvforecast_ac_power = (
                    cls.prediction.key_to_array(
                        key="pvforecast_ac_power",
                        start_datetime=ems.start_datetime,
                        end_datetime=prediction_end_datetime,
                        interval=interval,
                        fill_method="linear",
                    )
                    * power_to_energy_per_interval_factor
                ).tolist()
            except:
                logger.exception(
                    "No PV forecast data available - defaulting to demo data. Parameter preparation attempt {}.",
                    attempt,
                )
                cls.config.merge_settings_from_dict(
                    {
                        "pvforecast": {
                            "provider": "PVForecastAkkudoktor",
                            "planes": [
                                {
                                    "peakpower": 5.0,
                                    "surface_azimuth": 170,
                                    "surface_tilt": 7,
                                    "userhorizon": [20, 27, 22, 20],
                                    "inverter_paco": 10000,
                                },
                                {
                                    "peakpower": 4.8,
                                    "surface_azimuth": 90,
                                    "surface_tilt": 7,
                                    "userhorizon": [30, 30, 30, 50],
                                    "inverter_paco": 10000,
                                },
                                {
                                    "peakpower": 1.4,
                                    "surface_azimuth": 140,
                                    "surface_tilt": 60,
                                    "userhorizon": [60, 30, 0, 30],
                                    "inverter_paco": 2000,
                                },
                                {
                                    "peakpower": 1.6,
                                    "surface_azimuth": 185,
                                    "surface_tilt": 45,
                                    "userhorizon": [45, 25, 30, 60],
                                    "inverter_paco": 1400,
                                },
                            ],
                        },
                    }
                )
                # Retry
                continue
            try:
                elecprice_marketprice_wh = cls.prediction.key_to_array(
                    key="elecprice_marketprice_wh",
                    start_datetime=ems.start_datetime,
                    end_datetime=prediction_end_datetime,
                    interval=interval,
                    fill_method="ffill",
                ).tolist()
            except:
                logger.exception(
                    "No Electricity Marketprice forecast data available - defaulting to demo data. Parameter preparation attempt {}.",
                    attempt,
                )
                cls.config.elecprice.provider = "ElecPriceAkkudoktor"
                # Retry
                continue
            try:
                load_mean_adjusted = cls.prediction.key_to_array(
                    key="load_mean_adjusted",
                    start_datetime=ems.start_datetime,
                    end_datetime=prediction_end_datetime,
                    interval=interval,
                    fill_method="ffill",
                ).tolist()
            except:
                logger.exception(
                    "No Load forecast data available - defaulting to demo data. Parameter preparation attempt {}.",
                    attempt,
                )
                cls.config.merge_settings_from_dict(
                    {
                        "load": {
                            "provider": "LoadAkkudoktor",
                            "provider_settings": {
                                "load_name": "Akkudoktor Profile",
                                "loadakkudoktor_year_energy": "1000",
                            },
                        },
                    }
                )
                # Retry
                continue
            try:
                feed_in_tariff_wh = cls.prediction.key_to_array(
                    key="feed_in_tariff_wh",
                    start_datetime=ems.start_datetime,
                    end_datetime=prediction_end_datetime,
                    interval=interval,
                    fill_method="ffill",
                ).tolist()
            except:
                logger.exception(
                    "No feed in tariff forecast data available - defaulting to demo data. Parameter preparation attempt {}.",
                    attempt,
                )
                cls.config.merge_settings_from_dict(
                    {
                        "feedintariff": {
                            "provider": "FeedInTariffFixed",
                            "provider_settings": {
                                "feed_in_tariff_kwh": 0.078,
                            },
                        },
                    }
                )
                # Retry
                continue
            try:
                weather_temp_air = cls.prediction.key_to_array(
                    key="weather_temp_air",
                    start_datetime=ems.start_datetime,
                    end_datetime=prediction_end_datetime,
                    interval=interval,
                    fill_method="ffill",
                ).tolist()
            except:
                logger.exception(
                    "No weather forecast data available - defaulting to demo data. Parameter preparation attempt {}.",
                    attempt,
                )
                cls.config.weather.provider = "BrightSky"
                # Retry
                continue

            # Add device data
            # Battery
            if cls.config.devices.batteries is None:
                logger.error("No battery device data available - defaulting to demo data.")
                cls.config.devices.batteries = [{"device_id": "battery1", "capacity_wh": 8000}]
            try:
                battery_config = cls.config.devices.batteries[0]
                battery_params = SolarPanelBatteryParameters(
                    device_id=battery_config.device_id,
                    capacity_wh=battery_config.capacity_wh,
                    charging_efficiency=battery_config.charging_efficiency,
                    discharging_efficiency=battery_config.discharging_efficiency,
                    max_charge_power_w=battery_config.max_charge_power_w,
                    min_soc_percentage=battery_config.min_soc_percentage,
                    max_soc_percentage=battery_config.max_soc_percentage,
                )
            except:
                logger.exception(
                    "No battery device data available - defaulting to demo data. Parameter preparation attempt {}.",
                    attempt,
                )
                cls.config.devices.batteries = [{"device_id": "battery1", "capacity_wh": 8000}]
                # Retry
                continue
            if battery_config.levelized_cost_of_storage_kwh is None:
                logger.error(
                    "No battery device LCOS data available - defaulting to 0 €/kWh. Parameter preparation attempt {}.",
                    attempt,
                )
                battery_config.levelized_cost_of_storage_kwh = 0
            battery_lcos_kwh = battery_config.levelized_cost_of_storage_kwh

            # Electric Vehicle
            if cls.config.devices.electric_vehicles is None:
                logger.error("No electric vehicle device data available - defaulting to demo data.")
                cls.config.devices.max_electric_vehicles = 1
                cls.config.devices.electric_vehicles = [
                    {
                        "device_id": "ev11",
                        "capacity_wh": 50000,
                        "charge_rates": [0.0, 0.375, 0.5, 0.625, 0.75, 0.875, 1.0],
                    }
                ]
            try:
                electric_vehicle_config = cls.config.devices.electric_vehicles[0]
                electric_vehicle_params = ElectricVehicleParameters(
                    device_id=electric_vehicle_config.device_id,
                    capacity_wh=electric_vehicle_config.capacity_wh,
                    charging_efficiency=electric_vehicle_config.charging_efficiency,
                    discharging_efficiency=electric_vehicle_config.discharging_efficiency,
                    max_charge_power_w=electric_vehicle_config.max_charge_power_w,
                    min_soc_percentage=electric_vehicle_config.min_soc_percentage,
                    max_soc_percentage=electric_vehicle_config.max_soc_percentage,
                )
            except:
                logger.exception(
                    "No electric_vehicle device data available - defaulting to demo data. Parameter preparation attempt {}.",
                    attempt,
                )
                cls.config.devices.max_electric_vehicles = 1
                cls.config.devices.electric_vehicles = [
                    {
                        "device_id": "ev12",
                        "capacity_wh": 50000,
                        "charge_rates": [0.0, 0.375, 0.5, 0.625, 0.75, 0.875, 1.0],
                    }
                ]
                # Retry
                continue

            # Inverter
            if cls.config.devices.inverters is None:
                logger.error("No inverter device data available - defaulting to demo data.")
                cls.config.devices.inverters = [
                    {
                        "device_id": "inverter1",
                        "max_power_w": 10000,
                        "battery_id": battery_config.device_id,
                    }
                ]
            try:
                inverter_config = cls.config.devices.inverters[0]
                inverter_params = InverterParameters(
                    device_id=inverter_config.device_id,
                    max_power_wh=inverter_config.max_power_w,
                    battery_id=inverter_config.battery_id,
                )
            except:
                logger.exception(
                    "No inverter device data available - defaulting to demo data. Parameter preparation attempt {}.",
                    attempt,
                )
                cls.config.devices.inverters = [
                    {
                        "device_id": "inverter1",
                        "max_power_w": 10000,
                        "battery_id": battery_config.device_id,
                    }
                ]
                # Retry
                continue

            # We got all parameter data
            try:
                oparams = GeneticOptimizationParameters(
                    ems=GeneticEnergyManagementParameters(
                        pv_prognose_wh=pvforecast_ac_power,
                        strompreis_euro_pro_wh=elecprice_marketprice_wh,
                        einspeiseverguetung_euro_pro_wh=feed_in_tariff_wh,
                        gesamtlast=load_mean_adjusted,
                        preis_euro_pro_wh_akku=battery_lcos_kwh / 1000,
                    ),
                    temperature_forecast=weather_temp_air,
                    pv_akku=battery_params,
                    eauto=electric_vehicle_params,
                    inverter=inverter_params,
                )
            except:
                logger.exception(
                    "Can not prepare optimization parameters - will retry. Parameter preparation attempt {}.",
                    attempt,
                )
                oparams = None
                # Retry
                continue

            # Parameters prepared
            break

        return oparams
