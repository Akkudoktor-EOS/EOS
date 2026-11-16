"""Run-time absence endpoints, persistent store, input page proxy and their merge into the plan request."""

import socket
from datetime import datetime, timedelta, timezone

import pytest
from conftest import THERMAL_START as START
from conftest import bundle_data
from fastapi.testclient import TestClient

from akkudoktoreos.server.thermalshadow import create_native_thermal_shadow_app
from akkudoktoreos.thermal.thermalabsences import AbsenceInput, AbsenceStore
from akkudoktoreos.thermal.thermalconfigrequest import (
    ThermalConfigRequest,
    ThermalDataBundle,
)

NOW = datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc)


def store(tmp_path, tz="Europe/Berlin"):
    return AbsenceStore(tmp_path / "presence_absences.json", tz)


# ---- store ---------------------------------------------------------------------------------
def test_add_persists_and_survives_a_new_store_instance(tmp_path):
    s = store(tmp_path)
    rec = s.add(AbsenceInput(end=NOW + timedelta(hours=26), label="Reise"), now=NOW)
    assert rec.start == NOW and rec.end == NOW + timedelta(hours=26)
    again = store(tmp_path)  # "restart"
    assert [r.id for r in again.entries()] == [rec.id] and again.entries()[0].label == "Reise"
    assert again.planning_absences()[0].resolve("UTC") == (NOW.timestamp(), (NOW + timedelta(hours=26)).timestamp())


def test_naive_input_is_read_in_the_installation_zone(tmp_path):
    s = store(tmp_path, "Europe/Berlin")
    rec = s.add(AbsenceInput(start=datetime(2026, 11, 15, 13, 0), end=datetime(2026, 11, 16, 14, 0)), now=NOW)
    assert rec.start == datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc)  # CET = UTC+1
    assert rec.end == datetime(2026, 11, 16, 13, 0, tzinfo=timezone.utc)


def test_replace_is_idempotent_and_keeps_one_entry(tmp_path):
    s = store(tmp_path)
    s.replace("x", AbsenceInput(end=NOW + timedelta(hours=5)), now=NOW)
    s.replace("x", AbsenceInput(end=NOW + timedelta(hours=9)), now=NOW)
    rows = s.entries()
    assert len(rows) == 1 and rows[0].end == NOW + timedelta(hours=9)


def test_back_home_ends_a_running_absence_but_keeps_it_for_learning(tmp_path):
    s = store(tmp_path)
    rec = s.add(AbsenceInput(start=NOW - timedelta(hours=3), end=NOW + timedelta(hours=20)), now=NOW)
    ended = s.back_home(now=NOW)
    assert [r.id for r in ended] == [rec.id]
    kept = s.entries()[0]
    assert kept.start == NOW - timedelta(hours=3) and kept.end == NOW  # kept, shortened
    assert s.summary(NOW)["active"] is None


def test_just_reported_entry_is_dropped_on_back_home_but_an_older_one_is_kept(tmp_path):
    s = store(tmp_path)
    fresh = s.add(AbsenceInput(end=NOW + timedelta(hours=5)), now=NOW)
    s.back_home(now=NOW + timedelta(seconds=20))
    assert fresh.id not in [r.id for r in s.entries()]
    older = s.add(AbsenceInput(start=NOW - timedelta(minutes=10), end=NOW + timedelta(hours=5)), now=NOW)
    s.back_home(now=NOW)
    assert s.entries()[0].id == older.id and s.entries()[0].end == NOW


def test_revoke_deletes_a_future_entry_and_open_ended_becomes_closed(tmp_path):
    s = store(tmp_path)
    future = s.add(AbsenceInput(start=NOW + timedelta(days=2), end=NOW + timedelta(days=3)), now=NOW)
    openend = s.add(AbsenceInput(start=NOW - timedelta(hours=1)), now=NOW)
    assert s.revoke(future.id, now=NOW) is not None and future.id not in [r.id for r in s.entries()]
    assert s.revoke(openend.id, now=NOW).end == NOW
    assert s.revoke("missing", now=NOW) is None


def test_old_entries_are_dropped_after_the_history_window(tmp_path):
    s = AbsenceStore(tmp_path / "a.json", "UTC", history_days=10)
    old = s.add(AbsenceInput(start=NOW - timedelta(days=40), end=NOW - timedelta(days=39)), now=NOW - timedelta(days=39))
    s.add(AbsenceInput(end=NOW + timedelta(hours=1)), now=NOW)
    assert old.id not in [r.id for r in s.entries()]


