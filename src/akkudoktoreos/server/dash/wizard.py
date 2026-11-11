"""Setup wizard for EOSdash.

The wizard walks a new user through the configuration EOS needs before it can predict and
optimize. Every step explains a small group of settings, gives an example, reports what is
still missing and shows the configuration cards of exactly these settings - the same cards as
on the Config page.

The steps are data: add a ``WizardStep`` to ``WIZARD_STEPS`` to add a step.

The wizard keeps no state of its own. The current step travels with the requests
(``wizard_step``) and whether a step is done is derived from the EOS configuration. The
configuration is read and written through the EOS server only.

The configuration cards send their updates to ``/eosdash/configuration``. The wizard content
is wrapped in an element that adds ``wizard_step`` to all requests from within (``hx-vals`` is
inherited), which makes that route answer with the wizard instead of the Config page.
"""

import json
from dataclasses import dataclass
from typing import Any, Callable, Optional, Union

import requests
from fasthtml.common import Div
from monsterui.franken import (
    H3,
    Card,
    DivHStacked,
    DivLAligned,
    P,
    UkIcon,
)

from akkudoktoreos.config.config import ConfigEOS
from akkudoktoreos.config.configabc import is_home_assistant_addon
from akkudoktoreos.server.dash.components import (
    Badge,
    ConfigButton,
    Error,
    Success,
)
from akkudoktoreos.server.dash.configuration import (
    config_card,
    config_update,
    create_config_details,
    get_config,
    remember_eos_server,
)
from akkudoktoreos.server.dash.context import request_url_for
from akkudoktoreos.server.dash.markdown import Markdown

WIZARD_PATH = "/eosdash/wizard"


# ---------------------------------------------------------------------------
# Step definition
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WizardChoice:
    """Configuration values a step shows depending on the value of another one.

    Typically the settings of the selected provider.

    Attributes:
        name: Dotted name of the configuration value that decides, e.g. ``load.provider``.
            Its value may also be a list of values.
        config_names: Dotted names of the configuration values to show, by value of ``name``.
            Values that need no further settings are left out.
    """

    name: str
    config_names: dict[str, tuple[str, ...]]


@dataclass(frozen=True)
class WizardStep:
    """A step of the setup wizard.

    Attributes:
        id: Identifier of the step, used in requests.
        title: Title of the step.
        intro: Explanation of the step (Markdown).
        example: Example for the settings of the step (Markdown).
        config_names: Dotted names of the configuration values to show.
        choices: Further configuration values to show depending on the value of another one.
            They are shown right after the configuration value that decides.
        check: Function that takes the configuration details and returns what is still to do
            in this step, one sentence each. An empty list means the step is done.
    """

    id: str
    title: str
    intro: str
    example: str = ""
    config_names: tuple[str, ...] = ()
    choices: tuple[WizardChoice, ...] = ()
    check: Optional[Callable[[dict[str, dict]], list[str]]] = None


def config_value(config_details: dict[str, dict], name: str, default: Any = None) -> Any:
    """Return the current value of a configuration value.

    Args:
        config_details: Configuration details, as created by ``create_config_details``.
        name: Dotted name of the configuration value.
        default: Value to return if the configuration value is unknown.
    """
    try:
        return json.loads(config_details[name]["value"])
    except (KeyError, TypeError, ValueError):
        return default


def step_config_names(step: WizardStep, config_details: dict[str, dict]) -> list[str]:
    """Return the names of the configuration values a step shows for this configuration."""
    names: list[str] = []
    for name in step.config_names:
        names.append(name)
        for choice in step.choices:
            if choice.name != name:
                continue
            value = config_value(config_details, name)
            for chosen in value if isinstance(value, list) else [value]:
                names.extend(choice.config_names.get(str(chosen), ()))
    # A configuration value is shown once, even if several choices name it
    return [name for name in dict.fromkeys(names) if name in config_details]


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def check_location(config_details: dict[str, dict]) -> list[str]:
    """Report what is still to do for the location."""
    latitude = config_value(config_details, "general.latitude")
    longitude = config_value(config_details, "general.longitude")
    if latitude is None or longitude is None:
        return ["Latitude and longitude are not set."]

    todo = []
    try:
        defaults = (
            json.loads(config_details["general.latitude"]["default"]),
            json.loads(config_details["general.longitude"]["default"]),
        )
    except (KeyError, TypeError, ValueError):
        defaults = None
    if defaults == (latitude, longitude):
        todo.append(
            f"Latitude and longitude are still the default values ({latitude}, {longitude}). "
            "Set the location of your home."
        )
    if not config_value(config_details, "general.timezone"):
        todo.append(
            "EOS has no time zone for this location. Check latitude and longitude or set "
            "general.timezone_override."
        )
    return todo


