//! Input model of the optimizer: `GeneticOptimizationParameters` and the device parameters.
//!
//! Mirror of `optimization/genetic/geneticparams.py`, `devices/genetic/battery.py`
//! (`SolarPanelBatteryParameters`, `ElectricVehicleParameters`), `devices/genetic/inverter.py`
//! (`InverterParameters`) and `devices/genetic/homeappliance.py` (`HomeApplianceParameters`).
//! Field names and defaults as in Python (`model_dump(mode="json")`), old German
//! field names as aliases. Unknown fields are – unlike Python (`extra="forbid"`) –
//! collected in `extra`, so that new upstream fields (e.g. `peak_shaving_threshold_w` from PR D)
//! do not abort the run; `validate()` reports them.

use chrono::{DateTime, FixedOffset, NaiveDateTime, TimeZone};
use chrono_tz::Tz;
use serde::{Deserialize, Deserializer, Serialize, Serializer};
use serde_json::{Map, Value};

/// A timestamp from the input: with offset (absolute) or without (local time of the run timezone,
/// like EOS' `to_datetime()` without a timezone).
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum InputDateTime {
    Aware(DateTime<FixedOffset>),
    Naive(NaiveDateTime),
}

impl InputDateTime {
    /// Absolute timestamp in the run timezone.
    pub fn resolve(&self, tz: Tz) -> DateTime<Tz> {
        match self {
            InputDateTime::Aware(dt) => dt.with_timezone(&tz),
            // pendulum: for ambiguous local time the later one, for a gap the offset before it
            // (same rule as in the request resolution).
            InputDateTime::Naive(n) => crate::datetime::local(tz, *n),
        }
    }

    /// Unix time in seconds (f64, like `DateTime.timestamp()`).
    pub fn timestamp_f64(&self, tz: Tz) -> f64 {
        let dt = self.resolve(tz);
        dt.timestamp() as f64 + f64::from(dt.timestamp_subsec_nanos()) / 1e9
    }

    pub fn parse(text: &str) -> Result<Self, String> {
        let t = text.trim();
        if let Ok(dt) = DateTime::parse_from_rfc3339(t) {
            return Ok(InputDateTime::Aware(dt));
        }
        // ISO with offset, but without seconds or similar.
        for fmt in ["%Y-%m-%dT%H:%M%:z", "%Y-%m-%d %H:%M:%S%:z", "%Y-%m-%dT%H:%M:%S%.f%:z"] {
            if let Ok(dt) = DateTime::parse_from_str(t, fmt) {
                return Ok(InputDateTime::Aware(dt));
            }
        }
        if let Some(stripped) = t.strip_suffix('Z') {
            for fmt in ["%Y-%m-%dT%H:%M:%S%.f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M"] {
                if let Ok(n) = NaiveDateTime::parse_from_str(stripped, fmt) {
                    return Ok(InputDateTime::Aware(chrono::Utc.from_utc_datetime(&n).fixed_offset()));
                }
            }
        }
        for fmt in
            ["%Y-%m-%dT%H:%M:%S%.f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"]
        {
            if let Ok(n) = NaiveDateTime::parse_from_str(t, fmt) {
                return Ok(InputDateTime::Naive(n));
            }
        }
        Err(format!("invalid datetime: {t}"))
    }
}

impl<'de> Deserialize<'de> for InputDateTime {
    fn deserialize<D: Deserializer<'de>>(d: D) -> Result<Self, D::Error> {
        let s = String::deserialize(d)?;
        InputDateTime::parse(&s).map_err(serde::de::Error::custom)
    }
}

impl Serialize for InputDateTime {
    fn serialize<S: Serializer>(&self, s: S) -> Result<S::Ok, S::Error> {
        match self {
            InputDateTime::Aware(dt) => s.serialize_str(&dt.to_rfc3339()),
            InputDateTime::Naive(n) => s.serialize_str(&n.format("%Y-%m-%dT%H:%M:%S").to_string()),
        }
    }
}

