"""Reported absences enter the presence forecast through one REST entry point, never as a command."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from akkudoktoreos.core.coreabc import get_prediction
from akkudoktoreos.prediction.presence import PresenceHistory
from akkudoktoreos.server.eos import app


@pytest.fixture
def presence_provider(config_eos, tmp_path):
    config_eos.general.data_folder_path = tmp_path
    config_eos.merge_settings_from_dict(
        {
            "presence": {
                "provider": "PresenceHistory",
                "zones": {"area1": {"measurement_key": "motion"}},
            },
            "measurement": {"channels": {"motion": {"quantity": "presence", "unit": "1"}}},
        }
    )
    provider = next(row for row in get_prediction().providers if isinstance(row, PresenceHistory))
    provider._absence_store = None
    return provider


def test_reported_absence_is_stored_and_listed(presence_provider):
    client = TestClient(app)
    end = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()
    created = client.post("/v1/thermal/absences", json={"end": end, "label": "Node-RED"})
    assert created.status_code == 200
    ident = created.json()["id"]
    assert client.get("/v1/thermal/absences").json()["absences"][0]["id"] == ident
    assert (
        tmp := presence_provider.absence_store().path
    ).exists() and tmp.name == "presence-absences.json"


def test_reported_absence_reaches_the_presence_forecast_as_evidence_not_a_command(
    presence_provider,
):
    import asyncio

    client = TestClient(app)
    start = datetime.now(timezone.utc) - timedelta(minutes=5)
    client.post(
        "/v1/thermal/absences",
        json={"start": start.isoformat(), "end": (start + timedelta(hours=3)).isoformat()},
    )
    now = datetime.now(timezone.utc)
    starts = [now + timedelta(minutes=15 * i) for i in range(1, 5)]
    estimates = asyncio.run(
        presence_provider.forecast_zone("area1", now=now, starts=starts, publish=False)
    )
    assert [row.source for row in estimates] == ["reported_absence"] * 4
    assert not any(row.require_comfort for row in estimates)


def test_back_home_ends_the_running_absence_and_keeps_it_for_learning(presence_provider):
    client = TestClient(app)
    start = datetime.now(timezone.utc) - timedelta(hours=1)
    ident = client.post(
        "/v1/thermal/absences",
        json={"start": start.isoformat(), "end": (start + timedelta(hours=9)).isoformat()},
    ).json()["id"]
    ended = client.post("/v1/thermal/absences/back").json()["ended"]
    assert [row["id"] for row in ended] == [ident]
    assert client.get("/v1/thermal/absences").json()["active"] is None


def test_back_home_sets_the_end_to_now_and_the_forecast_wants_comfort_again(presence_provider):
    import asyncio

    client = TestClient(app)
    start = datetime.now(timezone.utc) - timedelta(minutes=30)
    client.post(
        "/v1/thermal/absences",
        json={"start": start.isoformat(), "end": (start + timedelta(hours=9)).isoformat()},
    )
    before = datetime.now(timezone.utc)
    ended = client.post("/v1/thermal/absences/back").json()["ended"]
    after = datetime.now(timezone.utc)
    assert len(ended) == 1
    end = datetime.fromisoformat(ended[0]["end"])
    assert before - timedelta(seconds=1) <= end <= after + timedelta(seconds=1)
    now = datetime.now(timezone.utc)
    starts = [now + timedelta(minutes=15 * i) for i in range(1, 5)]
    estimates = asyncio.run(
        presence_provider.forecast_zone("area1", now=now, starts=starts, publish=False)
    )
    assert all(row.require_comfort for row in estimates)
    assert "reported_absence" not in {row.source for row in estimates}


def test_a_future_absence_is_the_next_one_and_a_delete_removes_it(presence_provider):
    client = TestClient(app)
    empty = client.get("/v1/thermal/absences").json()
    assert (empty["active"], empty["next"], empty["absences"]) == (None, None, [])
    start = datetime.now(timezone.utc) + timedelta(hours=100)  # beyond the planning horizon
    ident = client.post(
        "/v1/thermal/absences",
        json={"start": start.isoformat(), "end": (start + timedelta(hours=5)).isoformat()},
    ).json()["id"]
    listed = client.get("/v1/thermal/absences").json()
    assert listed["active"] is None
    assert listed["next"]["id"] == ident
    assert client.delete(f"/v1/thermal/absences/{ident}").status_code == 200
    assert client.get("/v1/thermal/absences").json()["absences"] == []
    assert client.delete(f"/v1/thermal/absences/{ident}").status_code == 404


def test_a_reported_absence_beyond_the_horizon_leaves_the_forecast_of_the_next_hours(
    presence_provider,
):
    import asyncio

    client = TestClient(app)
    now = datetime.now(timezone.utc)
    start = now + timedelta(hours=100)
    client.post(
        "/v1/thermal/absences",
        json={"start": start.isoformat(), "end": (start + timedelta(hours=5)).isoformat()},
    )
    starts = [now + timedelta(hours=i) for i in range(1, 73)]
    estimates = asyncio.run(
        presence_provider.forecast_zone("area1", now=now, starts=starts, publish=False)
    )
    assert "reported_absence" not in {row.source for row in estimates}


def test_the_room_target_of_node_red_is_accepted_as_a_temperature_sample(config_eos, tmp_path):
    config_eos.general.data_folder_path = tmp_path
    config_eos.merge_settings_from_dict(
        {"measurement": {"channels": {"room_target": {"quantity": "temperature", "unit": "degC"}}}}
    )
    client = TestClient(app)
    now = datetime.now(timezone.utc)
    body = [
        {
            "date_time": now.isoformat(),
            "key": "room_target",
            "value": 21,  # Node-RED sends the slider value as a JSON integer
            "quality": {"status": "measured"},
        }
    ]
    assert client.put("/v1/measurement/samples", json=body).json() == {"updated": 1}
    read = client.get(
        "/v1/measurement/samples",
        params={
            "key": "room_target",
            "start": (now - timedelta(minutes=1)).isoformat(),
            "end": (now + timedelta(minutes=1)).isoformat(),
        },
    ).json()
    assert [row["value"] for row in read] == [21.0]