def check_horizon(config_details: dict[str, dict]) -> list[str]:
    """Report what is still to do for the horizons and the energy management."""
    todo = []
    algorithm = str(config_value(config_details, "optimization.algorithm", "")).lower()
    horizon_name = f"optimization.{algorithm}.horizon_hours"
    horizon = config_value(config_details, horizon_name)
    prediction = config_value(config_details, "prediction.hours")
    if isinstance(horizon, (int, float)) and (prediction is None or prediction < horizon):
        todo.append(
            f"prediction.hours ({prediction}) is shorter than {horizon_name} ({horizon}). "
            "Optimization runs fail until the predictions cover the optimization horizon."
        )
    if config_value(config_details, "ems.mode") == "DISABLED":
        todo.append(
            "ems.mode is DISABLED: EOS does not run the energy management. Set it to "
            "OPTIMIZATION to get a plan, or to PREDICTION to only update the predictions."
        )
    return todo


# PV forecast providers that calculate the forecast from the planes of the PV system
PV_PLANES_PROVIDERS = (
    "PVForecastAkkudoktor",
    "PVForecastAdaptiveAkkudoktor",
    "PVForecastForecastSolar",
    "PVForecastPVLib",
)


def _is_empty(value: Any) -> bool:
    """Whether a configuration value is not set."""
    return value is None or value == "" or value == [] or value == {}


def _missing(config_details: dict[str, dict], *names: str) -> list[str]:
    """Report the configuration values of the given names that are not set."""
    return [
        f"{name} is not set." for name in names if _is_empty(config_value(config_details, name))
    ]


def check_pv(config_details: dict[str, dict]) -> list[str]:
    """Report what is still to do for the PV system."""
    provider = config_value(config_details, "pvforecast.provider")
    if not provider:
        return [
            "No PV forecast provider is selected, so EOS plans without PV yield. "
            "Leave it like this only if you have no PV system."
        ]
    if provider in PV_PLANES_PROVIDERS:
        planes = config_value(config_details, "pvforecast.planes") or []
        if not any(isinstance(plane, dict) and plane.get("peakpower") for plane in planes):
            return [f"{provider} needs at least one plane with a peakpower in pvforecast.planes."]
    elif provider == "PVForecastSolcast":
        return _missing(config_details, "pvforecast.solcast.api_key", "pvforecast.solcast.site_id")
    elif provider == "PVForecastPVNode":
        return _missing(config_details, "pvforecast.pvnode.api_key")
    return []


def check_weather(config_details: dict[str, dict]) -> list[str]:
    """Report what is still to do for the weather forecast."""
    if not config_value(config_details, "weather.provider"):
        return ["No weather provider is selected."]
    return []


def check_prices(config_details: dict[str, dict]) -> list[str]:
    """Report what is still to do for prices, fees and the feed-in tariff."""
    todo = []
    provider = config_value(config_details, "elecprice.provider")
    if not provider:
        todo.append(
            "No electricity price provider is selected. EOS needs the price of electricity "
            "from the grid to optimize."
        )
    elif provider == "ElecPriceFixed":
        todo += _missing(
            config_details, "elecprice.elecpricefixed.elecprice_marketprice_amt_kwh.windows"
        )
    elif provider == "ElecPriceTibber":
        todo += _missing(config_details, "elecprice.tibber.access_token")

    if config_value(config_details, "elecfee.provider") == "ElecFeeFixed" and all(
        _is_empty(config_value(config_details, f"elecfee.elecfeefixed.{name}.windows"))
        for name in (
            "consumption_amt_kwh",
            "consumption_percent_amt",
            "feedin_amt_kwh",
            "feedin_percent_amt",
        )
    ):
        todo.append("ElecFeeFixed is selected, but no fee is set.")

    provider = config_value(config_details, "feedintariff.provider")
    if not provider:
        todo.append(
            "No feed-in tariff provider is selected. Select one if you are paid for the "
            "energy you feed into the grid."
        )
    elif provider == "FeedInTariffFixed":
        todo += _missing(
            config_details, "feedintariff.feedintarifffixed.feed_in_tariff_amt_kwh.windows"
        )
    return todo


def check_load(config_details: dict[str, dict]) -> list[str]:
    """Report what is still to do for the load forecast."""
    provider = config_value(config_details, "load.provider")
    if not provider:
        return [
            "No load provider is selected. EOS needs a forecast of your consumption to optimize."
        ]
    if provider in ("LoadAkkudoktor", "LoadAkkudoktorAdjusted"):
        todo = _missing(config_details, "load.loadakkudoktor.loadakkudoktor_year_energy_kwh")
        if provider == "LoadAkkudoktorAdjusted":
            todo += _missing(config_details, "measurement.load_emr_keys")
        return todo
    return []


def _device_ids(config_details: dict[str, dict], kind: str) -> list[str]:
    """Return the ids of the configured devices of a kind, e.g. ``batteries``."""
    devices = config_value(config_details, f"devices.{kind}")
    return list(devices) if isinstance(devices, dict) else []


def check_devices(config_details: dict[str, dict]) -> list[str]:
    """Report what is still to do for the devices."""
    if not any(
        _device_ids(config_details, kind)
        for kind in ("batteries", "electric_vehicles", "home_appliances")
    ):
        return [
            "No battery, electric vehicle or home appliance is configured, so there is "
            "nothing EOS can schedule."
        ]
    return []


