"""Keep imported sale revenues separate from purchase prices in main's async GENETIC path.

Adapted from PRs #1224 (Christin) and #1304 (Normann). Main has no direct-marketing
parameter override yet: these regressions cover its existing preparation/simulation
contract and refuse unavailable imported revenue instead of creating a demo tariff.
"""

from unittest.mock import AsyncMock, Mock, patch

import numpy as np
import pandas as pd
import pytest

from akkudoktoreos.devices.genetic.inverter import Inverter
from akkudoktoreos.optimization.genetic.genetic import GeneticSimulation
from akkudoktoreos.optimization.genetic.geneticdevices import InverterParameters
from akkudoktoreos.optimization.genetic.geneticparams import (
    GeneticOptimizationParameters,
)
from akkudoktoreos.prediction.feedintariffabc import FeedInTariffDataRecord
from akkudoktoreos.prediction.feedintariffimport import FeedInTariffImport
from akkudoktoreos.utils.datetimeutil import to_datetime


@pytest.fixture
def prepare_tariffs(config_eos):
    """Run real async preparation with deterministic forecasts and no device fallback."""

    async def prepare(provider, revenues, tariff_reader=None):
        config_eos.merge_settings_from_dict(
            {
                "prediction": {"hours": 24},
                "optimization": {"genetic": {"horizon_hours": 24, "interval_sec": 3600}},
                "feedintariff": {"provider": provider},
                "elecfee": {"provider": None},
                "devices": {
                    "max_batteries": 0,
                    "max_electric_vehicles": 0,
                    "max_inverters": 0,
                    "max_home_appliances": 0,
                },
            }
        )
        prices = np.array([0.000269, -0.00002] * 12)
        arrays = {
            "weather_temp_air": np.full(24, 20.0),
            "pvforecast_ac_power": np.full(24, 1000.0),
            "loadforecast_power_w": np.zeros(24),
            "elecprice_marketprice_wh": prices,
            "feed_in_tariff_wh": revenues,
        }

        async def read_array(key, **kwargs):
            if key == "feed_in_tariff_wh" and tariff_reader is not None:
                return await tariff_reader(key=key, **kwargs)
            value = arrays[key]
            if isinstance(value, Exception):
                raise value
            return np.asarray(value)

        prediction = Mock(update_data=AsyncMock(), key_to_array=AsyncMock(side_effect=read_array))
        ems = Mock(start_datetime=to_datetime("2026-08-01T00:00:00+00:00"))
        ems.genetic_solution.return_value = None
        with (
            patch("akkudoktoreos.optimization.genetic.geneticparams.get_ems", return_value=ems),
            patch.object(GeneticOptimizationParameters, "prediction", prediction),
        ):
            parameters = await GeneticOptimizationParameters.prepare()
        return parameters, prices, prediction

    return prepare


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "provider",
    [
        "FeedInTariffImport",
        "FeedInTariffAkkudoktor",
        "FeedInTariffEnergyCharts",
        "FeedInTariffTibber",
        "FeedInTariffFixed",
        "FeedInTariffSMARD",
        "FeedInTariffDvhubOnline",
    ],
)
@pytest.mark.parametrize("revenues", [[0.00007], [0.0], [-0.00005], [0.000184, -0.00005]])
async def test_provider_revenues_survive_preparation_and_simulation(
    prepare_tariffs, provider, revenues, config_eos
):
    expected = (revenues * 24)[:24]
    parameters, prices, prediction = await prepare_tariffs(provider, expected)
    assert parameters is not None
    assert parameters.ems.feed_in_tariff_per_wh == expected
    assert parameters.ems.einspeiseverguetung_euro_pro_wh == expected
    assert parameters.ems.electricity_price_per_wh == prices.tolist()
    assert config_eos.feedintariff.provider == provider
    prediction.update_data.assert_awaited_once()

    # A 1 kWh export at 0.00007 amount/Wh earns 0.07, not 70 or 0.00007.
    # No battery or household load is needed to expose tariff substitution/unit bugs.
    inverter = Inverter(InverterParameters(device_id="inverter1", max_power_wh=10000))
    inverter.self_consumption_predictor = Mock()
    inverter.self_consumption_predictor.calculate_self_consumption.return_value = 1.0
    simulation = GeneticSimulation()
    simulation.prepare(
        parameters.ems, optimization_hours=24, prediction_hours=24, inverter=inverter
    )
    result = simulation.simulate(start_hour=0)
    assert result["Netzeinspeisung_Wh_pro_Stunde"] == pytest.approx([1000.0] * 24)
    assert result["Einnahmen_Euro_pro_Stunde"] == pytest.approx(np.array(expected) * 1000)
    assert result["Gesamteinnahmen_Euro"] == pytest.approx(sum(expected) * 1000)
    assert result["Gesamtbilanz_Euro"] == pytest.approx(-sum(expected) * 1000)
    assert parameters.ems.feed_in_tariff_per_wh == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "revenues",
    [
        KeyError("feed_in_tariff_wh"),
        RuntimeError("import unavailable"),
        [],
        [0.00007] * 23,
        [0.00007] * 25,
        [np.nan] * 24,
        [0.00007] * 23 + [np.nan],
        [np.inf] * 24,
        [-np.inf] * 24,
        [None] * 24,
        [[0.00007]] * 24,
    ],
)
async def test_invalid_import_cancels_without_replacing_provider(
    prepare_tariffs, revenues, config_eos, caplog
):
    parameters, _, prediction = await prepare_tariffs("FeedInTariffImport", revenues)
    assert parameters is None
    assert config_eos.feedintariff.provider == "FeedInTariffImport"
    prediction.update_data.assert_awaited_once()
    assert "canceling optimization" in caplog.text
    assert "FeedInTariffImport" in caplog.text
    assert "defaulting to demo" not in caplog.text


def test_prediction_record_prices_are_already_per_wh():
    record = FeedInTariffDataRecord(feed_in_tariff_wh=0.00007)
    assert record.feed_in_tariff_kwh == pytest.approx(0.07)


@pytest.mark.asyncio
@pytest.mark.parametrize("values", [[0.000184, -0.00005] * 12, [0.0] * 24, [None] * 24])
async def test_timestamped_import_records_are_read_in_order(prepare_tariffs, values):
    provider = FeedInTariffImport()
    provider._db_reset_state()
    start = to_datetime("2026-08-01T00:00:00+00:00").set(hour=0)
    try:
        await provider.key_from_series(
            "feed_in_tariff_wh",
            pd.Series(values, index=pd.date_range(start=start, periods=24, freq="h")),
        )
        parameters, _, _ = await prepare_tariffs(
            "FeedInTariffImport", [], tariff_reader=provider.key_to_array
        )
        if values[0] is None:
            assert parameters is None
        else:
            assert parameters is not None
            assert parameters.ems.feed_in_tariff_per_wh == pytest.approx(values)
    finally:
        provider._db_reset_state()
