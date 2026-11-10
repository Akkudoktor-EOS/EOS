//! Data model of the GENETIC solution (`geneticsolution.py`): [`GeneticSolution`] and its
//! parts, serialized like `GeneticSolution.model_dump(mode="json")` so Python can validate it
//! back into the same pydantic model.

pub mod model;

use chrono::{DateTime, Offset, TimeZone, Utc};
use chrono_tz::Tz;
use serde::Serialize;
use serde_json::{Map, Value};

use crate::params::{
    ElectricVehicleParameters, GeneticOptimizationParameters, HomeApplianceParameters, InputDateTime,
    SolarPanelBatteryParameters,
};

pub use model::*;

// --------------------------------------------------------------------------- time formats

fn push_offset(out: &mut String, offset_secs: i32) {
    let sign = if offset_secs < 0 { '-' } else { '+' };
    let a = offset_secs.unsigned_abs();
    out.push(sign);
    out.push_str(&format!("{:02}:{:02}", a / 3600, (a % 3600) / 60));
    if !a.is_multiple_of(60) {
        out.push_str(&format!(":{:02}", a % 60));
    }
}

fn format_base<Z: TimeZone>(dt: &DateTime<Z>) -> String {
    let mut s = dt.naive_local().format("%Y-%m-%dT%H:%M:%S").to_string();
    let micros = dt.timestamp_subsec_micros() % 1_000_000;
    if micros != 0 {
        s.push_str(&format!(".{micros:06}"));
    }
    s
}

/// Pydantic JSON of a timestamp: microseconds only if ≠ 0, offset 0 as `Z`.
pub fn format_pydantic<Z: TimeZone>(dt: &DateTime<Z>) -> String {
    let off = dt.offset().fix().local_minus_utc();
    let mut s = format_base(dt);
    if off == 0 {
        s.push('Z');
    } else {
        push_offset(&mut s, off);
    }
    s
}

pub(crate) fn to_json<T: Serialize>(t: &T) -> Value {
    serde_json::to_value(t).unwrap_or(Value::Null)
}

fn to_map<T: Serialize>(t: &T) -> Map<String, Value> {
    match to_json(t) {
        Value::Object(m) => m,
        _ => Map::new(),
    }
}

// --------------------------------------------------------------------------- parameter JSON

/// Device timestamp as Pydantic writes it after `to_datetime(value)`: in the server zone (with
/// zone) or unchanged (without zone, for plain serde output).
fn device_datetime(v: Option<&InputDateTime>, server_tz: Option<Tz>) -> Value {
    match (v, server_tz) {
        (None, _) => Value::Null,
        (Some(InputDateTime::Aware(dt)), Some(tz)) => Value::String(format_pydantic(&dt.with_timezone(&tz))),
        (Some(InputDateTime::Aware(dt)), None) => Value::String(format_pydantic(dt)),
        (Some(d @ InputDateTime::Naive(_)), Some(tz)) => Value::String(format_pydantic(&d.resolve(tz))),
        (Some(InputDateTime::Naive(n)), None) => Value::String(format_base(&Utc.from_utc_datetime(n))),
    }
}

/// `start_solution_datetime` of the parameters: with offset unchanged (`DateTime.instance`),
/// without offset local time of the server (`to_datetime(value)`).
fn start_solution_datetime_json(v: Option<&InputDateTime>, server_tz: Option<Tz>) -> Value {
    match v {
        Some(InputDateTime::Aware(dt)) => Value::String(format_pydantic(dt)),
        other => device_datetime(other, server_tz),
    }
}

fn battery_json(b: &SolarPanelBatteryParameters, tz: Option<Tz>) -> Value {
    let mut m = to_map(b);
    m.insert("full_charge_deadline_datetime".into(), device_datetime(b.full_charge_deadline_datetime.as_ref(), tz));
    Value::Object(m)
}

fn ev_json(e: &ElectricVehicleParameters, tz: Option<Tz>) -> Value {
    let mut m = to_map(e);
    m.insert("min_soc_deadline_datetime".into(), device_datetime(e.min_soc_deadline_datetime.as_ref(), tz));
    Value::Object(m)
}

fn appliance_json(a: &HomeApplianceParameters, tz: Option<Tz>) -> Value {
    let mut m = to_map(a);
    m.insert("earliest_start_datetime".into(), device_datetime(a.earliest_start_datetime.as_ref(), tz));
    m.insert("deadline_datetime".into(), device_datetime(a.deadline_datetime.as_ref(), tz));
    Value::Object(m)
}

/// `GeneticOptimizationParameters.model_dump(mode="json")`.
///
/// `server_tz`: zone into which Python converts the device timestamps on validation (`None` =
/// leave unchanged). `computed`: append old names (`pv_akku`, `eauto`, `pv_prognose_wh`, …) like the
/// server. The devices' `time_windows`/`shared_time_windows` pass through unchanged
/// (`serde_json::Value`) – the optimizer must fill them the way Python completes them during the run.
pub fn parameters_json(p: &GeneticOptimizationParameters, server_tz: Option<Tz>, computed: bool) -> Value {
    let mut ems = to_map(&p.ems);
    if computed {
        for (new, old) in [
            ("pv_forecast_wh", "pv_prognose_wh"),
            ("electricity_price_per_wh", "strompreis_euro_pro_wh"),
            ("feed_in_tariff_per_wh", "einspeiseverguetung_euro_pro_wh"),
            ("price_per_wh_battery", "preis_euro_pro_wh_akku"),
            ("total_load", "gesamtlast"),
        ] {
            let v = ems.get(new).cloned().unwrap_or(Value::Null);
            ems.insert(old.into(), v);
        }
    }
    let mut m = Map::new();
    m.insert("ems".into(), Value::Object(ems));
    let battery = p.pv_battery.as_ref().map_or(Value::Null, |b| battery_json(b, server_tz));
    let ev = p.ev.as_ref().map_or(Value::Null, |e| ev_json(e, server_tz));
    m.insert("pv_battery".into(), battery.clone());
    m.insert("inverter".into(), p.inverter.as_ref().map_or(Value::Null, |i| Value::Object(to_map(i))));
    m.insert("ev".into(), ev.clone());
    m.insert("dishwasher".into(), p.dishwasher.as_ref().map_or(Value::Null, |a| appliance_json(a, server_tz)));
    m.insert("temperature_forecast".into(), to_json(&p.temperature_forecast));
    m.insert("start_solution".into(), to_json(&p.start_solution));
    m.insert("forecast_interval_seconds".into(), to_json(&p.forecast_interval_seconds));
    m.insert(
        "home_appliances".into(),
        p.home_appliances
            .as_ref()
            .map_or(Value::Null, |list| Value::Array(list.iter().map(|a| appliance_json(a, server_tz)).collect())),
    );
    m.insert(
        "start_solution_datetime".into(),
        start_solution_datetime_json(p.start_solution_datetime.as_ref(), server_tz),
    );
    if computed {
        m.insert("pv_akku".into(), battery);
        m.insert("eauto".into(), ev);
    }
    Value::Object(m)
}