def test_damaged_file_is_moved_aside_not_overwritten_and_planning_continues(tmp_path):
    path = tmp_path / "presence_absences.json"
    path.write_text("{not json", encoding="utf-8")
    s = AbsenceStore(path, "UTC")
    assert s.entries() == []
    assert list(tmp_path.glob("presence_absences.json.damaged-*"))


def test_summary_reports_active_and_next_in_local_time(tmp_path):
    s = store(tmp_path)
    s.add(AbsenceInput(start=NOW - timedelta(hours=1), end=NOW + timedelta(hours=3)), now=NOW)
    s.add(AbsenceInput(start=NOW + timedelta(days=1), end=NOW + timedelta(days=2)), now=NOW)
    summary = s.summary(NOW)
    assert summary["active"]["end"].endswith("+01:00") and summary["next"]["start"].endswith("+01:00")
    assert len(summary["absences"]) == 2 and summary["timezone"] == "Europe/Berlin"


def test_invalid_period_is_rejected(tmp_path):
    with pytest.raises(ValueError):
        store(tmp_path).add(AbsenceInput(start=NOW, end=NOW - timedelta(hours=1)), now=NOW)


# ---- REST ------------------------------------------------------------------------------------
class FakeContext:
    absence_store = None


@pytest.fixture
def client(tmp_path):
    app = create_native_thermal_shadow_app(FakeContext(), absence_store=store(tmp_path))
    return TestClient(app)


def test_rest_roundtrip_post_get_back_delete(client):
    end = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
    created = client.post("/v1/thermal/absences", json={"end": end, "label": "Test"})
    assert created.status_code == 200 and created.json()["active"]["label"] == "Test"
    ident = created.json()["created"]
    assert client.get("/v1/thermal/absences").json()["active"]["id"] == ident
    assert client.put(f"/v1/thermal/absences/{ident}", json={"end": end, "label": "Neu"}).json()["active"]["label"] == "Neu"
    back = client.post("/v1/thermal/absences/back").json()
    assert back["ended"] == [ident] and back["active"] is None
    assert client.delete("/v1/thermal/absences/unknown").status_code == 404
    assert client.delete(f"/v1/thermal/absences/{ident}").status_code == 404  # just reported and ended: nothing kept


def test_rest_validates_input(client):
    now = datetime.now(timezone.utc)
    bad = client.post("/v1/thermal/absences", json={"start": now.isoformat(), "end": (now - timedelta(hours=1)).isoformat()})
    assert bad.status_code == 422
    assert client.post("/v1/thermal/absences", json={"ende": "x"}).status_code == 422


def test_rest_is_unavailable_without_a_store():
    app = create_native_thermal_shadow_app(FakeContext())
    assert TestClient(app).get("/v1/thermal/absences").status_code == 503


# ---- merge into the plan request ---------------------------------------------------------------
def _configure(context):
    context.config.merge_settings_from_dict(
        {
            "presence": {"provider": "PresenceHistory", "zones": {"presence1": {"measurement_key": "present"}}},
            "measurement": {"channels": {"present": {"quantity": "presence", "unit": "1"}}},
            "thermal": {"planning_time_limit_seconds": 7.5, "zones": {"area1": {"presence_area_id": "presence1",
                "unoccupied_comfort": {"min_temperature_c": 16.0, "target_temperature_c": 17.0, "max_temperature_c": 18.0}}}},
        }
    )


async def _resolve(context):
    await context.ingest(ThermalDataBundle.model_validate(bundle_data()))
    return await context._resolve(ThermalConfigRequest(zone_id="area1", heat_source_id="source1", basis_control_revision=7))


@pytest.mark.asyncio
async def test_stored_absence_changes_the_next_plan_request_without_restart(context, tmp_path):
    _configure(context)
    context.absence_store = store(tmp_path, "UTC")
    request, diagnostics = await _resolve(context)
    assert all(row["source"] != "reported_absence" for row in diagnostics["presence"])
    context.absence_store.add(AbsenceInput(start=START - timedelta(hours=1), end=START + timedelta(hours=1)), now=START)
    request, diagnostics = await _resolve(context)
    assert [row["source"] for row in diagnostics["presence"]][:4] == ["reported_absence"] * 4
    assert request.comfort_bands[0].min_temperature_c == 16.0
    context.absence_store.back_home(now=START)  # back home: next plan is back to normal
    _, diagnostics = await _resolve(context)
    assert all(row["source"] != "reported_absence" for row in diagnostics["presence"])


@pytest.mark.asyncio
async def test_without_a_store_the_request_is_unchanged(context):
    _configure(context)
    assert context.absence_store is None
    request, diagnostics = await _resolve(context)
    assert all(row["source"] != "reported_absence" for row in diagnostics["presence"])


# ---- the one-line input page (standard library service) ---------------------------------------
def _free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]
