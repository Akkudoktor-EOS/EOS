// Tail und anschließenden Terminalwert der Optimierung -> MariaDB
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
