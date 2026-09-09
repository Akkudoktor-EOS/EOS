"""Create an importable Node-RED flow for the split control/tail horizon."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


PREDICTION_FUNCTION = r'''const BASE_URL = "http://192.168.1.151:8503";
const PREDICTION_HOURS = 72;
const SLOT_MINUTES = 15;
const PREDICTION_KEY = "{key}";

const now = new Date();
const start = new Date(now);
start.setHours(0, 0, 0, 0);

// EOS schneidet den bereits vergangenen Teil des Tages selbst ab. Daher wird
// ab Mitternacht bis exakt 72 Stunden nach dem laufenden Viertelstunden-Slot
// abgefragt.
const slotStart = new Date(now);
slotStart.setSeconds(0, 0);
slotStart.setMinutes(Math.floor(slotStart.getMinutes() / SLOT_MINUTES) * SLOT_MINUTES);
const end = new Date(slotStart.getTime() + PREDICTION_HOURS * 3600000);

function toLocalIsoWithOffset(date) {{
    const pad = n => String(Math.trunc(Math.abs(n))).padStart(2, "0");
    const offsetMin = -date.getTimezoneOffset();
    const sign = offsetMin >= 0 ? "+" : "-";
    return `${{date.getFullYear()}}-${{pad(date.getMonth() + 1)}}-${{pad(date.getDate())}}` +
        `T${{pad(date.getHours())}}:${{pad(date.getMinutes())}}:${{pad(date.getSeconds())}}` +
        `${{sign}}${{pad(offsetMin / 60)}}:${{pad(offsetMin % 60)}}`;
}}

msg.method = "GET";
msg.url = `${{BASE_URL}}/v1/prediction/list` +
    `?key=${{encodeURIComponent(PREDICTION_KEY)}}` +
    `&start_datetime=${{encodeURIComponent(toLocalIsoWithOffset(start))}}` +
    `&end_datetime=${{encodeURIComponent(toLocalIsoWithOffset(end))}}` +
    `&interval=${{encodeURIComponent("15 minutes")}}`;
return msg;
'''


PV_SERIES_FUNCTION = r'''const BASE_URL = "http://192.168.1.151:8503";
const PREDICTION_HOURS = 72;
const SLOT_MINUTES = 15;
const now = new Date();
const start = new Date(now);
start.setHours(0, 0, 0, 0);
const slotStart = new Date(now);
slotStart.setSeconds(0, 0);
slotStart.setMinutes(Math.floor(slotStart.getMinutes() / SLOT_MINUTES) * SLOT_MINUTES);
const end = new Date(slotStart.getTime() + PREDICTION_HOURS * 3600000);

function iso(date) {
    const pad = n => String(Math.trunc(Math.abs(n))).padStart(2, "0");
    const offset = -date.getTimezoneOffset();
    const sign = offset >= 0 ? "+" : "-";
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}` +
        `T${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}` +
        `${sign}${pad(offset / 60)}:${pad(offset % 60)}`;
}

// /list interpolates über das echte Forecast-Ende hinaus. /series liefert nur
// die tatsächlich vom Provider vorhandenen Zeitpunkte.
msg.method = "GET";
msg.url = `${BASE_URL}/v1/prediction/series?key=pvforecast_ac_power` +
    `&start_datetime=${encodeURIComponent(iso(start))}` +
    `&end_datetime=${encodeURIComponent(iso(end))}`;
return msg;
'''


PV_SERIES_TO_SLOTS_FUNCTION = r'''const SLOT_MINUTES = 15;
const PREDICTION_HOURS = 72;
const SLOT_MS = SLOT_MINUTES * 60000;
const raw = msg.payload && msg.payload.data;
if (!raw || typeof raw !== "object") {
    node.warn("PV-Rohprognose fehlt oder hat kein data-Objekt.");
    return null;
}

const points = Object.entries(raw)
    .map(([timestamp, value]) => [new Date(timestamp).getTime(), Number(value)])
    .filter(([timestamp, value]) => Number.isFinite(timestamp) && Number.isFinite(value))
    .sort((a, b) => a[0] - b[0]);
if (points.length < 2) {
    node.warn("PV-Rohprognose enthält weniger als zwei gültige Punkte.");
    return null;
}

const now = new Date();
const start = new Date(now);
start.setHours(0, 0, 0, 0);
const slotStart = new Date(now);
slotStart.setSeconds(0, 0);
slotStart.setMinutes(Math.floor(slotStart.getMinutes() / SLOT_MINUTES) * SLOT_MINUTES);
const endMs = slotStart.getTime() + PREDICTION_HOURS * 3600000;
const values = [];
let right = 1;
for (let timestamp = start.getTime(); timestamp < endMs; timestamp += SLOT_MS) {
    if (timestamp < points[0][0]) {
        values.push(0);
        continue;
    }
    if (timestamp > points[points.length - 1][0]) {
        // Absichtlich null: Function 3 und EOS verkürzen damit den Tail, statt
        // einen erfundenen linearen PV-Rest als echten Forecast zu behandeln.
        values.push(null);
        continue;
    }
    while (right < points.length && points[right][0] < timestamp) right++;
    const b = points[Math.min(right, points.length - 1)];
    const a = points[Math.max(right - 1, 0)];
    if (timestamp === b[0] || a[0] === b[0]) {
        values.push(b[1]);
    } else {
        const fraction = (timestamp - a[0]) / (b[0] - a[0]);
        values.push(a[1] + fraction * (b[1] - a[1]));
    }
}

msg.topic = "pv_forecast";
msg.payload = values;
node.status({
    fill: points[points.length - 1][0] >= endMs - SLOT_MS ? "green" : "yellow",
    shape: "dot",
    text: `PV echt bis ${new Date(points[points.length - 1][0]).toLocaleString()}`
});
return msg;
'''


LOAD_RESULT_FUNCTION = r'''// /gesamtlast aktualisiert den angepassten Last-Forecast. Die alte Endpoint-
// Antwort ist auf 48 Stunden begrenzt; anschließend lesen wir deshalb den
// aktualisierten 72-Stunden-Forecast über /v1/prediction/list.
const BASE_URL = "http://192.168.1.151:8503";
const PREDICTION_HOURS = 72;
const SLOT_MINUTES = 15;
const now = new Date();
const start = new Date(now);
start.setHours(0, 0, 0, 0);
const slotStart = new Date(now);
slotStart.setSeconds(0, 0);
slotStart.setMinutes(Math.floor(slotStart.getMinutes() / SLOT_MINUTES) * SLOT_MINUTES);
const end = new Date(slotStart.getTime() + PREDICTION_HOURS * 3600000);

function iso(date) {
    const pad = n => String(Math.trunc(Math.abs(n))).padStart(2, "0");
    const offset = -date.getTimezoneOffset();
    const sign = offset >= 0 ? "+" : "-";
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}` +
        `T${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}` +
        `${sign}${pad(offset / 60)}:${pad(offset % 60)}`;
}

msg.method = "GET";
msg.url = `${BASE_URL}/v1/prediction/list?key=loadforecast_power_w` +
    `&start_datetime=${encodeURIComponent(iso(start))}` +
    `&end_datetime=${encodeURIComponent(iso(end))}` +
    `&interval=${encodeURIComponent("15 minutes")}`;
delete msg.payload;
return msg;
'''


TERMINAL_VALUE_FUNCTION = r'''// Tail und anschließenden Terminalwert der Optimierung -> MariaDB
const tv = msg.payload && msg.payload.terminal_value;
if (!tv) {
    node.warn("Kein terminal_value im Payload - läuft EOS noch auf dem alten Stand?");
    return null;
}

const curve = tv.curve;
const continuation = tv.continuation_curve;
const diag = tv.tail_diagnostics || {};
const d = new Date();
const p2 = v => ("0" + v).slice(-2);
const runTs = `${d.getFullYear()}-${p2(d.getMonth() + 1)}-${p2(d.getDate())} ` +
    `${p2(d.getHours())}:${p2(d.getMinutes())}:${p2(d.getSeconds())}`;
const num = v => (v === null || v === undefined || isNaN(v)) ? "NULL" : Number(v).toFixed(8);
const str = v => "'" + String(v === null || v === undefined ? "" : v)
    .replace(/'/g, "''").slice(0, 250) + "'";

function marginalAt(c, energyWh) {
    if (!c || !Array.isArray(c.energy_wh) || c.energy_wh.length < 2) return null;
    for (let i = 1; i < c.energy_wh.length; i++) {
        if (energyWh <= c.energy_wh[i]) return c.marginal_euro_per_kwh[i - 1];
    }
    return c.marginal_euro_per_kwh[c.marginal_euro_per_kwh.length - 1] ?? null;
}

const res = msg.payload.result || {};
const priceNow = Array.isArray(res.Electricity_price) && res.Electricity_price.length
    ? res.Electricity_price[0] * 100000 : null;
const feedInNow = Array.isArray(res.Feed_in_tariff) && res.Feed_in_tariff.length
    ? res.Feed_in_tariff[0] * 100000 : null;
const marginalNow = marginalAt(curve, tv.battery_energy_wh);
const continuationMarginalNow = marginalAt(continuation, tv.battery_energy_wh);

const scalars = {
    credited_euro: tv.credited_euro,
    tail_operating_euro: tv.tail_operating_euro,
    continuation_value_euro: tv.continuation_value_euro,
    battery_energy_wh: tv.battery_energy_wh,
    control_horizon_hours: tv.control_horizon_hours,
    requested_tail_hours: tv.requested_tail_hours,
    effective_tail_hours: tv.effective_tail_hours,
    tail_end_hour: tv.tail_end_hour,
    marginal_now_ct_kwh: marginalNow === null ? null : marginalNow * 100,
    continuation_marginal_now_ct_kwh:
        continuationMarginalNow === null ? null : continuationMarginalNow * 100,
    price_now_ct_kwh: priceNow,
    feed_in_now_ct_kwh: feedInNow,
    tail_slots: diag.slots,
    tail_slot_hours: diag.slot_hours,
    tail_soc_grid_points: diag.soc_grid_points,
    tail_min_import_ct_kwh: diag.min_import_price_euro_per_kwh * 100,
    tail_max_import_ct_kwh: diag.max_import_price_euro_per_kwh * 100,
    tail_min_feed_in_ct_kwh: diag.min_feed_in_tariff_euro_per_kwh * 100,
    tail_max_feed_in_ct_kwh: diag.max_feed_in_tariff_euro_per_kwh * 100,
    tail_negative_price_slots: diag.negative_import_price_slots,
    tail_positive_export_slots: diag.positive_battery_export_slots,
    mode_tail: tv.mode === "TAIL" ? 1 : 0,
    mode_auto: (tv.mode === "TAIL" || tv.mode === "AUTO") ? 1 : 0
};

let sql = "START TRANSACTION;\n";
sql += `DELETE FROM eos_terminal_value WHERE run_ts = '${runTs}';\n`;
for (const topic in scalars) {
    const info = topic === "mode_tail" || topic === "mode_auto"
        ? str(`${tv.mode}; continuation=${tv.continuation_mode}; ${tv.reason || "vollständiger Forecast"}`)
        : "NULL";
    sql += `INSERT INTO eos_terminal_value (run_ts, topic, data, info) VALUES ` +
        `('${runTs}', '${topic}', ${num(scalars[topic])}, ${info});\n`;
}

if (curve && Array.isArray(curve.energy_wh) && curve.energy_wh.length) {
    sql += `DELETE FROM eos_terminal_value_curve WHERE run_ts = '${runTs}';\n`;
    const rows = curve.energy_wh.map((wh, i) => {
        const marginal = i < curve.marginal_euro_per_kwh.length
            ? curve.marginal_euro_per_kwh[i] * 100 : null;
        const operating = Array.isArray(curve.operating_value_euro)
            ? curve.operating_value_euro[i] : null;
        const continuationValue = Array.isArray(curve.continuation_value_euro)
            ? curve.continuation_value_euro[i] : null;
        return `('${runTs}', ${i}, ${num(wh)}, ${num(curve.value_euro[i])}, ` +
            `${num(marginal)}, ${num(operating)}, ${num(continuationValue)})`;
    });
    sql += "INSERT INTO eos_terminal_value_curve " +
        "(run_ts, point_idx, energy_wh, value_euro, marginal_ct_kwh, " +
        "tail_operating_euro, continuation_value_euro) VALUES\n" + rows.join(",\n") + ";\n";
}

const tailPlan = Array.isArray(tv.tail_plan) ? tv.tail_plan : [];
if (tailPlan.length) {
    const slotHours = Number(diag.slot_hours) || 0.25;
    const slotMs = slotHours * 3600000;
    const planStart = new Date(Math.floor(d.getTime() / slotMs) * slotMs);
    sql += `DELETE FROM eos_tail_plan WHERE run_ts = '${runTs}';\n`;
    const rows = tailPlan.map((slot, i) => {
        const ts = new Date(planStart.getTime() + Number(slot.hour_from_start) * 3600000);
        const tsSql = `${ts.getFullYear()}-${p2(ts.getMonth() + 1)}-${p2(ts.getDate())} ` +
            `${p2(ts.getHours())}:${p2(ts.getMinutes())}:${p2(ts.getSeconds())}`;
        return `('${runTs}', '${tsSql}', ${i}, ${str(slot.action)}, ` +
            `${str(slot.alternative_action)}, ${num(slot.decision_margin_euro)}, ` +
            `${num(slot.soc_start_percentage)}, ${num(slot.soc_end_percentage)}, ` +
            `${num(slot.pv_wh)}, ${num(slot.load_wh)}, ${num(slot.grid_import_wh)}, ` +
            `${num(slot.grid_export_wh)}, ${num(slot.battery_charge_wh)}, ` +
            `${num(slot.battery_discharge_wh)}, ${num(slot.import_price_euro_per_kwh)}, ` +
            `${num(slot.feed_in_tariff_euro_per_kwh)}, ${num(slot.slot_value_euro)}, ` +
            `${num(slot.remaining_value_euro)}, ${num(slot.ac_charge_factor)}, ` +
            `${num(slot.dc_charge_allowed)}, ${num(slot.discharge_allowed)}, ` +
            `${num(slot.battery_grid_export_factor)})`;
    });
    sql += "INSERT INTO eos_tail_plan " +
        "(run_ts, timestamp, slot, action, alternative_action, decision_margin_euro, " +
        "soc_start_pct, soc_end_pct, pv_wh, load_wh, grid_import_wh, grid_export_wh, " +
        "battery_charge_wh, battery_discharge_wh, import_price_euro_kwh, " +
        "feed_in_tariff_euro_kwh, slot_value_euro, remaining_value_euro, " +
        "ac_charge_factor, dc_charge_allowed, discharge_allowed, " +
        "battery_grid_export_factor) VALUES\n" + rows.join(",\n") + ";\n";
}

sql += "DELETE FROM eos_terminal_value_curve WHERE run_ts < NOW() - INTERVAL 14 DAY;\n";
sql += "DELETE FROM eos_terminal_value WHERE run_ts < NOW() - INTERVAL 90 DAY;\n";
sql += "DELETE FROM eos_tail_plan WHERE run_ts < NOW() - INTERVAL 14 DAY;\n";
sql += "COMMIT;";
msg.topic = msg.payload = sql;
return msg;
'''


HORIZON_STATUS_FUNCTION = r'''const tv = msg.payload && msg.payload.terminal_value;
if (!tv) {
    msg.payload = "Keine Horizont-Diagnose in der EOS-Antwort";
    node.status({fill: "red", shape: "ring", text: msg.payload});
    return msg;
}
const diag = tv.tail_diagnostics || {};
const status = {
    genetische_steuerung: `${tv.control_horizon_hours} h`,
    tail_optimierung: `${tv.effective_tail_hours} / ${tv.requested_tail_hours} h`,
    tail_verfahren: "deterministische Batterie-DP (101 SoC-Stützstellen)",
    terminalwert_nach_tail: tv.continuation_mode,
    tail_ergebnis_euro: tv.tail_operating_euro,
    terminalwert_euro: tv.continuation_value_euro,
    gesamtgutschrift_euro: tv.credited_euro,
    batterie_am_steuerende_wh: tv.battery_energy_wh,
    tail_slots: diag.slots,
    negative_preis_slots_im_tail: diag.negative_import_price_slots,
    begruendung: tv.reason || "vollständiger Forecast"
};
msg.payload = status;
node.status({
    fill: tv.effective_tail_hours === tv.requested_tail_hours ? "green" : "yellow",
    shape: "dot",
    text: `GA ${tv.control_horizon_hours}h | Tail ${tv.effective_tail_hours}/${tv.requested_tail_hours}h | TV ${tv.continuation_mode}`
});
return msg;
'''


def node_by_id(nodes: list[dict], node_id: str) -> dict:
    return next(node for node in nodes if node.get("id") == node_id)


def replace_active_function_3(source: str) -> str:
    source = source.split("\n/*const SPH", 1)[0].rstrip()
    source = source.replace(
        "const SPH = 4;       // 4 bei 900 Sekunden\nconst N = 48 * SPH;  // 192 Slots",
        "const INTERVAL_SECONDS = 900;\n"
        "const SPH = 3600 / INTERVAL_SECONDS;\n"
        "const CONTROL_HOURS = 24;\n"
        "const TAIL_HOURS = 48;\n"
        "const PREDICTION_HOURS = CONTROL_HOURS + TAIL_HOURS;\n"
        "const CONTROL_SLOTS = CONTROL_HOURS * SPH;\n"
        "const PREDICTION_SLOTS = PREDICTION_HOURS * SPH;",
    )
    old = re.search(
        r"function toSlots\(values, isEnergy\) \{.*?\n\}\n\nfunction clampPercentage",
        source,
        flags=re.DOTALL,
    )
    if old is None:
        raise RuntimeError("toSlots block in function 3 was not found")
    new = r'''function toSlots(values, isEnergy, name) {
    if (!Array.isArray(values)) return null;

    const now = new Date();
    const midnight = new Date(now);
    midnight.setHours(0, 0, 0, 0);
    const slotStart = new Date(now);
    slotStart.setSeconds(0, 0);
    slotStart.setMinutes(Math.floor(slotStart.getMinutes() / 15) * 15);
    const elapsedSlots = Math.floor((slotStart.getTime() - midnight.getTime()) / 900000);
    const minimumLength = elapsedSlots + CONTROL_SLOTS;
    const requestedLength = elapsedSlots + PREDICTION_SLOTS;

    let validLength = values.length;
    const clean = values.map((value, index) => {
        if (value === null || value === undefined || value === "") {
            // Dieser Bereich wird von EOS ohnehin abgeschnitten. Fehlende alte
            // Tageswerte dürfen deshalb den rollenden Forecast nicht blockieren.
            if (index < elapsedSlots) return 0;
            validLength = Math.min(validLength, index);
            return NaN;
        }
        const parsed = Number(value);
        if (!Number.isFinite(parsed)) {
            if (index < elapsedSlots) return 0;
            validLength = Math.min(validLength, index);
        }
        return parsed;
    });

    if (validLength < minimumLength) {
        node.warn(`${name}: nur ${validLength} gültige Viertelstundenwerte ab Mitternacht; ` +
            `mindestens ${minimumLength} für den 24-h-Steuerhorizont erforderlich.`);
        return null;
    }
    if (validLength < requestedLength) {
        node.warn(`${name}: Tail verkürzt; ${validLength - elapsedSlots - CONTROL_SLOTS} von ` +
            `${TAIL_HOURS * SPH} Tail-Slots verfügbar.`);
    }

    const slots = clean.slice(0, Math.min(validLength, requestedLength));
    // Die v1-Endpoints liefern Leistung in W. Für den 15-min-EMS-Slot wird
    // daraus bei PV und Last Energie in Wh; Preise bleiben EUR/Wh.
    return isEnergy ? slots.map(value => value / SPH) : slots;
}

function clampPercentage'''
    source = source[: old.start()] + new + source[old.end() :]
    source = source.replace(
        "const pv = toSlots(context.data.pv_forecast, true);\n"
        "const preis = toSlots(context.data.strompreis, false);\n"
        "const einsp = toSlots(context.data.feed_in_tariff_wh, false);\n"
        "const last = toSlots(context.data.gesamtlast, true);",
        "const pv = toSlots(context.data.pv_forecast, true, \"PV-Prognose\");\n"
        "const preis = toSlots(context.data.strompreis, false, \"Strompreis\");\n"
        "const einsp = toSlots(context.data.feed_in_tariff_wh, false, \"Einspeisevergütung\");\n"
        "const last = toSlots(context.data.gesamtlast, true, \"Gesamtlast\");",
    )
    source = source.replace("N * (optimizeEv ? 2 : 1)", "CONTROL_SLOTS * (optimizeEv ? 2 : 1)")
    source = source.replace(
        "msg.payload = {\n    ems:",
        "msg.payload = {\n    forecast_interval_seconds: INTERVAL_SECONDS,\n\n    ems:",
    )
    source = source.replace(
        "`Warm-Start=${startSolution !== null}`",
        "`Horizonte=${CONTROL_HOURS}h GA + ${TAIL_HOURS}h Tail, ` +\n"
        "    `Forecast-Slots(PV/Preis/Tarif/Last)=${pv.length}/${preis.length}/${einsp.length}/${last.length}, ` +\n"
        "    `Warm-Start=${startSolution !== null}`",
    )
    return source + "\n"


def update_control_index(source: str) -> str:
    source = source.replace(
        "let indexForCurrentHour = values_eauto.length > 48\n"
        "    ? currentDate.getHours() * 4 + Math.floor(currentDate.getMinutes() / 15)\n"
        "    : currentHour;",
        "let indexForCurrentHour = msg.payload.controls_start_at_now === true\n"
        "    ? 0\n"
        "    : (values_eauto.length > 48\n"
        "        ? currentDate.getHours() * 4 + Math.floor(currentDate.getMinutes() / 15)\n"
        "        : currentHour);",
    )
    source = source.replace(
        "let i = dis.length > 48 ? d.getHours() * 4 + Math.floor(d.getMinutes() / 15) : d.getHours();",
        "let i = msg.payload.controls_start_at_now === true\n"
        "    ? 0\n"
        "    : (dis.length > 48 ? d.getHours() * 4 + Math.floor(d.getMinutes() / 15) : d.getHours());",
    )
    source = source.replace(
        "var idx = values.length > 48\n"
        "    ? now.getHours() * 4 + Math.floor(now.getMinutes() / 15)\n"
        "    : now.getHours();",
        "var idx = msg.payload && msg.payload.controls_start_at_now === true\n"
        "    ? 0\n"
        "    : (values.length > 48\n"
        "        ? now.getHours() * 4 + Math.floor(now.getMinutes() / 15)\n"
        "        : now.getHours());",
    )
    return source


FUNCTION_4 = r'''const values = msg.payload.discharge_allowed || [];
const valuesAc = msg.payload.ac_charge || [];
const valuesDc = msg.payload.dc_charge || [];
const valuesExport = msg.payload.battery_grid_export_allowed || [];
const valuesEv = msg.payload.eautocharge_hours_float || [];
const sph = values.length > 48 ? 4 : 1;
const slotMs = 3600000 / sph;
const now = new Date();
const legacyMidnight = new Date(now);
legacyMidnight.setHours(0, 0, 0, 0);
const currentSlot = now.getHours() * sph + Math.floor(now.getMinutes() / (60 / sph));
const runRelative = msg.payload.controls_start_at_now === true;
const planStart = runRelative
    ? new Date(Math.floor(now.getTime() / slotMs) * slotMs)
    : legacyMidnight;
const firstIndex = runRelative ? 0 : currentSlot;
const applianceStarts = (msg.payload.appliance_starts || {}).spuelmaschine || [];
const applianceStartMs = new Set(applianceStarts.map(value => new Date(value).getTime()));

function sqlValue(array, index) {
    return Array.isArray(array) && index < array.length && Number.isFinite(Number(array[index]))
        ? Number(array[index]) : null;
}
function timestamp(date) {
    const p2 = value => ("0" + value).slice(-2);
    return `${date.getFullYear()}-${p2(date.getMonth() + 1)}-${p2(date.getDate())} ` +
        `${p2(date.getHours())}:${p2(date.getMinutes())}:00`;
}

let sql = "START TRANSACTION;\n";
// Alte 48-h-Pläne oder frühere Läufe dürfen hinter dem neuen 24-h-Plan keine
// scheinbaren SoC-Sprünge und keine veralteten Schaltwerte hinterlassen.
sql += `DELETE FROM eos WHERE timestamp >= '${timestamp(planStart)}';\n`;
values.forEach((value, index) => {
    if (index < firstIndex) return;
    const ts = new Date(planStart.getTime() + index * slotMs);
    const tsString = timestamp(ts);
    sql += `DELETE FROM eos WHERE timestamp = '${tsString}';\n`;
    sql += `INSERT INTO eos (timestamp, topic, data) VALUES ('${tsString}','discharge_allowed', ${Number(value)});\n`;
    sql += `INSERT INTO eos (timestamp, topic, data) VALUES ('${tsString}','ac_charge', ${sqlValue(valuesAc, index) ?? 0});\n`;
    sql += `INSERT INTO eos (timestamp, topic, data) VALUES ('${tsString}','dc_charge', ${sqlValue(valuesDc, index) ?? 0});\n`;
    const exportValue = sqlValue(valuesExport, index);
    const evValue = sqlValue(valuesEv, index);
    if (exportValue !== null) sql += `INSERT INTO eos (timestamp, topic, data) VALUES ('${tsString}','battery_grid_export_allowed', ${exportValue});\n`;
    if (evValue !== null) sql += `INSERT INTO eos (timestamp, topic, data) VALUES ('${tsString}','eautocharge_hours_float', ${evValue});\n`;
    const startsHere = applianceStartMs.has(ts.getTime()) ? 1 : 0;
    sql += `INSERT INTO eos (timestamp, topic, data) VALUES ('${tsString}','spuelstart_hours_bin', ${startsHere});\n`;
});
sql += "COMMIT;";
msg.topic = msg.payload = sql;
return msg;
'''


def update_simulation_timebase(source: str) -> str:
    pattern = re.compile(
        r"// Startzeitpunkt ab der jetzigen Stunde.*?let currentHour = now\.getHours\(\); // Offset für die 48h-Top-Level-Arrays",
        re.DOTALL,
    )
    replacement = r'''// Neue Antworten beginnen mit dem laufenden Slot (controls_start_at_now=true).
// Der Legacy-Zweig bleibt für ältere EOS-Antworten erhalten.
let sph = (msg.payload.discharge_allowed || []).length > 48 ? 4 : 1;
let stepMs = 3600000 / sph;
let now = new Date();
if (msg.payload.controls_start_at_now === true) {
    now = new Date(Math.floor(now.getTime() / stepMs) * stepMs);
} else {
    let startSlot = msg.payload.discharge_allowed.length - data.Last_Wh_pro_Stunde.length - 1;
    now.setHours(0, 0, 0, 0);
    now = new Date(now.getTime() + startSlot * stepMs);
}
let currentHour = now.getHours();'''
    updated, count = pattern.subn(replacement, source, count=1)
    if count != 1:
        raise RuntimeError("simulation_data timebase block was not found")
    updated = updated.replace(
        'let sqlStatements = "";',
        '''const p2Start = value => ("0" + value).slice(-2);
const planStartSql = `${now.getFullYear()}-${p2Start(now.getMonth() + 1)}-${p2Start(now.getDate())} ` +
    `${p2Start(now.getHours())}:${p2Start(now.getMinutes())}:00`;
let sqlStatements = "START TRANSACTION;\\n" +
    `DELETE FROM eos_simulation_data WHERE timestamp >= '${planStartSql}';\\n`;''',
        1,
    )
    updated = updated.replace(
        "// Das generierte SQL-Statement in msg.topic einfügen\nmsg.topic = sqlStatements;",
        '// Das generierte SQL-Statement in msg.topic einfügen\nsqlStatements += "COMMIT;\\n";\nmsg.topic = sqlStatements;',
        1,
    )
    return updated


SCHEMA_INFO = r'''Einmalig auf MariaDB in der Datenbank `sensor` ausführen:

CREATE TABLE IF NOT EXISTS eos_terminal_value (
  run_ts DATETIME NOT NULL,
  topic VARCHAR(64) NOT NULL,
  data DOUBLE NULL,
  info VARCHAR(255) NULL,
  PRIMARY KEY (run_ts, topic)
);

CREATE TABLE IF NOT EXISTS eos_terminal_value_curve (
  run_ts DATETIME NOT NULL,
  point_idx SMALLINT NOT NULL,
  energy_wh DOUBLE NOT NULL,
  value_euro DOUBLE NOT NULL,
  marginal_ct_kwh DOUBLE NULL,
  tail_operating_euro DOUBLE NULL,
  continuation_value_euro DOUBLE NULL,
  PRIMARY KEY (run_ts, point_idx),
  KEY (run_ts)
);

CREATE TABLE IF NOT EXISTS eos_tail_plan (
  run_ts DATETIME NOT NULL,
  timestamp DATETIME NOT NULL,
  slot SMALLINT NOT NULL,
  action VARCHAR(32) NOT NULL,
  alternative_action VARCHAR(32) NULL,
  decision_margin_euro DOUBLE NULL,
  soc_start_pct DOUBLE NULL,
  soc_end_pct DOUBLE NULL,
  pv_wh DOUBLE NULL,
  load_wh DOUBLE NULL,
  grid_import_wh DOUBLE NULL,
  grid_export_wh DOUBLE NULL,
  battery_charge_wh DOUBLE NULL,
  battery_discharge_wh DOUBLE NULL,
  import_price_euro_kwh DOUBLE NULL,
  feed_in_tariff_euro_kwh DOUBLE NULL,
  slot_value_euro DOUBLE NULL,
  remaining_value_euro DOUBLE NULL,
  ac_charge_factor DOUBLE NULL,
  dc_charge_allowed TINYINT NULL,
  discharge_allowed TINYINT NULL,
  battery_grid_export_factor DOUBLE NULL,
  PRIMARY KEY (run_ts, slot),
  KEY ix_eos_tail_plan_timestamp (timestamp),
  KEY ix_eos_tail_plan_run (run_ts)
);

Bei bereits vorhandener Tabelle einmalig ergänzen:
ALTER TABLE eos_terminal_value_curve
  ADD COLUMN IF NOT EXISTS tail_operating_euro DOUBLE NULL,
  ADD COLUMN IF NOT EXISTS continuation_value_euro DOUBLE NULL;
'''


GRAFANA_INFO = r'''Grafana-Ausgaben für die getrennten Bereiche:

1) Tail und Terminalwert pro Optimierungslauf (Time series)
SELECT run_ts AS time, data AS value, topic AS metric
FROM eos_terminal_value
WHERE $__timeFilter(run_ts)
  AND topic IN ('tail_operating_euro','continuation_value_euro','credited_euro')
ORDER BY run_ts;

2) Effektiver Tail gegen Soll-Tail (Time series)
SELECT run_ts AS time, data AS value, topic AS metric
FROM eos_terminal_value
WHERE $__timeFilter(run_ts)
  AND topic IN ('requested_tail_hours','effective_tail_hours')
ORDER BY run_ts;

3) Wertkurve des letzten Laufs, getrennt nach Komponenten (XY/Trend, X=energy_wh)
SELECT energy_wh, tail_operating_euro, continuation_value_euro, value_euro
FROM eos_terminal_value_curve
WHERE run_ts = (SELECT MAX(run_ts) FROM eos_terminal_value_curve)
ORDER BY point_idx;

Dabei gilt: value_euro = tail_operating_euro + continuation_value_euro.

4) Tail-Preisspanne und negative Preise (Time series)
SELECT run_ts AS time, data AS value, topic AS metric
FROM eos_terminal_value
WHERE $__timeFilter(run_ts)
  AND topic IN ('tail_min_import_ct_kwh','tail_max_import_ct_kwh',
                'tail_negative_price_slots','tail_positive_export_slots')
ORDER BY run_ts;

5) Grenzwert gegen aktuellen Preis (Time series)
SELECT run_ts AS time, data AS value, topic AS metric
FROM eos_terminal_value
WHERE $__timeFilter(run_ts)
  AND topic IN ('marginal_now_ct_kwh','continuation_marginal_now_ct_kwh',
                'price_now_ct_kwh','feed_in_now_ct_kwh')
ORDER BY run_ts;
'''


def update_flow(source_path: Path, output_path: Path) -> None:
    nodes = json.loads(source_path.read_text(encoding="utf-8-sig"))

    prediction_nodes = {
        "9ea11560f93ca817": "elecprice_marketprice_wh",
        "670b95cb8913ac03": "pvforecast_ac_power",
        "62eba1e8e3fdf8a8": "feed_in_tariff_wh",
    }
    for node_id, key in prediction_nodes.items():
        node_by_id(nodes, node_id)["func"] = PREDICTION_FUNCTION.format(key=key)
    node_by_id(nodes, "670b95cb8913ac03")["func"] = PV_SERIES_FUNCTION
    node_by_id(nodes, "9ea11560f93ca817")["name"] = "Prediction URL: Strompreis 72h"
    node_by_id(nodes, "670b95cb8913ac03")["name"] = "Prediction URL: PV 72h"
    node_by_id(nodes, "62eba1e8e3fdf8a8")["name"] = "Prediction URL: Einspeisetarif 72h"

    function3 = node_by_id(nodes, "a10f03b13dc84c70")
    function3["name"] = "EOS Request: 24h Steuerung + 48h Tail"
    function3["func"] = replace_active_function_3(function3["func"])

    # Preserve the adjusted-load update, then read the full native forecast.
    load_builder = node_by_id(nodes, "79da21908d49f3a4")
    load_builder["name"] = "Load forecast URL 72h"
    load_builder["func"] = LOAD_RESULT_FUNCTION
    load_builder["wires"] = [["e17d6c2f7a2db604"]]
    nodes.extend(
        [
            {
                "id": "e17d6c2f7a2db604",
                "type": "http request",
                "z": load_builder["z"],
                "name": "Load prediction read 72h",
                "method": "GET",
                "ret": "obj",
                "paytoqs": "ignore",
                "url": "{{{url}}}",
                "tls": "",
                "persist": False,
                "proxy": "",
                "insecureHTTPParser": False,
                "authType": "",
                "senderr": False,
                "headers": [],
                "x": 1530,
                "y": 940,
                "wires": [["875e0ce6f508eb8e"]],
            },
            {
                "id": "875e0ce6f508eb8e",
                "type": "function",
                "z": load_builder["z"],
                "name": "rename gesamtlast",
                "func": 'msg.topic = "gesamtlast";\nreturn msg;\n',
                "outputs": 1,
                "timeout": 0,
                "noerr": 0,
                "initialize": "",
                "finalize": "",
                "libs": [],
                "x": 1750,
                "y": 940,
                "wires": [["a10f03b13dc84c70", "40bd1d84308a5867"]],
            },
        ]
    )

    # In the source flow PV and /gesamtlast shared node 79da... as a simple
    # pass-through. That node now belongs exclusively to the two-stage load
    # refresh, so PV must bypass it and reach function 3 directly.
    pv_rename = node_by_id(nodes, "abaaa368bd4cf66a")
    pv_rename["name"] = "PV series -> echte 15-min-Slots"
    pv_rename["func"] = PV_SERIES_TO_SLOTS_FUNCTION
    pv_rename["wires"] = [["01081b5bc242bbb9", "a10f03b13dc84c70"]]

    # Controls returned by the new API are indexed from the current slot.
    node_by_id(nodes, "c2a6307eb669063a")["name"] = "Plan speichern (ab jetzt)"
    node_by_id(nodes, "c2a6307eb669063a")["func"] = FUNCTION_4
    simulation_node = node_by_id(nodes, "600feebbd9c0503a")
    simulation_node["name"] = "Simulation speichern (ab jetzt)"
    simulation_node["func"] = update_simulation_timebase(simulation_node["func"])
    for node_id in (
        "80ad76b39bed8ad2",
        "08393d5c53be1551",
        "6ea3455e6d12ed84",
        "3e1ffe2cf5c11f36",
    ):
        node_by_id(nodes, node_id)["func"] = update_control_index(node_by_id(nodes, node_id)["func"])

    node_by_id(nodes, "c7c1bc8ffb849dd9")["name"] = "Tail + Terminalwert speichern"
    node_by_id(nodes, "c7c1bc8ffb849dd9")["func"] = TERMINAL_VALUE_FUNCTION
    node_by_id(nodes, "b8ac7ea4ea819b95")["info"] = SCHEMA_INFO
    node_by_id(nodes, "a8f4519fc6d671bf")["info"] = GRAFANA_INFO

    # Fix the one inconsistent EOS host address in the manual force-update node.
    node_by_id(nodes, "4b50b8d0c073da5d")["url"] = (
        "http://192.168.1.151:8503/v1/prediction/update?force_update=true"
    )

    optimize = node_by_id(nodes, "89c6553101552b40")
    optimize["name"] = "EOS /optimize"
    optimize["wires"][0].append("f07e64ad81a369bf")
    nodes.extend(
        [
            {
                "id": "f07e64ad81a369bf",
                "type": "function",
                "z": optimize["z"],
                "name": "Horizonte: GA / Tail / Terminalwert",
                "func": HORIZON_STATUS_FUNCTION,
                "outputs": 1,
                "timeout": 0,
                "noerr": 0,
                "initialize": "",
                "finalize": "",
                "libs": [],
                "x": 1570,
                "y": 1340,
                "wires": [["972c33086928996f"]],
            },
            {
                "id": "972c33086928996f",
                "type": "debug",
                "z": optimize["z"],
                "name": "Tail-/Terminalwert-Diagnose",
                "active": True,
                "tosidebar": True,
                "console": False,
                "tostatus": False,
                "complete": "payload",
                "targetType": "msg",
                "statusVal": "",
                "statusType": "auto",
                "x": 1850,
                "y": 1340,
                "wires": [],
            },
        ]
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(nodes, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    update_flow(args.source, args.output)


if __name__ == "__main__":
    main()