def check_measurements(config_details: dict[str, dict]) -> list[str]:
    """Report what is still to do for measurements and the home automation."""
    providers = config_value(config_details, "adapter.provider") or []
    device_ids = [
        device_id
        for kind in ("batteries", "electric_vehicles")
        for device_id in _device_ids(config_details, kind)
    ]
    if not providers:
        devices = f" of {', '.join(device_ids)}" if device_ids else ""
        return [
            "No adapter is selected, so no source for the state of charge"
            f"{devices} is known. Select an adapter, or make sure your own system sends the "
            "state of charge with PUT /v1/measurement/value."
        ]
    # The mapping of the Home Assistant adapter is checked in the Home Assistant step
    return []


def check_homeassistant(config_details: dict[str, dict]) -> list[str]:
    """Report what is still to do for the Home Assistant adapter.

    Nothing is to do if the Home Assistant adapter is not selected.
    """
    providers = config_value(config_details, "adapter.provider") or []
    if "HomeAssistant" not in providers:
        return []

    todo = []
    if not is_home_assistant_addon():
        # EOSdash is started by the EOS server and inherits its environment
        todo.append(
            "EOS does not run as a Home Assistant add-on, so the Home Assistant adapter "
            "cannot reach Home Assistant. Run EOS as an add-on, or remove HomeAssistant "
            "from adapter.provider and send the measurements with PUT /v1/measurement/value."
        )

    entity_ids = config_value(config_details, "adapter.homeassistant.device_measurement_entity_ids")
    entity_ids = entity_ids if isinstance(entity_ids, dict) else {}
    for kind in ("batteries", "electric_vehicles"):
        for device_id in _device_ids(config_details, kind):
            key = f"{device_id}-soc-factor"
            if not entity_ids.get(key):
                todo.append(
                    f"No Home Assistant entity is set for the state of charge of {device_id} "
                    f"({key}) in adapter.homeassistant.device_measurement_entity_ids."
                )

    if _is_empty(
        config_value(config_details, "adapter.homeassistant.device_instruction_entity_ids")
    ):
        todo.append(
            "adapter.homeassistant.device_instruction_entity_ids is not set, so EOS writes "
            "no instructions to Home Assistant and your automations get nothing to act on."
        )
    return todo


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------

