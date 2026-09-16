"""API, persistence and physical balance contracts for typed measurements."""

# ruff: noqa: S101

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from akkudoktoreos.core.coreabc import get_database, get_measurement
from akkudoktoreos.measurement.measurement import MeasurementCommonSettings
from akkudoktoreos.measurement.quality import MeasurementSample
from akkudoktoreos.server.rest.measurement import router

START = datetime(2026, 9, 10, tzinfo=timezone.utc)
END = START + timedelta(seconds=900)
POWER = dict(quantity="power", unit="W", integration_method="hold", max_gap_seconds=900)


@pytest.fixture
def setup_measurement(config_eos):
    measurement = get_measurement()
    settings, records = config_eos.measurement, measurement.records
    measurement._db_reset_state()

    def configure(topology="direct", inputs=None, channels=None):
        inputs = inputs or [dict(key="site", branch="house", role="site")]
        config_eos.measurement = MeasurementCommonSettings(
            channels=channels or {item["key"]: POWER for item in inputs},
            household=dict(topology=topology, inputs=inputs),
        )
        return measurement

    yield configure
    measurement._db_reset_state()
    measurement.records = records
    config_eos.measurement = settings


async def write(measurement, key, points):
    (await measurement.import_samples(
        [
            MeasurementSample(
                date_time=START + timedelta(seconds=t),
                key=key,
                value=value,
                quality=quality if quality else {},
            )
            for t, value, quality in points
        ]
    ))


async def constant(measurement, key, watts):
    (await write(measurement, key, [(0, watts, None), (900, watts, None)]))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "topology,values",
    [
        ("direct", {"site": 4000}),
        ("separate_ac", {"grid": 1000, "pv": 2000, "battery": 1000}),
        ("hybrid_ac", {"grid": 1000, "inverter": 3000}),
    ],
)
async def test_topologies_and_device_subtraction(setup_measurement, topology, values):
    values = values | {"ev": 2000, "device": 800}
    m = setup_measurement(topology, [dict(key=k, branch=k, role=k) for k in values])
    for key, value in values.items():
        (await constant(m, key, value))
    rows = (await m.household_intervals(START, END))
    assert rows["site"][0].energy_wh == 1000
    assert rows["household"][0].energy_wh == 500
    assert rows["base"][0].energy_wh == 300


@pytest.mark.asyncio
async def test_export_charge_and_polarity(setup_measurement):
    m = setup_measurement(
        "separate_ac",
        [
            dict(key="grid", branch="grid", role="grid"),
            dict(key="solar", branch="solar", role="pv"),
            dict(key="charge", branch="battery", role="battery", polarity=-1),
        ],
    )
    for key, watts in dict(grid=-1000, solar=4000, charge=1000).items():
        (await constant(m, key, watts))
    assert (await m.household_intervals(START, END))["site"][0].energy_wh == 500


@pytest.mark.asyncio
@pytest.mark.parametrize("grid,inverter", [(-6000, 7200), (4200, -3000)])
async def test_hybrid_with_independent_ac_pv_and_ev(setup_measurement, grid, inverter):
    inputs = [dict(key=k, branch=k, role=k) for k in ("grid", "inverter", "pv", "ev")]
    m = setup_measurement("hybrid_ac", inputs)
    for key, watts in dict(grid=grid, inverter=inverter, pv=400, ev=600).items():
        (await constant(m, key, watts))
    rows = (await m.household_intervals(START, END))
    assert rows["site"][0].energy_wh == 400
    assert rows["household"][0].energy_wh == 250


@pytest.mark.asyncio
async def test_hybrid_missing_ac_pv_is_not_zero(setup_measurement):
    m = setup_measurement(
        "hybrid_ac", [dict(key=k, branch=k, role=k) for k in ("grid", "inverter", "pv")]
    )
    (await constant(m, "grid", -6000))
    (await constant(m, "inverter", 7200))
    row = (await m.household_intervals(START, END))["site"][0]
    assert row.energy_wh is None
    assert row.coverage_seconds == 0


def test_hybrid_rejects_separate_battery_to_avoid_double_counting(setup_measurement):
    with pytest.raises(ValidationError):
        setup_measurement(
            "hybrid_ac",
            [dict(key=k, branch=k, role=k) for k in ("grid", "inverter", "battery")],
        )