/// Pydantic `int` in lax mode: integer or float without a fractional part.
pub fn de_int<'de, D: Deserializer<'de>>(d: D) -> Result<i64, D::Error> {
    let v = Value::deserialize(d)?;
    value_to_int(&v).map_err(serde::de::Error::custom)
}

pub fn de_opt_int<'de, D: Deserializer<'de>>(d: D) -> Result<Option<i64>, D::Error> {
    let v = Option::<Value>::deserialize(d)?;
    match v {
        None | Some(Value::Null) => Ok(None),
        Some(v) => value_to_int(&v).map(Some).map_err(serde::de::Error::custom),
    }
}

fn value_to_int(v: &Value) -> Result<i64, String> {
    match v {
        Value::Number(n) => {
            if let Some(i) = n.as_i64() {
                Ok(i)
            } else if let Some(f) = n.as_f64() {
                if f.fract() == 0.0 && f.is_finite() {
                    Ok(f as i64)
                } else {
                    Err(format!("expected an integer, got {f}"))
                }
            } else {
                Err("expected an integer".into())
            }
        }
        Value::String(s) => s.trim().parse::<i64>().map_err(|e| e.to_string()),
        Value::Bool(b) => Ok(i64::from(*b)),
        _ => Err("expected an integer".into()),
    }
}

fn default_charging_efficiency() -> f64 {
    0.88
}
fn default_bat_discharging_efficiency() -> f64 {
    0.88
}
fn default_ev_discharging_efficiency() -> f64 {
    1.0
}
fn default_max_charge_power_w() -> Option<f64> {
    Some(5000.0)
}
fn default_max_soc() -> i64 {
    100
}
fn default_one() -> f64 {
    1.0
}
fn default_reference_load_fraction() -> f64 {
    0.06
}
fn default_num_cycles() -> i64 {
    1
}

/// `SolarPanelBatteryParameters` (home battery).
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct SolarPanelBatteryParameters {
    pub device_id: String,
    #[serde(default, deserialize_with = "de_opt_int")]
    pub hours: Option<i64>,
    #[serde(deserialize_with = "de_int")]
    pub capacity_wh: i64,
    #[serde(default = "default_charging_efficiency")]
    pub charging_efficiency: f64,
    #[serde(default = "default_bat_discharging_efficiency")]
    pub discharging_efficiency: f64,
    #[serde(default = "default_max_charge_power_w")]
    pub max_charge_power_w: Option<f64>,
    #[serde(default, deserialize_with = "de_int")]
    pub initial_soc_percentage: i64,
    #[serde(default, deserialize_with = "de_int")]
    pub min_soc_percentage: i64,
    #[serde(default = "default_max_soc", deserialize_with = "de_int")]
    pub max_soc_percentage: i64,
    #[serde(default)]
    pub charge_rates: Option<Vec<f64>>,
    #[serde(default)]
    pub grid_export_rates: Option<Vec<f64>>,
    #[serde(default)]
    pub levelized_cost_of_storage_kwh: f64,
    #[serde(default, deserialize_with = "de_opt_int")]
    pub full_charge_soc_percentage: Option<i64>,
    #[serde(default)]
    pub full_charge_deadline_datetime: Option<InputDateTime>,
    #[serde(flatten, skip_serializing)]
    pub extra: Map<String, Value>,
}

