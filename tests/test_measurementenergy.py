"""Physical and temporal contracts for interval energy."""

# ruff: noqa: S101

from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from akkudoktoreos.measurement.energy import (
    energy_intervals,
    hourly_energy_to_meter_readings,
    valid_reading,
)
from akkudoktoreos.measurement.measurement import MeasurementChannelSettings
from akkudoktoreos.measurement.quality import SampleQuality
from akkudoktoreos.utils.datetimeutil import to_datetime, to_duration

START = datetime(2026, 9, 10, tzinfo=timezone.utc)


def channel(quantity="power", **kwargs):
    defaults: dict[str, dict[str, Any]] = {
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
    assert all(r.energy_wh is not None for r in result)
    assert sum(r.energy_wh for r in result if r.energy_wh is not None) == 1000
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


@pytest.mark.parametrize("quantity,unit", [("temperature", "degC"), ("presence", "1")])
def test_direct_energy_helper_rejects_nonenergy_channels(quantity, unit):
    config = MeasurementChannelSettings(quantity=quantity, unit=unit)
    with pytest.raises(ValueError, match="requires a power or energy channel"):
        convert([(0, 1), (900, 1)], config)


@pytest.mark.parametrize("month,day,hours", [(3, 29, 23), (10, 25, 25)])
def test_dst_calendar_day(month, day, hours):
    start = datetime(2026, month, day, tzinfo=ZoneInfo("Europe/Berlin"))
    end = start + timedelta(days=1)
    result = energy_intervals([(start, 0), (end, hours)], channel("cumulative_energy"), start, end)
    assert len(result) == hours * 4
    assert all(r.energy_wh is not None for r in result)
    assert sum(r.energy_wh for r in result if r.energy_wh is not None) == pytest.approx(
        hours * 1000
    )


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
        (await measurement.update_value(to_datetime(START), "p", 800))
        (await measurement.update_value(to_datetime(START + timedelta(seconds=450)), "other", 1))
        (await measurement.update_value(to_datetime(START + timedelta(seconds=900)), "p", 800))
        result = await measurement.energy_intervals("p", START, START + timedelta(seconds=900))
        assert result[0].energy_wh == 200
    finally:
        measurement._db_reset_state()
        measurement.records = records
        config_eos.measurement = previous


@pytest.mark.parametrize(
    ("value", "quality", "expected"),
    [
        (1, None, 1.0),
        (2.5, SampleQuality(status="estimated"), 2.5),
        (2.5, SampleQuality(status="invalid"), None),
        (2.5, SampleQuality(status="unavailable"), None),
        (True, None, None),
        (float("nan"), None, None),
        (float("inf"), None, None),
        (None, None, None),
        ("1.0", None, None),
    ],
)
def test_valid_reading(value: Any, quality: SampleQuality | None, expected: float | None) -> None:
    """One rule decides which raw readings count, shared by conversion and scans."""
    assert valid_reading(value, quality) == expected


def hour_start(text: str):
    """Start of an hour, with an explicit offset so that the instant is unambiguous."""
    return to_datetime(text)


class TestHourlyEnergyToMeterReadings:
    """Energy of whole hours as cumulative meter readings (`/gesamtlast`)."""

    def test_reading_sits_at_the_end_of_its_hour(self):
        """Start reading 0.0 on the first hour, then each running sum at the end of its hour."""
        dates, values = hourly_energy_to_meter_readings(
            [
                (hour_start("2026-11-26T05:00:00+01:00"), 2.0),
                (hour_start("2026-11-26T06:00:00+01:00"), 3.0),
                (hour_start("2026-11-26T07:00:00+01:00"), 0.5),
            ]
        )

        assert [d.isoformat() for d in dates] == [
            to_datetime("2026-11-26T05:00:00+01:00").isoformat(),
            to_datetime("2026-11-26T06:00:00+01:00").isoformat(),
            to_datetime("2026-11-26T07:00:00+01:00").isoformat(),
            to_datetime("2026-11-26T08:00:00+01:00").isoformat(),
        ]
        assert values == [0.0, 2.0, 5.0, 5.5]

    def test_energy_of_an_hour_is_the_difference_across_that_hour(self):
        """R(t + 1 h) - R(t) is the energy that was passed in for the hour starting at t."""
        energy = {
            to_datetime("2026-11-26T05:00:00+01:00"): 2.0,
            to_datetime("2026-11-26T06:00:00+01:00"): 3.0,
            to_datetime("2026-11-26T07:00:00+01:00"): 0.5,
        }
        dates, values = hourly_energy_to_meter_readings(energy.items())
        reading = {d.timestamp(): v for d, v in zip(dates, values)}

        for start, kwh in energy.items():
            end = start + to_duration("1 hour")
            assert reading[end.timestamp()] - reading[start.timestamp()] == pytest.approx(kwh)

    def test_empty_input(self):
        assert hourly_energy_to_meter_readings([]) == ([], [])

    def test_single_hour(self):
        dates, values = hourly_energy_to_meter_readings(
            [(hour_start("2026-11-26T06:00:00+01:00"), 1.5)]
        )

        assert [d.isoformat() for d in dates] == [
            to_datetime("2026-11-26T06:00:00+01:00").isoformat(),
            to_datetime("2026-11-26T07:00:00+01:00").isoformat(),
        ]
        assert values == [0.0, 1.5]

    def test_unsorted_input_is_sorted_by_time_not_by_text(self):
        """Hours in any order give the readings of the sorted hours, also across a day change."""
        ordered = [
            (hour_start("2026-11-26T23:00:00+01:00"), 1.0),
            (hour_start("2026-11-27T00:00:00+01:00"), 2.0),
            (hour_start("2026-11-27T01:00:00+01:00"), 4.0),
        ]
        shuffled = [ordered[2], ordered[0], ordered[1]]

        assert hourly_energy_to_meter_readings(shuffled) == hourly_energy_to_meter_readings(ordered)
        dates, values = hourly_energy_to_meter_readings(shuffled)
        assert values == [0.0, 1.0, 3.0, 7.0]
        assert dates == sorted(dates)

    def test_mixed_offsets_are_ordered_by_instant(self):
        """Wall clock text of different offsets does not decide the order, the instant does."""
        later_in_utc = (hour_start("2026-11-26T06:00:00+00:00"), 2.0)  # 07:00 in Berlin
        earlier_in_utc = (hour_start("2026-11-26T06:30:00+01:00"), 1.0)  # 05:30 UTC

        dates, values = hourly_energy_to_meter_readings([later_in_utc, earlier_in_utc])

        assert values == [0.0, 1.0, 3.0]
        assert dates[0].timestamp() == earlier_in_utc[0].timestamp()
        assert dates[1].timestamp() == earlier_in_utc[0].timestamp() + 3600

    def test_duplicate_hour_keeps_its_last_value(self):
        """A repeated hour is one hour, not two: its energy is not added twice."""
        dates, values = hourly_energy_to_meter_readings(
            [
                (hour_start("2026-11-26T06:00:00+01:00"), 1.0),
                (hour_start("2026-11-26T07:00:00+01:00"), 2.0),
                (hour_start("2026-11-26T06:00:00+01:00"), 3.0),
            ]
        )

        assert len(dates) == 3
        assert values == [0.0, 3.0, 5.0]

    def test_duplicate_hour_in_another_offset_is_the_same_hour(self):
        dates, values = hourly_energy_to_meter_readings(
            [
                (hour_start("2026-11-26T06:00:00+01:00"), 1.0),
                (hour_start("2026-11-26T05:00:00+00:00"), 4.0),
            ]
        )

        assert len(dates) == 2
        assert values == [0.0, 4.0]

    def test_gap_hour_gets_no_reading(self):
        """A missing hour is not filled with zero: the readings skip it."""
        dates, values = hourly_energy_to_meter_readings(
            [
                (hour_start("2026-11-26T06:00:00+01:00"), 1.0),
                (hour_start("2026-11-26T07:00:00+01:00"), 2.0),
                (hour_start("2026-11-26T09:00:00+01:00"), 4.0),  # 08:00 is missing
            ]
        )

        assert [d.timestamp() for d in dates] == [
            to_datetime(text).timestamp()
            for text in (
                "2026-11-26T06:00:00+01:00",
                "2026-11-26T07:00:00+01:00",
                "2026-11-26T08:00:00+01:00",
                "2026-11-26T10:00:00+01:00",
            )
        ]
        assert values == [0.0, 1.0, 3.0, 7.0]

    def test_hours_are_elapsed_hours_on_the_daylight_saving_change(self):
        """25.10.2026 in Europe/Berlin has 25 hours; the wall clock 02:00 happens twice."""
        first = to_datetime("2026-10-24T22:00:00+00:00")  # 00:00 CEST
        hours = [(first + to_duration(f"{k} hours"), 1.0 + k / 10) for k in range(25)]

        dates, values = hourly_energy_to_meter_readings(hours)

        assert len(dates) == 26
        assert all(b.timestamp() - a.timestamp() == 3600 for a, b in zip(dates, dates[1:]))
        assert values[0] == 0.0
        assert values[-1] == pytest.approx(sum(energy for _, energy in hours))
        # Both 02:00 of that night are distinct readings.
        berlin = [d.in_timezone("Europe/Berlin") for d in dates]
        two_o_clock = [d for d in berlin if d.hour == 2 and d.minute == 0]
        assert len(two_o_clock) == 2
        assert {d.utcoffset().total_seconds() for d in two_o_clock} == {7200, 3600}

    def test_text_hours_with_both_offsets_of_the_change_stay_two_hours(self):
        """02:00+02:00 and 02:00+01:00 are different instants and keep their own energy."""
        dates, values = hourly_energy_to_meter_readings(
            [
                (hour_start("2026-10-25T02:00:00+02:00"), 1.0),
                (hour_start("2026-10-25T02:00:00+01:00"), 2.0),
            ]
        )

        assert len(dates) == 3
        assert values == [0.0, 1.0, 3.0]