WIZARD_STEPS: tuple[WizardStep, ...] = (
    WizardStep(
        id="start",
        title="Start",
        intro="""
This assistant takes you through the settings EOS needs before it can predict and optimize.

Each step explains a few settings, gives an example and shows the configuration cards of
these settings. The cards are the same as on the **Config** page:

- Click a card to open it. It shows the description, the type and the default of the setting.
- Type the new value into the **update** field and press Enter, or choose a value and press
  **Set**. The value is checked by EOS; if EOS rejects it, the card shows why.
- A new value is used by EOS right away. By default EOS also saves it to the configuration
  file within a minute, so it survives a restart of EOS. If you switched the automatic saving
  off, save the configuration in the last step.

Above the explanation you see all steps. A check mark means EOS has what it needs from that
step, a warning sign means something is left to do. You can leave the assistant at any time
and come back later: it reads the state from the EOS configuration.

If EOS is already configured, save a copy of the configuration on the **Admin** page before
you change it.
""",
    ),
    WizardStep(
        id="location",
        title="Location",
        intro="""
EOS needs to know where your home is. The weather forecast and the PV forecast are made for
this location, and EOS looks up your **time zone** from it. All times in the predictions and
in the plan are local times of this time zone.

- `general.latitude` - decimal degrees, north is positive.
- `general.longitude` - decimal degrees, east is positive.
- `general.timezone_override` - only needed if the time zone EOS looks up is not the one you
  want. Leave it at `null` otherwise.
""",
        example="""
A house in Munich: latitude `48.137`, longitude `11.575`. EOS looks up the time zone
`Europe/Berlin`. Three decimal places are precise enough (about 100 m).

To set the time zone yourself use an IANA name such as `Europe/Vienna`.
""",
        config_names=("general.latitude", "general.longitude", "general.timezone_override"),
        check=check_location,
    ),
    WizardStep(
        id="horizon",
        title="Horizons and energy management",
        intro="""
Two horizons decide how far EOS looks ahead, and the energy management decides whether and
how often EOS works on its own.

- `prediction.hours` - how many hours into the future EOS predicts PV yield, load and prices.
- `optimization.<algorithm>.horizon_hours` - how many hours the plan covers. The predictions
  must cover at least this horizon, otherwise the optimization fails.
- `ems.mode` - what the energy management does in each run: `DISABLED` nothing,
  `PREDICTION` updates the predictions, `OPTIMIZATION` updates the predictions and
  calculates a new plan. Outside of Home Assistant the default is `DISABLED`, so EOS stays
  idle until you change it.
- `ems.interval` - seconds between two energy management runs.
""",
        example="""
A common setup: `prediction.hours` = `48`, `horizon_hours` = `24`, `ems.mode` =
`OPTIMIZATION` and `ems.interval` = `300`. EOS then predicts two days ahead and calculates
a plan for the next 24 hours every five minutes.

Use `PREDICTION` while you are still setting up the forecasts and want to look at them on the
**Prediction** page before EOS optimizes.
""",
        config_names=(
            "prediction.hours",
            "optimization.algorithm",
            "ems.mode",
            "ems.interval",
        ),
        choices=(
            WizardChoice(
                "optimization.algorithm",
                {
                    "GENETIC": ("optimization.genetic.horizon_hours",),
                    "GENETIC0": ("optimization.genetic0.horizon_hours",),
                },
            ),
        ),
        check=check_horizon,
    ),
    WizardStep(
        id="pv",
        title="PV system",
        intro="""
EOS predicts the yield of your PV system. Select who makes the forecast with
`pvforecast.provider`; the settings the selected provider needs appear below it.

- `PVForecastAkkudoktor`, `PVForecastAdaptiveAkkudoktor`, `PVForecastForecastSolar` and
  `PVForecastPVLib` calculate the forecast from your location and the **planes** of your PV
  system. A plane is a group of modules with the same orientation, e.g. one side of the roof.
- `PVForecastSolcast`, `PVForecastPVNode`, `PVForecastVrm` and `PVForecastHomeAssistant` take
  the forecast from a service or system that already knows your PV system. They need the
  access data of that service.
- `PVForecastImport` takes a forecast you provide as a file or as JSON.

The most important settings of a plane:

- `peakpower` - nominal power of the modules of the plane in kW.
- `surface_azimuth` - orientation, clockwise from north: north = 0, east = 90, south = 180,
  west = 270.
- `surface_tilt` - tilt from the horizontal in degrees: 0 is flat, 90 is vertical.
- `inverter_paco` - AC power rating of the inverter in W.

Skip this step if you have no PV system.
""",
        example="""
A south-facing roof with 8 kWp, 30° tilt and an 8 kW inverter: select
`PVForecastAkkudoktor`, open `pvforecast.planes`, add a plane and set `peakpower` = `8`,
`surface_azimuth` = `180`, `surface_tilt` = `30` and `inverter_paco` = `8000`.

An east/west roof is two planes, one with `surface_azimuth` = `90` and one with `270`, each
with the peak power of its side.
""",
        config_names=("pvforecast.provider",),
        choices=(
            WizardChoice(
                "pvforecast.provider",
                {
                    "PVForecastAkkudoktor": ("pvforecast.planes",),
                    "PVForecastAdaptiveAkkudoktor": ("pvforecast.planes",),
                    "PVForecastForecastSolar": (
                        "pvforecast.planes",
                        "pvforecast.forecastsolar.api_key",
                    ),
                    "PVForecastPVLib": ("pvforecast.planes",),
                    "PVForecastPVNode": (
                        "pvforecast.pvnode.api_key",
                        "pvforecast.pvnode.site_id",
                        "pvforecast.planes",
                    ),
                    "PVForecastSolcast": (
                        "pvforecast.solcast.api_key",
                        "pvforecast.solcast.site_id",
                    ),
                    "PVForecastVrm": ("pvforecast.vrm.site_id", "pvforecast.vrm.token"),
                    "PVForecastHomeAssistant": (
                        "pvforecast.homeassistant.entity_id",
                        "pvforecast.homeassistant.base_url",
                        "pvforecast.homeassistant.token",
                    ),
                    "PVForecastImport": (
                        "pvforecast.pvforecastimport.import_file_path",
                        "pvforecast.pvforecastimport.import_json",
                    ),
                },
            ),
        ),
        check=check_pv,
    ),
    WizardStep(
        id="weather",
        title="Weather",
        intro="""
The weather forecast gives EOS the outside temperature, the irradiance and the cloud cover
at your location. You see it on the **Prediction** page, and `PVForecastPVLib` calculates
the PV forecast from it.

Select the service with `weather.provider`: `BrightSky`, `ClearOutside` or `OpenMeteo`
retrieve the forecast for your location, `WeatherImport` takes a forecast you provide.
""",
        example="""
To use Open-Meteo set `weather.provider` to `OpenMeteo`. The provider needs no further
settings; it takes the location from the **Location** step.
""",
        config_names=("weather.provider",),
        choices=(
            WizardChoice(
                "weather.provider",
                {
                    "WeatherImport": (
                        "weather.weatherimport.import_file_path",
                        "weather.weatherimport.import_json",
                    ),
                },
            ),
        ),
        check=check_weather,
    ),
    WizardStep(
        id="prices",
        title="Prices and fees",
        intro="""
EOS optimizes costs, so it needs to know what electricity from the grid costs and what you
get for the energy you feed in. Three predictions work together:

- `elecprice.provider` - the electricity price. The market price providers
  (`ElecPriceAkkudoktor`, `ElecPriceEnergyCharts`, `ElecPriceSMARD`, `ElecPriceTibber`)
  deliver the raw market price **without** fees. `ElecPriceFixed` uses prices you set for
  time windows of the day.
- `elecfee.provider` - what is added to the raw price. With `ElecFeeFixed` you set a fee per
  kWh (grid fee, levies, the margin of your supplier) and a surcharge in percent (VAT). The
  price EOS uses is `(raw price + fee per kWh) * (100 + percent) / 100`.
- `feedintariff.provider` - what you get per kWh fed into the grid. `FeedInTariffFixed` uses
  the tariff you set; the other providers use market prices, for direct marketing.

Prices and fees are amounts of your currency per kWh, e.g. `0.30` for 30 ct/kWh.
""",
        example="""
A dynamic tariff in Germany: `elecprice.provider` = `ElecPriceEnergyCharts` with bidding zone
`DE-LU`, `elecfee.provider` = `ElecFeeFixed` with `consumption_amt_kwh` = `0.15` (all fees
per kWh without VAT) and `consumption_percent_amt` = `19` (VAT), each in one time window
that covers the whole day.

A fixed tariff of 32 ct/kWh including everything: `elecprice.provider` = `ElecPriceFixed`
with one time window of `0.32`, and no `elecfee.provider`.

A fixed feed-in tariff of 7.86 ct/kWh: `feedintariff.provider` = `FeedInTariffFixed` with one
time window of `0.0786`.
""",
        config_names=("elecprice.provider", "elecfee.provider", "feedintariff.provider"),
        choices=(
            WizardChoice(
                "elecprice.provider",
                {
                    "ElecPriceEnergyCharts": ("elecprice.energycharts.bidding_zone",),
                    "ElecPriceFixed": (
                        "elecprice.elecpricefixed.elecprice_marketprice_amt_kwh.windows",
                    ),
                    "ElecPriceImport": (
                        "elecprice.elecpriceimport.import_file_path",
                        "elecprice.elecpriceimport.import_json",
                    ),
                    "ElecPriceSMARD": ("elecprice.smard.region", "elecprice.smard.filter_id"),
                    "ElecPriceTibber": (
                        "elecprice.tibber.access_token",
                        "elecprice.tibber.home_id",
                    ),
                },
            ),
            WizardChoice(
                "elecfee.provider",
                {
                    "ElecFeeFixed": (
                        "elecfee.elecfeefixed.consumption_amt_kwh.windows",
                        "elecfee.elecfeefixed.consumption_percent_amt.windows",
                        "elecfee.elecfeefixed.feedin_amt_kwh.windows",
                        "elecfee.elecfeefixed.feedin_percent_amt.windows",
                    ),
                    "ElecFeeImport": (
                        "elecfee.elecfeeimport.import_file_path",
                        "elecfee.elecfeeimport.import_json",
                    ),
                },
            ),
            WizardChoice(
                "feedintariff.provider",
                {
                    "FeedInTariffDvhubOnline": ("feedintariff.dvhubonline.zone",),
                    "FeedInTariffEnergyCharts": ("feedintariff.energycharts.bidding_zone",),
                    "FeedInTariffFixed": (
                        "feedintariff.feedintarifffixed.feed_in_tariff_amt_kwh.windows",
                    ),
                    "FeedInTariffImport": (
                        "feedintariff.feedintariffimport.import_file_path",
                        "feedintariff.feedintariffimport.import_json",
                    ),
                },
            ),
        ),
        check=check_prices,
    ),
    WizardStep(
        id="load",
        title="Load",
        intro="""
EOS needs a forecast of the electricity your household consumes. Select it with
`load.provider`:

- `LoadAkkudoktor` - a generic load profile, scaled to your yearly consumption.
- `LoadAkkudoktorAdjusted` - the same profile, refined with your measured consumption. It
  needs load measurements, see the **Measurements** step.
- `LoadVrm` - the forecast of the Victron Remote Management (VRM) portal.
- `LoadImport` - a forecast you provide as a file or as JSON.
""",
        example="""
A household that consumes 4500 kWh per year: `load.provider` = `LoadAkkudoktor` and
`load.loadakkudoktor.loadakkudoktor_year_energy_kwh` = `4500`. You find the yearly
consumption on your electricity bill.
""",
        config_names=("load.provider",),
        choices=(
            WizardChoice(
                "load.provider",
                {
                    "LoadAkkudoktor": ("load.loadakkudoktor.loadakkudoktor_year_energy_kwh",),
                    "LoadAkkudoktorAdjusted": (
                        "load.loadakkudoktor.loadakkudoktor_year_energy_kwh",
                    ),
                    "LoadVrm": ("load.vrm.site_id", "load.vrm.token"),
                    "LoadImport": (
                        "load.loadimport.import_file_path",
                        "load.loadimport.import_json",
                    ),
                },
            ),
        ),
        check=check_load,
    ),
    WizardStep(
        id="devices",
        title="Devices",
        intro="""
Tell EOS which devices it plans for. Each kind of device has its own card; open it to add a
device or to change one.

- `devices.batteries` - stationary batteries.
- `devices.inverters` - inverters that connect PV and battery to the house.
- `devices.electric_vehicles` - the batteries of electric vehicles.
- `devices.home_appliances` - appliances EOS may shift in time, e.g. a dishwasher.

Every device has a `device_id`. Choose a short name and keep it: EOS names the measurements
of the device after it, e.g. `battery1-soc-factor` for the state of charge of `battery1`.

The most important settings of a battery:

- `capacity_wh` - capacity in Wh.
- `max_charge_power_w` - maximum charge power in W.
- `min_soc_percentage`, `max_soc_percentage` - the range of the state of charge EOS may use.
- `charging_efficiency`, `discharging_efficiency` - as a factor, `0.95` for 95 %.
""",
        example="""
**Battery** - a 10 kWh battery that charges with up to 5 kW and shall never go below 10 %:
add a battery with `device_id` = `battery1`, `capacity_wh` = `10000`,
`max_charge_power_w` = `5000`, `min_soc_percentage` = `10` and `max_soc_percentage` = `100`.

**Inverter** - a 10 kW inverter the battery is connected to: add an inverter with
`device_id` = `inverter1`, `max_power_w` = `10000` and `battery_id` = `battery1`.

**Electric vehicle** - a car with a 60 kWh battery at an 11 kW wallbox that shall be charged
to 80 % by 7 o'clock in the morning: add an electric vehicle with `device_id` = `ev1`,
`capacity_wh` = `60000`, `max_charge_power_w` = `11000`, `min_soc_percentage` = `80` (for
an electric vehicle this is the state of charge to reach) and
`min_soc_deadline_datetime` = `2026-07-16T07:00:00` (the departure).

**Home appliance** - a dishwasher that needs 1.5 kWh in a run of 3 hours and may run once
between 10:00 and 16:00: add a home appliance with `device_id` = `dishwasher`,
`consumption_wh` = `1500`, `duration_h` = `3` and
`time_windows` = `{"windows": [{"start_time": "10:00", "duration": "6 hours"}]}`.
""",
        config_names=(
            "devices.batteries",
            "devices.inverters",
            "devices.electric_vehicles",
            "devices.home_appliances",
        ),
        check=check_devices,
    ),
    WizardStep(
        id="measurements",
        title="Measurements",
        intro="""
To plan a battery or an electric vehicle EOS must know its **current state of charge**. The
measurement is named `<device_id>-soc-factor` and is a factor from `0.0` (empty) to `1.0`
(full) - not a percentage. It must be fresh: the GENETIC optimization does not use a state
of charge older than `optimization.genetic.measurement_max_age_seconds` and then assumes an
**empty** battery (0 %).

There are two ways to get measurements into EOS:

- An adapter, selected with `adapter.provider`. With `HomeAssistant` EOS reads the
  measurements from Home Assistant entities before each energy management run and writes
  its instructions back to Home Assistant - this needs EOS to run as a Home Assistant
  add-on, the next step explains it. With `NodeRED` EOS sends its instructions to a
  Node-RED flow; the measurements still have to come from your flow.
- Your own system sends each value to the EOS server with `PUT /v1/measurement/value`.

Energy meter readings are optional. They are meter readings in kWh that only count up, not
the current power. With `LoadAkkudoktorAdjusted` EOS uses the load meter readings to adapt
the load forecast to your household; list their measurement keys in
`measurement.load_emr_keys`. The Home Assistant adapter does this on its own for the
entities it reads.
""",
        example="""
Your own system, e.g. a Node-RED flow or a script, at least every five minutes:
`PUT /v1/measurement/value?datetime=2026-01-31T12:00:00&key=battery1-soc-factor&value=0.55`

Home Assistant: select `HomeAssistant` in `adapter.provider` and continue with the next
step.
""",
        config_names=(
            "adapter.provider",
            "measurement.load_emr_keys",
            "optimization.genetic.measurement_max_age_seconds",
        ),
        choices=(
            WizardChoice(
                "adapter.provider",
                {
                    "NodeRED": ("adapter.nodered.host", "adapter.nodered.port"),
                },
            ),
        ),
        check=check_measurements,
    ),
    WizardStep(
        id="homeassistant",
        title="Home Assistant",
        intro="""
Skip this step if you do not use Home Assistant.

**EOS must run as a Home Assistant add-on.** The Home Assistant adapter talks to Home
Assistant through the API of the add-on system, with the access Home Assistant grants the
add-on. There is no setting for an address or a token. If EOS runs somewhere else, remove
`HomeAssistant` from `adapter.provider` and let Home Assistant send the measurements with
`PUT /v1/measurement/value`, e.g. with a `rest_command`.

Select `HomeAssistant` in `adapter.provider` (step **Measurements**). Before each energy
management run EOS then **reads** from Home Assistant:

- `device_measurement_entity_ids` - the current state of your devices, above all the state
  of charge: map `<device_id>-soc-factor` to an entity whose state is a factor from `0.0` to
  `1.0`. Without it EOS plans with an empty battery.
- `load_emr_entity_ids`, `pv_production_emr_entity_ids`, `grid_import_emr_entity_ids`,
  `grid_export_emr_entity_ids` - optional energy meter readings in **kWh** that only count
  up (`state_class: total_increasing`), not power sensors in W. EOS uses the entity IDs as
  measurement keys and adds them to `measurement.*_emr_keys` on its own.
- `config_entity_ids` - optional: configuration values taken from entities, e.g. a battery
  capacity an integration reports. The key is the path of the setting with `/`, and devices
  are addressed by their `device_id`: `devices/batteries/battery1/capacity_wh`.

After each run EOS **writes** to Home Assistant:

- `device_instruction_entity_ids` - one entity per device, named `sensor.eos_<device_id>`,
  e.g. `sensor.eos_battery1`. The state is the operation mode EOS wants right now in lower
  case, e.g. `self_consumption`, `forced_charge` or `idle`; the attribute
  `operation_mode_factor` says how much, from `0.0` to `1.0`.
- `solution_entity_ids` - optional values of the current plan for dashboards and debugging,
  named `sensor.eos_<solution key>`, e.g. `sensor.eos_battery1_soc_factor`.

EOS only writes the entities you list. The **Config** page shows read-only lists to pick
from: `adapter.homeassistant.homeassistant_entity_ids` has the entities of Home Assistant,
`adapter.homeassistant.eos_device_instruction_entity_ids` and
`adapter.homeassistant.eos_solution_entity_ids` have the names EOS can write. The
instruction names appear after the first optimization.

**EOS does not switch anything itself.** Write an automation in Home Assistant that reacts to
`sensor.eos_<device_id>` and controls your inverter, wallbox or appliance. The entities EOS
writes have no unique ID: to use them in dashboards or the history, wrap them in template
sensors.
""",
        example="""
A battery `battery1` whose integration reports the state of charge in percent as
`sensor.battery_soc`. Create a template sensor in Home Assistant that converts it:

```yaml
template:
  - sensor:
      - name: "Battery1 SoC Factor"
        unique_id: "battery1_soc_factor"
        state: "{{ states('sensor.battery_soc') | float(0) / 100 }}"
```

In EOS set:

- `device_measurement_entity_ids` = `{"battery1-soc-factor": "sensor.battery1_soc_factor"}`
- `load_emr_entity_ids` = `["sensor.house_consumption_total_kwh"]`
- `device_instruction_entity_ids` = `["sensor.eos_battery1"]`

Then let Home Assistant follow the instruction, e.g. charge the battery from the grid while
EOS asks for it (replace the actions with the ones of your inverter integration):

```yaml
automation:
  - alias: "EOS battery1 instruction"
    triggers:
      - trigger: state
        entity_id: sensor.eos_battery1
    actions:
      - choose:
          - conditions: "{{ is_state('sensor.eos_battery1', 'forced_charge') }}"
            sequence:
              - action: select.select_option
                target:
                  entity_id: select.inverter_battery_mode
                data:
                  option: "Charge from grid"
        default:
          - action: select.select_option
            target:
              entity_id: select.inverter_battery_mode
            data:
              option: "Self consumption"
```
""",
        config_names=(
            "adapter.homeassistant.device_measurement_entity_ids",
            "adapter.homeassistant.load_emr_entity_ids",
            "adapter.homeassistant.pv_production_emr_entity_ids",
            "adapter.homeassistant.grid_import_emr_entity_ids",
            "adapter.homeassistant.grid_export_emr_entity_ids",
            "adapter.homeassistant.config_entity_ids",
            "adapter.homeassistant.device_instruction_entity_ids",
            "adapter.homeassistant.solution_entity_ids",
        ),
        check=check_homeassistant,
    ),
    WizardStep(
        id="finish",
        title="Finish",
        intro="""
Below is what the steps of this assistant found. Go back to a step to complete it.

EOS saves changes of the configuration to the configuration file on its own if
`general.config_save_mode` is `AUTOMATIC`, which is the default. With `MANUAL` your settings
are lost when EOS restarts unless you save them. **Save configuration** writes the
configuration file right now in both modes.

All other settings of EOS are on the **Config** page.

Then look at the **Prediction** page to check the forecasts and at the **Plan** page for the
result of the optimization.
""",
        config_names=("general.config_save_mode",),
    ),
)