/// `ElectricVehicleParameters` (EV).
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct ElectricVehicleParameters {
    pub device_id: String,
    #[serde(default, deserialize_with = "de_opt_int")]
    pub hours: Option<i64>,
    #[serde(deserialize_with = "de_int")]
    pub capacity_wh: i64,
    #[serde(default = "default_charging_efficiency")]
    pub charging_efficiency: f64,
    #[serde(default = "default_ev_discharging_efficiency")]
    pub discharging_efficiency: f64,
    #[serde(default = "default_max_charge_power_w")]
    pub max_charge_power_w: Option<f64>,
    #[serde(default, deserialize_with = "de_int")]
    pub initial_soc_percentage: i64,
    #[serde(default, deserialize_with = "de_int")]
    pub min_soc_percentage: i64,
    #[serde(default = "default_max_soc", deserialize_with = "de_int")]
    pub max_soc_percentage: i64,
    #[serde(default)]
    pub charge_rates: Option<Vec<f64>>,
    #[serde(default)]
    pub grid_export_rates: Option<Vec<f64>>,
    #[serde(default)]
    pub min_soc_deadline_datetime: Option<InputDateTime>,
    #[serde(default)]
    pub min_soc_max_duration_h: Option<f64>,
    #[serde(flatten, skip_serializing)]
    pub extra: Map<String, Value>,
}

/// `InverterParameters`.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct InverterParameters {
    pub device_id: String,
    #[serde(default, deserialize_with = "de_opt_int")]
    pub hours: Option<i64>,
    pub max_power_wh: f64,
    #[serde(default)]
    pub battery_id: Option<String>,
    #[serde(default = "default_one")]
    pub ac_to_dc_efficiency: f64,
    #[serde(default = "default_one")]
    pub dc_to_ac_efficiency: f64,
    /// Points (load fraction, efficiency), strictly increasing in load fraction.
    #[serde(default)]
    pub dc_to_ac_efficiency_curve: Option<Vec<(f64, f64)>>,
    #[serde(default = "default_reference_load_fraction")]
    pub dc_to_ac_efficiency_reference_load_fraction: f64,
    #[serde(default)]
    pub max_ac_charge_power_w: Option<f64>,
    #[serde(default)]
    pub ac_charge_limits_total_charge: bool,
    #[serde(flatten, skip_serializing)]
    pub extra: Map<String, Value>,
}

/// `HomeApplianceParameters` (schedulable household appliances).
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct HomeApplianceParameters {
    pub device_id: String,
    #[serde(default, deserialize_with = "de_opt_int")]
    pub hours: Option<i64>,
    #[serde(default, deserialize_with = "de_opt_int")]
    pub consumption_wh: Option<i64>,
    #[serde(default, deserialize_with = "de_opt_int")]
    pub duration_h: Option<i64>,
    #[serde(default = "default_num_cycles", deserialize_with = "de_int")]
    pub num_cycles: i64,
    #[serde(default, deserialize_with = "de_int")]
    pub completed_cycles: i64,
    #[serde(default, deserialize_with = "de_int")]
    pub min_cycle_gap_h: i64,
    /// Time windows per cycle (`CycleTimeWindowSequence`). Only `None` is supported.
    #[serde(default)]
    pub time_windows: Option<Value>,
    /// Shared time windows (`TimeWindowSequence`). Only `None` is supported.
    #[serde(default)]
    pub shared_time_windows: Option<Value>,
    #[serde(default)]
    pub load_profile_power_w: Option<Vec<f64>>,
    #[serde(default, deserialize_with = "de_opt_int")]
    pub load_profile_interval_seconds: Option<i64>,
    #[serde(default = "default_schedule_mode")]
    pub schedule_mode: String,
    #[serde(default)]
    pub earliest_start_datetime: Option<InputDateTime>,
    #[serde(default)]
    pub deadline_datetime: Option<InputDateTime>,
    #[serde(default = "default_deadline_policy")]
    pub deadline_policy: String,
    #[serde(flatten, skip_serializing)]
    pub extra: Map<String, Value>,
}

fn default_schedule_mode() -> String {
    "ONCE".into()
}
fn default_deadline_policy() -> String {
    "BEST_EFFORT".into()
}