@pytest.mark.asyncio
async def test_missing_ev_does_not_destroy_site(setup_measurement):
    m = setup_measurement(inputs=[dict(key=k, branch=k, role=k) for k in ("site", "ev")])
    (await constant(m, "site", 800))
    rows = (await m.household_intervals(START, END))
    assert rows["site"][0].energy_wh == 200
    assert rows["household"][0].energy_wh is None
    assert rows["household"][0].coverage_seconds == 0


@pytest.mark.asyncio
async def test_intersection_integrates_actual_shape(setup_measurement):
    m = setup_measurement(inputs=[dict(key=k, branch=k, role=k) for k in ("site", "ev")])
    (await write(m, "site", [(0, 1000, None), (300, 2000, None), (600, None, None)]))
    (await write(m, "ev", [(300, 500, None), (900, 500, None)]))
    row = (await m.household_intervals(START, END))["household"][0]
    assert row.coverage_seconds == 300
    assert row.energy_wh is None
    assert row.observed_energy_wh == pytest.approx(125)


@pytest.mark.asyncio
async def test_nonoverlapping_coverage_is_missing(setup_measurement):
    m = setup_measurement(inputs=[dict(key=k, branch=k, role=k) for k in ("site", "ev")])
    (await write(m, "site", [(0, 1000, None), (300, 1000, None)]))
    (await write(m, "ev", [(600, 500, None), (900, 500, None)]))
    row = (await m.household_intervals(START, END))["household"][0]
    assert row.observed_energy_wh is None
    assert row.coverage_seconds == 0


@pytest.mark.parametrize(
    "inputs",
    [
        [dict(key="p", branch="x", role="grid"), dict(key="p", branch="y", role="pv")],
        [dict(key="p", branch="x", role="grid"), dict(key="q", branch="x", role="pv")],
        [dict(key="p", branch="x", role="site"), dict(key="q", branch="y", role="pv")],
        [dict(key="unknown", branch="x", role="grid")],
    ],
)
def test_invalid_balance_configuration(setup_measurement, inputs):
    with pytest.raises(ValidationError):
        setup_measurement("separate_ac", inputs, {"p": POWER, "q": POWER})


@pytest.mark.asyncio
async def test_quality_reset_even_with_increasing_meter(setup_measurement):
    m = setup_measurement(channels={"site": dict(quantity="cumulative_energy", unit="Wh")})
    (await write(
        m,
        "site",
        [
            (0, 0, {"generation": "old"}),
            (450, 1000, {"generation": "new", "reset": True}),
            (900, 1100, {"generation": "new", "status": "estimated"}),
        ],
    ))
    row = (await m.energy_intervals("site", START, END))[0]
    assert row.energy_wh is None
    assert row.observed_energy_wh == 100
    assert set(row.flags) == {"meter_reset", "estimated"}