def wizard_step(step_id: Optional[str]) -> WizardStep:
    """Return the step with the given id, or the first step if there is no such step."""
    for step in WIZARD_STEPS:
        if step.id == step_id:
            return step
    return WIZARD_STEPS[0]


def step_todo(step: WizardStep, config_details: dict[str, dict]) -> list[str]:
    """Return what is still to do in a step. An empty list means the step is done."""
    return step.check(config_details) if step.check else []


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------


def save_config(eos_host: str, eos_port: Union[str, int]) -> Any:
    """Let the EOS server save the configuration to its configuration file.

    Returns:
        A success or an error message.
    """
    server = f"http://{eos_host}:{eos_port}"
    try:
        result = requests.put(f"{server}/v1/config/file", timeout=10)
        result.raise_for_status()
        config_file_path = result.json()["general"]["config_file_path"]
    except requests.exceptions.HTTPError as e:
        try:
            detail = result.json().get("detail", result.text)
        except ValueError:
            detail = result.text
        return Error(f"Can not save the configuration on '{eos_host}': {e}, {detail}")
    except Exception as e:
        return Error(f"Can not save the configuration on '{eos_host}': {e}")
    return Success(f"Saved the configuration to '{config_file_path}' on '{eos_host}'.")


# ---------------------------------------------------------------------------
# Visual representation
# ---------------------------------------------------------------------------