/// `feed_in_tariff_per_wh`: series or fixed value.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(untagged)]
pub enum FeedInTariff {
    Series(#[serde(deserialize_with = "de_float_list")] Vec<f64>),
    Scalar(f64),
}

/// `GeneticEnergyManagementParameters`.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct EnergyManagementParameters {
    #[serde(alias = "pv_prognose_wh", deserialize_with = "de_float_list")]
    pub pv_forecast_wh: Vec<f64>,
    #[serde(alias = "strompreis_euro_pro_wh", deserialize_with = "de_float_list")]
    pub electricity_price_per_wh: Vec<f64>,
    #[serde(alias = "einspeiseverguetung_euro_pro_wh")]
    pub feed_in_tariff_per_wh: FeedInTariff,
    #[serde(alias = "preis_euro_pro_wh_akku")]
    pub price_per_wh_battery: f64,
    #[serde(alias = "gesamtlast", deserialize_with = "de_float_list")]
    pub total_load: Vec<f64>,
}

/// List of numbers; `null` becomes NaN (like `np.asarray([... None ...], float)`).
pub fn de_float_list<'de, D: Deserializer<'de>>(d: D) -> Result<Vec<f64>, D::Error> {
    let v = Vec::<Option<f64>>::deserialize(d)?;
    Ok(v.into_iter().map(|x| x.unwrap_or(f64::NAN)).collect())
}

/// `GeneticOptimizationParameters`.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct GeneticOptimizationParameters {
    pub ems: EnergyManagementParameters,
    #[serde(default, alias = "pv_akku")]
    pub pv_battery: Option<SolarPanelBatteryParameters>,
    #[serde(default)]
    pub inverter: Option<InverterParameters>,
    #[serde(default, alias = "eauto")]
    pub ev: Option<ElectricVehicleParameters>,
    #[serde(default)]
    pub dishwasher: Option<HomeApplianceParameters>,
    #[serde(default)]
    pub temperature_forecast: Option<Vec<Option<f64>>>,
    #[serde(default)]
    pub start_solution: Option<Vec<f64>>,
    #[serde(default, deserialize_with = "de_opt_int")]
    pub forecast_interval_seconds: Option<i64>,
    #[serde(default)]
    pub home_appliances: Option<Vec<HomeApplianceParameters>>,
    #[serde(default)]
    pub start_solution_datetime: Option<InputDateTime>,
}

impl GeneticOptimizationParameters {
    /// `resolved_home_appliances()`.
    pub fn resolved_home_appliances(&self) -> Vec<HomeApplianceParameters> {
        if let Some(list) = &self.home_appliances {
            return list.clone();
        }
        self.dishwasher.iter().cloned().collect()
    }

