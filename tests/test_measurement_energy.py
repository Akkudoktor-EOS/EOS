"""Physical and temporal contracts for interval energy."""

# ruff: noqa: S101

from datetime import datetime, timedelta, timezone

import pytest
from zoneinfo import ZoneInfo

from akkudoktoreos.measurement.energy import energy_intervals
from akkudoktoreos.measurement.measurement import MeasurementChannelSettings


START = datetime(2026, 9, 10, tzinfo=timezone.utc)


def channel(quantity="power", **kwargs):
    defaults = {
        "power": dict(unit="W", integration_method="hold", max_gap_seconds=900),
        "cumulative_energy": dict(unit="kWh"),
        "interval_energy": dict(unit="Wh", interval_seconds=900, timestamp_reference="start"),
    }
    return MeasurementChannelSettings(quantity=quantity, **(defaults[quantity] | kwargs))


def convert(points, config=None, seconds=900):
    return energy_intervals(
        [(START + timedelta(seconds=t), v) for t, v in points],
        config or channel(),
        START,
        START + timedelta(seconds=seconds),
    )


@pytest.mark.parametrize(
    "config,points",
    [
        (channel(), [(0, 800), (900, 800)]),
        (channel(unit="kW"), [(0, 0.8), (900, 0.8)]),
        (channel("cumulative_energy"), [(0, 10), (900, 10.2)]),
        (channel("interval_energy"), [(0, 200)]),
        (channel("interval_energy", timestamp_reference="end"), [(900, 200)]),
    ],
)
def test_equivalent_measurements(config, points):
    result = convert(points, config)[0]
    assert result.energy_wh == pytest.approx(200)
    assert result.coverage_seconds == 900
    assert result.coverage_status == "complete"


def test_time_weighting_and_linear_interpolation():
    assert convert([(0, 0), (600, 1200), (900, 1200)])[0].energy_wh == 100
    assert convert([(0, 0), (900, 1600)], channel(integration_method="linear"))[0].energy_wh == 200


def test_gap_and_no_extrapolation():
    result = convert([(0, 800), (300, 800)])[0]
    assert result.energy_wh is None
    assert result.observed_energy_wh == pytest.approx(800 / 12)
    assert result.coverage_status == "partial"
    assert convert([(0, 800), (900, 800)], channel(max_gap_seconds=60))[0].energy_wh is None
    assert convert([])[0].coverage_status == "missing"


def test_null_breaks_hold_at_outage():
    result = convert([(0, 800), (300, None), (600, 800), (900, 800)])[0]
    assert result.coverage_seconds == 600
    assert result.coverage_status == "partial"
    assert len(result.coverage_ranges) == 2


def test_reset_does_not_create_negative_consumption():
    result = convert([(0, 10), (450, 0), (900, 0.1)], channel("cumulative_energy"))[0]
    assert result.energy_wh is None
    assert result.observed_energy_wh == pytest.approx(100)
    assert "meter_reset" in result.flags


def test_hour_allocation_conserves_energy_and_is_labelled():
    result = convert([(0, 1000)], channel("interval_energy", interval_seconds=3600), 3600)
    assert sum(r.energy_wh for r in result) == 1000
    assert all("allocated_energy" in r.methods for r in result)


@pytest.mark.parametrize(
    "quantity,points",
    [
        ("power", [(0, 1), (0, 2)]),
        ("interval_energy", [(0, 100), (450, 100)]),
    ],
)
def test_ambiguous_time_support_rejected(quantity, points):
    with pytest.raises(ValueError):
        convert(points, channel(quantity))


def test_nan_and_signed_power():
    assert convert([(0, float("nan")), (900, 800)])[0].energy_wh is None
    assert convert([(0, -800), (900, -800)])[0].energy_wh == -200


@pytest.mark.parametrize("month,day,hours", [(3, 29, 23), (10, 25, 25)])
def test_dst_calendar_day(month, day, hours):
    start = datetime(2026, month, day, tzinfo=ZoneInfo("Europe/Berlin"))
    end = start + timedelta(days=1)
    result = energy_intervals([(start, 0), (end, hours)], channel("cumulative_energy"), start, end)
    assert len(result) == hours * 4
    assert sum(r.energy_wh for r in result) == pytest.approx(hours * 1000)


@pytest.mark.asyncio
async def test_measurement_wrapper_preserves_asynchronous_channels(config_eos):
    from akkudoktoreos.core.coreabc import get_measurement
    from akkudoktoreos.measurement.measurement import MeasurementCommonSettings

    measurement = get_measurement()
    previous, records = config_eos.measurement, measurement.records
    try:
        config_eos.measurement = MeasurementCommonSettings(
            channels={"p": channel(), "other": channel()}
        )
        measurement._db_reset_state()
        (await measurement.update_value(START, "p", 800))
        (await measurement.update_value(START + timedelta(seconds=450), "other", 1))
        (await measurement.update_value(START + timedelta(seconds=900), "p", 800))
        result = (await measurement.energy_intervals("p", START, START + timedelta(seconds=900)))
        assert result[0].energy_wh == 200
    finally:
        measurement._db_reset_state()
        measurement.records = records
        config_eos.measurement = previous