def _step_button(label: Any, step: WizardStep, **kwargs: Any) -> Any:
    """Button that opens a step."""
    return ConfigButton(
        label,
        hx_get=request_url_for(WIZARD_PATH),
        hx_target="#page-content",
        hx_swap="innerHTML",
        hx_vals=json.dumps({"wizard_step": step.id}),
        **kwargs,
    )


def _todo_list(todo: list[str]) -> Any:
    """What is still to do in a step, or that nothing is left to do."""
    if not todo:
        return DivLAligned(UkIcon("circle-check"), P("Nothing left to do in this step."))
    return Div(
        *[DivLAligned(UkIcon("triangle-alert", cls="shrink-0"), P(item)) for item in todo],
        cls="space-y-2",
    )


def WizardSteps(current: WizardStep, config_details: dict[str, dict]) -> Card:
    """Overview of all steps with their state and the navigation between the steps."""
    index = WIZARD_STEPS.index(current)
    badges = []
    for number, step in enumerate(WIZARD_STEPS):
        if step.check is None:
            icon = "circle"
        elif step_todo(step, config_details):
            icon = "triangle-alert"
        else:
            icon = "circle-check"
        badges.append(
            Div(
                Badge(
                    icon, f"{number + 1}. {step.title}", "accent" if step is current else "muted"
                ),
                hx_get=request_url_for(WIZARD_PATH),
                hx_target="#page-content",
                hx_swap="innerHTML",
                hx_vals=json.dumps({"wizard_step": step.id}),
                id=f"wizard-step-{step.id}",
                cls="cursor-pointer",
            )
        )
    return Card(
        Div(*badges, cls="flex flex-wrap gap-2"),
        DivHStacked(
            _step_button("Back", WIZARD_STEPS[index - 1], id="wizard-back") if index > 0 else None,
            _step_button("Next", WIZARD_STEPS[index + 1], id="wizard-next")
            if index + 1 < len(WIZARD_STEPS)
            else None,
            cls="gap-2 mt-4",
        ),
        header=H3(f"Step {index + 1} of {len(WIZARD_STEPS)}: {current.title}"),
    )