@pytest.mark.asyncio
async def test_api_upsert_outage_validation_and_readback(setup_measurement):
    m = setup_measurement()
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        payload = [
            dict(
                date_time=START.isoformat(), key="site", value=800, quality={"status": "estimated"}
            ),
            dict(date_time=END.isoformat(), key="site", value=800),
        ]
        assert client.put("/v1/measurement/samples", json=payload).status_code == 200
        params = dict(key="site", start=START.isoformat(), end=END.isoformat())
        row = client.get("/v1/measurement/energy", params=params).json()[0]
        assert row["energy_wh"] == 200
        assert "estimated" in row["flags"]
        raw = client.get("/v1/measurement/samples", params=params).json()
        assert raw[0]["quality"]["status"] == "estimated"
        payload[0]["value"] = None
        payload[0]["quality"] = {"status": "unavailable"}
        assert client.put("/v1/measurement/samples", json=payload[:1]).status_code == 200
        row = client.get("/v1/measurement/energy", params=params).json()[0]
        assert row["energy_wh"] is None
        assert "unavailable" in row["flags"]
        assert len(m.records) == 2
        payload[0]["value"] = 0
        payload[0]["quality"] = {}
        assert client.put("/v1/measurement/samples", json=payload[:1]).status_code == 200
        assert client.get("/v1/measurement/energy", params=params).json()[0]["energy_wh"] == 0
        assert (
            client.get("/v1/measurement/household", params=params).json()["site"][0]["energy_wh"]
            == 0
        )
        # Validate a whole batch before changing any value.
        bad = [payload[0] | {"value": 999}, payload[0] | {"key": "unknown"}]
        assert client.put("/v1/measurement/samples", json=bad).status_code == 422
        assert (await m.energy_intervals("site", START, END))[0].energy_wh == 0
        assert (
            client.get(
                "/v1/measurement/energy", params=params | {"interval_seconds": 0}
            ).status_code
            == 422
        )
        assert (
            client.get(
                "/v1/measurement/energy", params=params | {"start": "2026-09-10T00:00:00"}
            ).status_code
            == 422
        )
        assert (
            client.get(
                "/v1/measurement/energy",
                params=params | {"end": (START + timedelta(days=32)).isoformat()},
            ).status_code
            == 422
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", [None, "SQLite", "LMDB"])
async def test_quality_persists_through_storage_restart(
    setup_measurement, config_eos, tmp_path, provider
):
    m = setup_measurement()
    folder, previous_db = config_eos.general.data_folder_path, config_eos.database.provider
    try:
        config_eos.general.data_folder_path = tmp_path
        config_eos.database.provider = provider
        (await write(
            m,
            "site",
            [
                (0, 800, {"status": "estimated", "generation": "meter1"}),
                (450, None, {"status": "unavailable"}),
                (900, 800, None),
            ],
        ))
        before = (await m.energy_intervals("site", START, END))
        assert (await m.save())
        if provider:
            (await get_database().close())
        m._db_reset_state()
        if not provider:
            assert (await m.load())
        # DB loads on demand via the bounded query, without a full-history load.
        assert (await m.energy_intervals("site", START, END)) == before
        assert m.records[0].sample_quality["site"].generation == "meter1"
    finally:
        if provider:
            (await get_database().close())
        m._db_reset_state()
        config_eos.database.provider = previous_db
        config_eos.general.data_folder_path = folder


@pytest.mark.asyncio
async def test_query_passes_bounded_storage_window(setup_measurement, monkeypatch):
    m = setup_measurement()
    calls = []
    original = type(m).db_iterate_records

    def record_window(self, start_timestamp=None, end_timestamp=None):
        calls.append((start_timestamp, end_timestamp))
        return original(self, start_timestamp, end_timestamp)

    monkeypatch.setattr(type(m), "db_iterate_records", record_window)
    (await m.energy_intervals("site", START, END))
    assert calls and all(a is not None and b is not None for a, b in calls)


@pytest.mark.asyncio
async def test_actual_server_routes_and_legacy_value_api(setup_measurement):
    setup_measurement()
    from akkudoktoreos.server.eos import app

    # No lifespan: this test must not start schedulers or write application state.
    client = TestClient(app)
    try:
        for time in (START, END):
            response = client.put(
                "/v1/measurement/value",
                params={
                    "datetime": time.isoformat(),
                    "key": "site",
                    "value": 800,
                },
            )
            assert response.status_code == 200, response.text
        response = client.get(
            "/v1/measurement/energy",
            params={
                "key": "site",
                "start": START.isoformat(),
                "end": END.isoformat(),
            },
        )
        assert response.status_code == 200
        assert response.json()[0]["energy_wh"] == 200
        assert "sample_quality" not in get_measurement().record_keys
    finally:
        client.close()


@pytest.mark.asyncio
async def test_quality_merge_keeps_other_channels(setup_measurement):
    from akkudoktoreos.measurement.measurement import MeasurementDataRecord

    m = setup_measurement(inputs=[dict(key=k, branch=k, role=k) for k in ("site", "ev")])
    (await write(m, "site", [(0, 800, {"status": "estimated"})]))
    (await write(m, "ev", [(0, None, {"status": "unavailable"})]))
    (await m.insert_by_datetime(
        MeasurementDataRecord(date_time=START, sample_quality={"site": {"status": "measured"}})
    ))
    assert m.records[0].sample_quality["site"].status == "measured"
    assert m.records[0].sample_quality["ev"].status == "unavailable"


@pytest.mark.asyncio
async def test_hour_energy_allocation_and_short_last_interval(setup_measurement):
    m = setup_measurement(
        channels={
            "site": dict(
                quantity="interval_energy",
                unit="Wh",
                interval_seconds=3600,
                timestamp_reference="start",
            )
        }
    )
    (await write(m, "site", [(0, 1000, None)]))
    rows = (await m.household_intervals(START, START + timedelta(seconds=1800)))["site"]
    assert [row.energy_wh for row in rows] == [250, 250]
    assert all("allocated_energy" in row.methods for row in rows)
    row = (await m.energy_intervals("site", START, START + timedelta(seconds=60)))[0]
    assert row.coverage_seconds == 60
    assert row.energy_wh == pytest.approx(1000 / 60)
