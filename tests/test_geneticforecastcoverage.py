"""Raw forecast coverage must survive resampling without inventing valid intervals."""

from unittest.mock import AsyncMock, Mock

import numpy as np
import pandas as pd
import pytest

from akkudoktoreos.optimization.genetic.forecast import bounded_forecast_array
from akkudoktoreos.utils.datetimeutil import to_datetime, to_duration


@pytest.mark.asyncio
@pytest.mark.parametrize("drop,missing", [(1, 4), (2, 8)])
async def test_hourly_gaps_and_unavailable_tail_are_not_forward_filled(drop, missing):
    index = pd.date_range("2026-09-16T00:00:00Z", periods=4, freq="h").delete(drop)
    prediction = Mock(key_to_raw_series=AsyncMock(return_value=pd.Series([100.0] * 3, index=index)))
    start = to_datetime("2026-09-16T00:00:00Z", in_timezone="UTC")
    values = await bounded_forecast_array(
        prediction,
        key="pv",
        start_datetime=start,
        end_datetime=start.add(hours=5),
        interval=to_duration(900),
    )
    assert np.isnan(values[missing : missing + 4]).all()
    assert np.isnan(values[16:]).all()
    assert np.isfinite(values[:4]).all()


@pytest.mark.asyncio
async def test_downsampling_requires_complete_coverage_and_uses_interval_average():
    index = pd.date_range("2026-09-16T00:00:00Z", periods=8, freq="15min")
    series = pd.Series([100.0, 200.0, 300.0, 400.0, 100.0, np.nan, 100.0, 100.0], index=index)
    prediction = Mock(key_to_raw_series=AsyncMock(return_value=series))
    start = to_datetime("2026-09-16T00:00:00Z", in_timezone="UTC")
    values = await bounded_forecast_array(
        prediction,
        key="pv",
        start_datetime=start,
        end_datetime=start.add(hours=2),
        interval=to_duration(3600),
    )
    assert values[0] == 250.0
    assert np.isnan(values[1])


@pytest.mark.asyncio
async def test_non_aligned_source_intervals_are_weighted_by_actual_overlap():
    index = pd.date_range("2026-09-16T00:05:00Z", periods=4, freq="15min")
    prediction = Mock(
        key_to_raw_series=AsyncMock(
            return_value=pd.Series([100.0, 400.0, 700.0, 1000.0], index=index)
        )
    )
    start = to_datetime("2026-09-16T00:00:00Z", in_timezone="UTC")
    values = await bounded_forecast_array(
        prediction,
        key="pv",
        start_datetime=start,
        end_datetime=start.add(hours=1),
        interval=to_duration(900),
    )
    assert np.isnan(values[0])
    assert values[1] == pytest.approx(300.0)
    assert values[2] == pytest.approx(600.0)