def Wizard(
    eos_host: str,
    eos_port: Union[str, int],
    data: Optional[dict] = None,
) -> Div:
    """Create the setup wizard page.

    Args:
        eos_host (str): The hostname of the EOS server.
        eos_port (Union[str, int]): The port of the EOS server.
        data (Optional[dict], optional): Request data: the step to show in ``wizard_step`` and
            optionally an ``action`` (``update`` a configuration value, ``save_to_file``).

    Returns:
        Div: The wizard page showing one step.
    """
    data = data or {}
    step = wizard_step(data.get("wizard_step"))

    # The lazily loaded options of some configuration cards are fetched from the EOS server
    remember_eos_server(eos_host, eos_port)

    save_status = None
    action = data.get("action")
    if action == "update":
        config_update(eos_host, eos_port, data)
    elif action == "save_to_file":
        save_status = save_config(eos_host, eos_port)

    # (Re-)read the configuration to show the actual state
    config_details = create_config_details(ConfigEOS, get_config(eos_host, eos_port))

    content: list[Any] = [
        WizardSteps(step, config_details),
        Card(Markdown(step.intro)),
    ]
    if step.example:
        content.append(Card(Markdown(step.example), header=H3("Example")))

    if step.check:
        content.append(Card(_todo_list(step_todo(step, config_details)), header=H3("Status")))

    for name in step_config_names(step, config_details):
        card = config_card(config_details[name], config_details)
        if card is not None:
            content.append(card)

    if step is WIZARD_STEPS[-1]:
        summary = [
            Div(
                P(f"{WIZARD_STEPS.index(other) + 1}. {other.title}", cls="font-bold"),
                _todo_list(step_todo(other, config_details)),
                cls="space-y-2",
            )
            for other in WIZARD_STEPS
            if other.check
        ]
        content.append(Card(*summary, header=H3("Status"), cls="space-y-4"))
        content.append(
            Card(
                DivHStacked(
                    Div(
                        ConfigButton(
                            "Save configuration",
                            hx_post=request_url_for(WIZARD_PATH),
                            hx_target="#page-content",
                            hx_swap="innerHTML",
                            hx_vals=json.dumps({"action": "save_to_file"}),
                            id="wizard-save",
                        ),
                        cls="shrink-0",
                    ),
                    save_status,
                    cls="gap-4",
                ),
            )
        )

    # hx-vals is inherited: all requests from within the wizard carry the current step
    return Div(
        *content,
        hx_vals=json.dumps({"wizard_step": step.id}),
        id="wizard",
        cls="space-y-4",
    )
