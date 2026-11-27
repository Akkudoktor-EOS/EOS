"""Shared fixtures and configuration pieces of the planning request tests."""

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from akkudoktoreos.config.config import ConfigEOS
from akkudoktoreos.core.coreabc import get_ems
from akkudoktoreos.optimization.request import PlanningRequest, RuntimeForecasts
from akkudoktoreos.optimization.request.devices import DeviceResolver
from akkudoktoreos.utils.datetimeutil import to_datetime

DIR_TESTDATA = Path(__file__).parent / "testdata" / "genetic"

ems_eos = get_ems(init=True)


class FakeMeasurement:
    """Measurement store with fixed histories per key; unknown keys raise ``KeyError``."""

    def __init__(self, histories: dict[str, list[tuple[Any, float]]]) -> None:
        self.histories = histories

    async def key_to_lists(
        self, key: str, start_datetime: Any, end_datetime: Any, dropna: bool = True
    ) -> tuple[list[Any], list[Any]]:
        """Return the dates and values of a key within the range."""
        if key not in self.histories:
            raise KeyError(key)
        rows = [
            (date, value)
            for date, value in self.histories[key]
            if start_datetime <= date < end_datetime
        ]
        return [date for date, _ in rows], [value for _, value in rows]


@pytest.fixture(name="fake_measurement")
def fake_measurement_fixture(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Replace the measurement of the planning request; fill ``histories`` in the test."""
    measurement = FakeMeasurement({})
    monkeypatch.setattr(DeviceResolver, "measurement", measurement)
    return measurement


def forecast_series(input_name: str, interval: int) -> dict[str, list[float]]:
    """Return the four forecast series of a test input from local midnight on the grid."""
    with (DIR_TESTDATA / input_name).open("r") as handle:
        ems = json.load(handle)["ems"]
    pv = list(ems["pv_prognose_wh"])
    load = list(ems["gesamtlast"])
    price = list(ems["strompreis_euro_pro_wh"])
    tariff = ems["einspeiseverguetung_euro_pro_wh"]
    tariff = list(tariff) if isinstance(tariff, list) else [float(tariff)] * len(pv)
    if interval == 900:
        pv = [v / 4 for v in pv for _ in range(4)]
        load = [v / 4 for v in load for _ in range(4)]
        price = [v for v in price for _ in range(4)]
        tariff = [v for v in tariff for _ in range(4)]
    return {
        "pv_forecast_wh": pv,
        "total_load": load,
        "electricity_price_per_wh": price,
        "feed_in_tariff_per_wh": tariff,
    }


BATTERY = {
    "profile_adjustment": {"nominal_capacity_wh": 8000, "max_charge_power_w": 5000},
    "charge_rates": [0.0, 0.25, 0.5, 0.75, 1.0],
    "min_soc_percentage": 10,
}
VEHICLE = {
    "profile_adjustment": {"nominal_capacity_wh": 60000, "max_charge_power_w": 7000},
    "charge_rates": [0.0, 0.375, 0.5, 0.75, 1.0],
    "min_soc_percentage": 80,
}
INVERTER = {"battery_id": "bat1", "profile_adjustment": {"nominal_power_w": 8000}}
DISHWASHER = {"consumption_wh": 1500, "duration_h": 2}

START = "2026-09-16T10:00:00+02:00"


async def resolve_task(
    config_eos: ConfigEOS,
    devices: dict[str, Any],
    soc: Mapping[str, float],
    *,
    interval: int = 3600,
    horizon: int = 24,
    forecasts: Any = None,
    start: str = START,
    extra: Any = None,
    request_extra: Any = None,
) -> Any:
    """Configure the devices and resolve a request with the forecasts of the first test input."""
    settings: dict[str, Any] = {
        "prediction": {"hours": 48},
        "optimization": {
            "algorithm": "OHMRAKEL42",
            "genetic": {
                "horizon_hours": horizon,
                "tail_horizon_hours": 0,
                "terminal_value_mode": "FIXED",
                "interval_sec": interval,
            },
        },
        "devices": devices,
    }
    for key, value in (extra or {}).items():
        settings[key] = value
    config_eos.merge_settings_from_dict(settings)
    ems_eos.set_start_datetime(to_datetime(start, in_timezone="Europe/Berlin"), interval)
    series = forecast_series("optimize_input_1.json", interval) if forecasts is None else forecasts
    return await PlanningRequest(
        soc=dict(soc), forecasts=RuntimeForecasts(**series), **(request_extra or {})
    ).resolve()