    /// The Pydantic checks, as far as they matter for the run.
    pub fn validate(&self) -> Result<(), String> {
        if self.pv_battery.is_some() && self.inverter.is_none() {
            // Python keeps such a battery out of the simulation (`GeneticSimulation.prepare`)
            // but still builds its terminal value curve; not reproduced natively.
            return Err(format!("a battery without an inverter is {}", crate::run::NOT_SUPPORTED));
        }
        let e = &self.ems;
        if e.pv_forecast_wh.is_empty() || e.electricity_price_per_wh.is_empty() || e.total_load.is_empty() {
            return Err("Forecast arrays must not be empty.".into());
        }
        if let FeedInTariff::Series(s) = &e.feed_in_tariff_per_wh {
            if s.is_empty() {
                return Err("Forecast arrays must not be empty.".into());
            }
        }
        if let Some(fi) = self.forecast_interval_seconds {
            if fi != 900 && fi != 3600 {
                return Err("forecast_interval_seconds must be 900 or 3600".into());
            }
        }
        if let Some(t) = &self.temperature_forecast {
            if t.len() != e.pv_forecast_wh.len() {
                return Err("Input lists have different lengths".into());
            }
        }
        if let Some(s) = &self.start_solution {
            if s.len() < 2 {
                return Err("Requires at least two values.".into());
            }
        }
        if self.dishwasher.is_some() && self.home_appliances.is_some() {
            return Err("Provide either 'home_appliances' or the deprecated 'dishwasher', not both.".into());
        }
        let ids: Vec<&str> = self.home_appliances.iter().flatten().map(|a| a.device_id.as_str()).collect();
        let mut sorted = ids.clone();
        sorted.sort_unstable();
        sorted.dedup();
        if sorted.len() != ids.len() {
            return Err("home_appliances device_id values must be unique.".into());
        }
        if let Some(b) = &self.pv_battery {
            if let Some(f) = b.full_charge_soc_percentage {
                if f > b.max_soc_percentage {
                    return Err("full_charge_soc_percentage must be <= max_soc_percentage".into());
                }
            }
            if b.capacity_wh <= 0 {
                return Err("capacity_wh must be > 0".into());
            }
        }
        if let Some(inv) = &self.inverter {
            if let Some(curve) = &inv.dc_to_ac_efficiency_curve {
                validate_efficiency_curve(curve)?;
            }
        }
        for (name, extra) in [
            ("pv_battery", self.pv_battery.as_ref().map(|b| &b.extra)),
            ("ev", self.ev.as_ref().map(|b| &b.extra)),
            ("inverter", self.inverter.as_ref().map(|b| &b.extra)),
        ] {
            if let Some(x) = extra {
                if let Some(k) = x.keys().next() {
                    return Err(format!("{name}: field '{k}' is {}", crate::run::NOT_SUPPORTED));
                }
            }
        }
        for a in self.resolved_home_appliances() {
            if let Some(k) = a.extra.keys().next() {
                return Err(format!("home appliance {}: field '{k}' is {}", a.device_id, crate::run::NOT_SUPPORTED));
            }
            if a.completed_cycles > a.num_cycles {
                return Err("completed_cycles must not exceed num_cycles.".into());
            }
        }
        Ok(())
    }
}

/// `devicesabc.validate_efficiency_curve` (check only, no transformation).
pub fn validate_efficiency_curve(curve: &[(f64, f64)]) -> Result<(), String> {
    if curve.len() < 2 {
        return Err("An efficiency curve needs at least two points.".into());
    }
    let mut last = f64::NEG_INFINITY;
    for &(x, e) in curve {
        if !(x.is_finite() && e.is_finite()) || !(0.0..=1.0).contains(&x) || !(e > 0.0 && e <= 1.0) {
            return Err("dc_to_ac_efficiency_curve: load fractions in [0, 1], efficiencies in (0, 1]".into());
        }
        if x <= last {
            return Err("dc_to_ac_efficiency_curve: load fractions must be strictly increasing".into());
        }
        last = x;
    }
    Ok(())
}

impl GeneticOptimizationParameters {
    /// Read from JSON, also from Python's `model_dump()`, which additionally contains the deprecated
    /// fields (`pv_akku`, `eauto`, `pv_prognose_wh` …) as computed fields.
    pub fn from_json_value(mut v: Value) -> Result<Self, String> {
        fn drop_alias(m: &mut Map<String, Value>, canonical: &str, alias: &str) {
            if m.contains_key(canonical) {
                m.remove(alias);
            }
        }
        if let Value::Object(m) = &mut v {
            drop_alias(m, "pv_battery", "pv_akku");
            drop_alias(m, "ev", "eauto");
            if let Some(Value::Object(e)) = m.get_mut("ems") {
                drop_alias(e, "pv_forecast_wh", "pv_prognose_wh");
                drop_alias(e, "electricity_price_per_wh", "strompreis_euro_pro_wh");
                drop_alias(e, "feed_in_tariff_per_wh", "einspeiseverguetung_euro_pro_wh");
                drop_alias(e, "price_per_wh_battery", "preis_euro_pro_wh_akku");
                drop_alias(e, "total_load", "gesamtlast");
            }
        }
        serde_json::from_value(v).map_err(|e| e.to_string())
    }
}
