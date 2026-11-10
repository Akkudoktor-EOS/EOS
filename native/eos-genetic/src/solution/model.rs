//! Data model of the GENETIC solution (`geneticsolution.py`, `terminalvalue.py`).
//!
//! Plain data without hidden invariants: the Rust GA fills these structures at the end of a
//! run (Python: `GeneticOptimization.optimize_ems`, returning `GeneticSolution(...)`), the
//! output layer in [`super`] turns them into the JSON responses of the EOS server.
//!
//! Field names and order as in Python (Pydantic declaration order), so that
//! `serde_json::to_value` yields the same key order (workspace `serde_json` with
//! `preserve_order`). Derived fields (`@computed_field`, old German names such as
//! `Last_Wh_pro_Stunde`, `eautocharge_hours_float`, `pv_akku`) are **not** part of the
//! structures; they are only created in [`super::optimize_response`]. When reading a Python
//! dump (`model_dump(mode="json")`, which contains these fields) they are ignored or, for
//! `parameters`, removed before reading.
//!
//! NaN: Python stores NaN in `list[float]`, the server writes it as JSON `null`. On
//! reading, `null` in number series becomes NaN again; on writing, NaN/±inf become `null`
//! (serde_json behaviour for non-finite numbers).
//!
//! Extensibility (not yet in the reference version): PR E (`soc_value_steps`) arrives as a new
//! `Option` field on [`GeneticSolution`] or [`TerminalValueResult`] with
//! `#[serde(default, skip_serializing_if = "Option::is_none")]`; PR D
//! (`peak_shaving_threshold_w`) belongs to the FRBC instruction, see
//! [`super::plan::PlanInstruction::peak_shaving_threshold_w`].

use chrono::{DateTime, FixedOffset, NaiveDateTime, TimeZone, Utc};
use serde::de::Error as _;
use serde::{Deserialize, Deserializer, Serialize, Serializer};
use serde_json::{Map, Value};

use crate::params::GeneticOptimizationParameters;

// --------------------------------------------------------------------------- helper types

/// Ordered map `device_id → value` (Python `dict`, insertion order is preserved).
///
/// Stored as a list of pairs so that the order of the devices (Python: order of
/// `home_appliances`) is preserved without an extra dependency. The type does not check for
/// duplicate keys; [`OrderedMap::insert`] replaces in place (like `dict[key] = value`).
#[derive(Debug, Clone, PartialEq)]
pub struct OrderedMap<V>(pub Vec<(String, V)>);

impl<V> Default for OrderedMap<V> {
    fn default() -> Self {
        OrderedMap(Vec::new())
    }
}

impl<V> OrderedMap<V> {
    pub fn new() -> Self {
        Self::default()
    }

    /// `dict[key] = value`: replace an existing key in place, otherwise append.
    pub fn insert(&mut self, key: impl Into<String>, value: V) {
        let key = key.into();
        if let Some(slot) = self.0.iter_mut().find(|(k, _)| *k == key) {
            slot.1 = value;
        } else {
            self.0.push((key, value));
        }
    }

    pub fn get(&self, key: &str) -> Option<&V> {
        self.0.iter().find(|(k, _)| k == key).map(|(_, v)| v)
    }

    pub fn iter(&self) -> impl Iterator<Item = (&str, &V)> {
        self.0.iter().map(|(k, v)| (k.as_str(), v))
    }

    pub fn len(&self) -> usize {
        self.0.len()
    }

    pub fn is_empty(&self) -> bool {
        self.0.is_empty()
    }
}

impl<V: Serialize> Serialize for OrderedMap<V> {
    fn serialize<S: Serializer>(&self, s: S) -> Result<S::Ok, S::Error> {
        use serde::ser::SerializeMap;
        let mut m = s.serialize_map(Some(self.0.len()))?;
        for (k, v) in &self.0 {
            m.serialize_entry(k, v)?;
        }
        m.end()
    }
}

impl<'de, V: Deserialize<'de>> Deserialize<'de> for OrderedMap<V> {
    fn deserialize<D: Deserializer<'de>>(d: D) -> Result<Self, D::Error> {
        struct Visit<V>(std::marker::PhantomData<V>);
        impl<'de, V: Deserialize<'de>> serde::de::Visitor<'de> for Visit<V> {
            type Value = OrderedMap<V>;
            fn expecting(&self, f: &mut std::fmt::Formatter) -> std::fmt::Result {
                f.write_str("a map")
            }
            fn visit_map<A: serde::de::MapAccess<'de>>(self, mut a: A) -> Result<Self::Value, A::Error> {
                let mut out = OrderedMap::new();
                while let Some((k, v)) = a.next_entry::<String, V>()? {
                    out.insert(k, v);
                }
                Ok(out)
            }
            fn visit_unit<E: serde::de::Error>(self) -> Result<Self::Value, E> {
                Ok(OrderedMap::new())
            }
        }
        d.deserialize_any(Visit(std::marker::PhantomData))
    }
}

/// Serde helpers: numbers where JSON `null` stands for NaN.
pub mod nan {
    use super::*;

    /// `f64`; `null` → NaN.
    pub fn de_f64<'de, D: Deserializer<'de>>(d: D) -> Result<f64, D::Error> {
        Ok(Option::<f64>::deserialize(d)?.unwrap_or(f64::NAN))
    }

    /// `list[float]`; `null` entries → NaN, `null` as the whole list → empty.
    pub fn de_vec<'de, D: Deserializer<'de>>(d: D) -> Result<Vec<f64>, D::Error> {
        let v = Option::<Vec<Option<f64>>>::deserialize(d)?.unwrap_or_default();
        Ok(v.into_iter().map(|x| x.unwrap_or(f64::NAN)).collect())
    }

    /// `Optional[list[float]]`; `null` → `None`, `null` entries → NaN.
    pub fn de_opt_vec<'de, D: Deserializer<'de>>(d: D) -> Result<Option<Vec<f64>>, D::Error> {
        let v = Option::<Vec<Option<f64>>>::deserialize(d)?;
        Ok(v.map(|v| v.into_iter().map(|x| x.unwrap_or(f64::NAN)).collect()))
    }

    /// `dict[str, list[float]]` with NaN-capable series.
    pub fn de_map_vec<'de, D: Deserializer<'de>>(d: D) -> Result<OrderedMap<Vec<f64>>, D::Error> {
        let m = Option::<OrderedMap<Vec<Option<f64>>>>::deserialize(d)?.unwrap_or_default();
        Ok(OrderedMap(
            m.0.into_iter().map(|(k, v)| (k, v.into_iter().map(|x| x.unwrap_or(f64::NAN)).collect())).collect(),
        ))
    }

    /// `Optional[dict[str, list[float]]]` with NaN-capable series.
    pub fn de_opt_map_vec<'de, D: Deserializer<'de>>(d: D) -> Result<Option<OrderedMap<Vec<f64>>>, D::Error> {
        let m = Option::<OrderedMap<Vec<Option<f64>>>>::deserialize(d)?;
        Ok(m.map(|m| {
            OrderedMap(
                m.0.into_iter().map(|(k, v)| (k, v.into_iter().map(|x| x.unwrap_or(f64::NAN)).collect())).collect(),
            )
        }))
    }
}

/// Serde helpers for timestamps in Pydantic JSON format (`2026-11-10T10:00:00+01:00`,
/// microseconds only if ≠ 0, offset 0 as `Z`).
pub mod pydt {
    use super::*;

    /// Read a Python timestamp from JSON (ISO 8601 with offset or `Z`; without offset as UTC).
    pub fn parse(text: &str) -> Result<DateTime<FixedOffset>, String> {
        let t = text.trim();
        if let Ok(dt) = DateTime::parse_from_rfc3339(t) {
            return Ok(dt);
        }
        for fmt in ["%Y-%m-%dT%H:%M:%S%.f%:z", "%Y-%m-%dT%H:%M%:z", "%Y-%m-%d %H:%M:%S%.f%:z"] {
            if let Ok(dt) = DateTime::parse_from_str(t, fmt) {
                return Ok(dt);
            }
        }
        for fmt in ["%Y-%m-%dT%H:%M:%S%.f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M"] {
            if let Ok(n) = NaiveDateTime::parse_from_str(t.trim_end_matches('Z'), fmt) {
                return Ok(Utc.from_utc_datetime(&n).fixed_offset());
            }
        }
        Err(format!("invalid datetime: {t}"))
    }

    pub fn serialize<S: Serializer, Z: TimeZone>(dt: &DateTime<Z>, s: S) -> Result<S::Ok, S::Error> {
        s.serialize_str(&crate::solution::format_pydantic(dt))
    }

    pub fn deserialize<'de, D: Deserializer<'de>>(d: D) -> Result<DateTime<FixedOffset>, D::Error> {
        let s = String::deserialize(d)?;
        parse(&s).map_err(D::Error::custom)
    }

    pub mod opt {
        use super::*;

        pub fn serialize<S: Serializer>(dt: &Option<DateTime<FixedOffset>>, s: S) -> Result<S::Ok, S::Error> {
            match dt {
                Some(dt) => s.serialize_str(&crate::solution::format_pydantic(dt)),
                None => s.serialize_none(),
            }
        }

        pub fn deserialize<'de, D: Deserializer<'de>>(d: D) -> Result<Option<DateTime<FixedOffset>>, D::Error> {
            match Option::<String>::deserialize(d)? {
                Some(s) => super::parse(&s).map(Some).map_err(D::Error::custom),
                None => Ok(None),
            }
        }
    }

    /// `dict[str, list[DateTime]]`.
    pub mod map_vec {
        use super::*;

        pub fn serialize<S: Serializer>(m: &OrderedMap<Vec<DateTime<FixedOffset>>>, s: S) -> Result<S::Ok, S::Error> {
            let conv = OrderedMap(
                m.0.iter()
                    .map(|(k, v)| (k.clone(), v.iter().map(crate::solution::format_pydantic).collect::<Vec<_>>()))
                    .collect(),
            );
            conv.serialize(s)
        }

        pub fn deserialize<'de, D: Deserializer<'de>>(
            d: D,
        ) -> Result<OrderedMap<Vec<DateTime<FixedOffset>>>, D::Error> {
            let m = Option::<OrderedMap<Vec<String>>>::deserialize(d)?.unwrap_or_default();
            let mut out = OrderedMap::new();
            for (k, v) in m.0 {
                let list =
                    v.iter().map(|s| super::parse(s)).collect::<Result<Vec<_>, _>>().map_err(D::Error::custom)?;
                out.insert(k, list);
            }
            Ok(out)
        }
    }
}

/// Resolve aliases like Pydantic (`validation_alias=AliasChoices(new, old)`): if the new name
/// is present, the old one (a `computed_field` in the dump) is dropped, otherwise the old one is renamed.
pub(crate) fn normalize_aliases(obj: &mut Map<String, Value>, pairs: &[(&str, &str)]) {
    for (canonical, alias) in pairs {
        if obj.contains_key(*canonical) {
            obj.shift_remove(*alias);
        } else if let Some(v) = obj.shift_remove(*alias) {
            obj.insert((*canonical).to_string(), v);
        }
    }
}

/// Read `parameters` from a Python dump: the `computed_field`s (`pv_akku`, `eauto`, German
/// `ems` names) would collide with the serde aliases in `params.rs` as duplicate fields.
fn de_parameters<'de, D: Deserializer<'de>>(d: D) -> Result<GeneticOptimizationParameters, D::Error> {
    let mut v = Value::deserialize(d)?;
    if let Value::Object(top) = &mut v {
        normalize_aliases(top, &[("pv_battery", "pv_akku"), ("ev", "eauto")]);
        if let Some(Value::Object(ems)) = top.get_mut("ems") {
            normalize_aliases(
                ems,
                &[
                    ("pv_forecast_wh", "pv_prognose_wh"),
                    ("electricity_price_per_wh", "strompreis_euro_pro_wh"),
                    ("feed_in_tariff_per_wh", "einspeiseverguetung_euro_pro_wh"),
                    ("price_per_wh_battery", "preis_euro_pro_wh_akku"),
                    ("total_load", "gesamtlast"),
                ],
            );
        }
    }
    GeneticOptimizationParameters::deserialize(v).map_err(D::Error::custom)
}

/// Serializes `parameters` in Python form, but without `computed_field`s and without timezone
/// conversion (that is done by [`super::parameters_json`] with the context).
fn ser_parameters<S: Serializer>(p: &GeneticOptimizationParameters, s: S) -> Result<S::Ok, S::Error> {
    super::parameters_json(p, None, false).serialize(s)
}

/// `GeneticSimulationResult` from a Python dump: resolve old German names.
fn de_result<'de, D: Deserializer<'de>>(d: D) -> Result<GeneticSimulationResult, D::Error> {
    let mut v = Value::deserialize(d)?;
    if let Value::Object(m) = &mut v {
        normalize_aliases(m, RESULT_ALIASES);
    }
    GeneticSimulationResult::deserialize(v).map_err(D::Error::custom)
}

/// (new name, old German name) of `GeneticSimulationResult`, in the order of the
/// `computed_field`s in the Python model (which is also how they appear in the dump).
pub(crate) const RESULT_ALIASES: &[(&str, &str)] = &[
    ("load_wh_per_hour", "Last_Wh_pro_Stunde"),
    ("ev_soc_per_hour", "EAuto_SoC_pro_Stunde"),
    ("revenue_per_hour", "Einnahmen_Euro_pro_Stunde"),
    ("total_losses", "Gesamt_Verluste"),
    ("total_balance", "Gesamtbilanz_Euro"),
    ("total_revenue", "Gesamteinnahmen_Euro"),
    ("total_costs", "Gesamtkosten_Euro"),
    ("home_appliance_wh_per_hour", "Home_appliance_wh_per_hour"),
    ("costs_per_hour", "Kosten_Euro_pro_Stunde"),
    ("grid_consumption_wh_per_hour", "Netzbezug_Wh_pro_Stunde"),
    ("grid_feed_in_wh_per_hour", "Netzeinspeisung_Wh_pro_Stunde"),
    ("losses_per_hour", "Verluste_Pro_Stunde"),
    ("battery_soc_per_hour", "akku_soc_pro_stunde"),
    ("electricity_price", "Electricity_price"),
    ("feed_in_tariff", "Feed_in_tariff"),
];

fn default_interval_seconds() -> i64 {
    3600
}
fn default_continuation_mode() -> String {
    "FIXED".into()
}

// --------------------------------------------------------------------------- solution

/// `GeneticSolution` – result of a GENETIC run (response of `POST /v1/optimize`).
///
/// All control series (`ac_charge`, `dc_charge`, `discharge_allowed`, `battery_grid_export_*`,
/// `ev_charge_hours_float`) have one element per slot (`interval_seconds`). With
/// `controls_start_at_now == true` (current GENETIC run) they start at the slot of
/// `start_solution_datetime`, otherwise (old payloads) at midnight of the start day.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct GeneticSolution {
    /// Input parameters of the run as the optimizer holds them at the end (during the run Python
    /// e.g. replaces the forecast series with those from "now" and fills the devices' `time_windows`).
    #[serde(deserialize_with = "de_parameters", serialize_with = "ser_parameters")]
    pub parameters: GeneticOptimizationParameters,
    /// AC charging (from the grid) per slot as a fraction of the charge power 0.0–1.0.
    #[serde(deserialize_with = "nan::de_vec")]
    pub ac_charge: Vec<f64>,
    /// DC charging (PV) per slot, fraction 0–1 (currently 0.0/1.0 = blocked/allowed).
    #[serde(deserialize_with = "nan::de_vec")]
    pub dc_charge: Vec<f64>,
    /// Discharging for the house load allowed (1) or not (0), per slot.
    pub discharge_allowed: Vec<i64>,
    /// EV charge factor per slot (0.0–1.0); `None` without an EV or if it is already full enough.
    #[serde(default, deserialize_with = "nan::de_opt_vec")]
    pub ev_charge_hours_float: Option<Vec<f64>>,
    /// Simulation result of the best solution.
    #[serde(deserialize_with = "de_result")]
    pub result: GeneticSimulationResult,
    /// EV state after the simulation (`Battery.to_dict()`), `None` without an EV.
    #[serde(default)]
    pub ev_obj: Option<ElectricVehicleResult>,
    /// Hour of the run start (local time, `ems.start_datetime.hour`).
    #[serde(default)]
    pub start_hour: i64,
    /// Best genome (warm start for the next run); Python: `list[float]`.
    #[serde(default, deserialize_with = "nan::de_opt_vec")]
    pub start_solution: Option<Vec<f64>>,
    /// Deprecated: start slot of the only appliance (hourly only, with exactly one appliance), otherwise `None`.
    #[serde(default)]
    pub washingstart: Option<i64>,
    /// Metrics of the final population: `bilanz`, `verluste`, `nebenbedingung` (per individual).
    #[serde(default, deserialize_with = "nan::de_opt_map_vec")]
    pub extra_data: Option<OrderedMap<Vec<f64>>>,
    /// Fitness history per generation and run statistics.
    #[serde(default)]
    pub fitness_history: Option<FitnessHistory>,
    /// Fixed seed of the run (`None` = random).
    #[serde(default)]
    pub fixed_seed: Option<i64>,
    /// Control series start at the run time instead of midnight (currently always `true`).
    #[serde(default)]
    pub controls_start_at_now: bool,
    /// Battery export to the grid allowed (1) per slot; empty without direct marketing.
    #[serde(default)]
    pub battery_grid_export_allowed: Vec<i64>,
    /// Terminal value of the remaining energy in the battery (with curve); `None` only for very old solutions.
    #[serde(default)]
    pub terminal_value: Option<TerminalValueResult>,
    /// Battery export level per slot as a fraction of the discharge power; empty without direct marketing.
    #[serde(default, deserialize_with = "nan::de_vec")]
    pub battery_grid_export_factor: Vec<f64>,
    /// Start of the slot controlled by gene 0 of `start_solution` (Python: slot 0 of the run, in
    /// the timezone `general.timezone`).
    #[serde(default, with = "pydt::opt")]
    pub start_solution_datetime: Option<DateTime<FixedOffset>>,
    /// Planned start times per appliance (`device_id`), local time `general.timezone`.
    #[serde(default, with = "pydt::map_vec")]
    pub appliance_starts: OrderedMap<Vec<DateTime<FixedOffset>>>,
    /// Per appliance with `deadline_datetime`: deadline missed (or not scheduled at all).
    #[serde(default)]
    pub appliance_deadline_missed: OrderedMap<bool>,
    /// Slot length of the result and control series in seconds (900 or 3600).
    #[serde(default = "default_interval_seconds")]
    pub interval_seconds: i64,
}

/// `GeneticSimulationResult` – simulation series per slot from the run start (despite the names
/// `*_per_hour` per slot, energy in Wh per slot, money in currency per slot).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct GeneticSimulationResult {
    /// Total load [Wh/slot].
    #[serde(deserialize_with = "nan::de_vec")]
    pub load_wh_per_hour: Vec<f64>,
    /// SoC of the EV [%] per slot (0 without an EV).
    #[serde(deserialize_with = "nan::de_vec")]
    pub ev_soc_per_hour: Vec<f64>,
    /// Feed-in revenue [currency/slot].
    #[serde(deserialize_with = "nan::de_vec")]
    pub revenue_per_hour: Vec<f64>,
    /// Total losses [Wh].
    #[serde(deserialize_with = "nan::de_f64")]
    pub total_losses: f64,
    /// Revenue minus costs [currency] (Python: `total_balance`).
    #[serde(deserialize_with = "nan::de_f64")]
    pub total_balance: f64,
    /// Total revenue [currency].
    #[serde(deserialize_with = "nan::de_f64")]
    pub total_revenue: f64,
    /// Total costs [currency].
    #[serde(deserialize_with = "nan::de_f64")]
    pub total_costs: f64,
    /// Energy of all household appliances [Wh/slot]; `None`/NaN allowed.
    pub home_appliance_wh_per_hour: Vec<Option<f64>>,
    /// Grid import costs [currency/slot].
    #[serde(deserialize_with = "nan::de_vec")]
    pub costs_per_hour: Vec<f64>,
    /// Grid import [Wh/slot].
    #[serde(deserialize_with = "nan::de_vec")]
    pub grid_consumption_wh_per_hour: Vec<f64>,
    /// Grid feed-in [Wh/slot].
    #[serde(deserialize_with = "nan::de_vec")]
    pub grid_feed_in_wh_per_hour: Vec<f64>,
    /// Losses [Wh/slot].
    #[serde(deserialize_with = "nan::de_vec")]
    pub losses_per_hour: Vec<f64>,
    /// SoC of the home battery [%] at the start of the slot.
    #[serde(deserialize_with = "nan::de_vec")]
    pub battery_soc_per_hour: Vec<f64>,
    /// Import price used [currency/Wh] per slot.
    #[serde(deserialize_with = "nan::de_vec")]
    pub electricity_price: Vec<f64>,
    /// Energy per appliance (`device_id`) [Wh/slot].
    #[serde(default, deserialize_with = "nan::de_map_vec")]
    pub home_appliance_energy_wh: OrderedMap<Vec<f64>>,
    /// Feed-in tariff used [currency/Wh] per slot.
    #[serde(default, deserialize_with = "nan::de_vec")]
    pub feed_in_tariff: Vec<f64>,
    /// Occupancy per appliance (running in the slot, also in phases without power).
    #[serde(default)]
    pub home_appliance_running: OrderedMap<Vec<bool>>,
}

/// `ElectricVehicleResult` – `ev_obj`, from `Battery.to_dict()` of the simulated EV.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ElectricVehicleResult {
    pub device_id: String,
    /// Length of the simulation series in slots (Python: `prediction_hours`, slots despite the name).
    pub hours: i64,
    /// Charge factor per slot.
    #[serde(deserialize_with = "nan::de_vec")]
    pub charge_array: Vec<f64>,
    /// Discharge per slot (0/1).
    pub discharge_array: Vec<i64>,
    pub discharging_efficiency: f64,
    /// Capacity [Wh] (Python `int`).
    pub capacity_wh: i64,
    pub charging_efficiency: f64,
    /// Maximum charge power [W] (Python `int`; a value with a fractional part would be a
    /// validation error there).
    pub max_charge_power_w: i64,
    /// Energy content at the end of the simulation [Wh].
    #[serde(deserialize_with = "nan::de_f64")]
    pub soc_wh: f64,
    /// SoC at the start [%].
    pub initial_soc_percentage: i64,
}

/// `fitness_history` (Python `dict[str, Any]`, values from the DEAP logbook).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct FitnessHistory {
    /// Generation numbers (0 = initial population).
    #[serde(rename = "gen")]
    pub generation: Vec<i64>,
    #[serde(deserialize_with = "nan::de_vec")]
    pub avg: Vec<f64>,
    #[serde(deserialize_with = "nan::de_vec")]
    pub max: Vec<f64>,
    #[serde(deserialize_with = "nan::de_vec")]
    pub min: Vec<f64>,
    #[serde(deserialize_with = "nan::de_vec")]
    pub diversity: Vec<f64>,
    pub stagnation: Vec<i64>,
    pub immigrants: Vec<i64>,
    /// 1 in generations with a soft restart.
    pub restart: Vec<i64>,
    pub fitness_cache: FitnessCacheStats,
    /// Empty (`{}`) if the adaptive evolution aborted.
    #[serde(default)]
    pub adaptive_evolution: AdaptiveEvolutionMetrics,
    pub local_search: LocalSearchStats,
    /// Pass further/future keys through unchanged.
    #[serde(flatten)]
    pub extra: Map<String, Value>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct FitnessCacheStats {
    pub hits: i64,
    pub misses: i64,
    #[serde(deserialize_with = "nan::de_f64")]
    pub hit_rate: f64,
    pub keys: i64,
}

/// `_adaptive_evolution_metrics`; all fields are absent when Python returns `{}`.
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct AdaptiveEvolutionMetrics {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub soft_restarts: Option<i64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub immigrants: Option<i64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub minimum_diversity: Option<f64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub final_diversity: Option<f64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub final_stagnation: Option<i64>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct LocalSearchStats {
    pub evaluations: i64,
    pub improvements: i64,
    /// NaN without local search (JSON `null`).
    #[serde(deserialize_with = "nan::de_f64")]
    pub initial_fitness: f64,
    #[serde(deserialize_with = "nan::de_f64")]
    pub final_fitness: f64,
}

// --------------------------------------------------------------------------- terminal value

/// `TerminalValueCurve` – piecewise linear value curve of the remaining energy (also for
/// `TailValueCurve`: the field is declared as `TerminalValueCurve`, Pydantic serializes
/// only these fields).
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct TerminalValueCurve {
    /// Breakpoints of the usable AC energy [Wh].
    #[serde(default, deserialize_with = "nan::de_vec")]
    pub energy_wh: Vec<f64>,
    /// Cumulative value per breakpoint [EUR].
    #[serde(default, deserialize_with = "nan::de_vec")]
    pub value_euro: Vec<f64>,
    /// Tail operation share per breakpoint [EUR]; empty for the proxy curve.
    #[serde(default, deserialize_with = "nan::de_vec")]
    pub operating_value_euro: Vec<f64>,
    /// Continuation value share per breakpoint [EUR]; empty for the proxy curve.
    #[serde(default, deserialize_with = "nan::de_vec")]
    pub continuation_value_euro: Vec<f64>,
    /// Marginal value of the segment starting at each breakpoint [EUR/kWh].
    #[serde(default, deserialize_with = "nan::de_vec")]
    pub marginal_euro_per_kwh: Vec<f64>,
    /// Kink: covered by the remaining load up to here [Wh].
    #[serde(default, deserialize_with = "nan::de_f64")]
    pub residual_energy_wh: f64,
    /// Number of slots the curve is derived from.
    #[serde(default)]
    pub window_slots: i64,
}

/// `TailDiagnostics` – forecast summary of the deterministic tail optimization.
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct TailDiagnostics {
    #[serde(default)]
    pub slots: i64,
    #[serde(default)]
    pub slot_hours: f64,
    #[serde(default)]
    pub soc_grid_points: i64,
    #[serde(default)]
    pub min_import_price_euro_per_kwh: f64,
    #[serde(default)]
    pub max_import_price_euro_per_kwh: f64,
    #[serde(default)]
    pub min_feed_in_tariff_euro_per_kwh: f64,
    #[serde(default)]
    pub max_feed_in_tariff_euro_per_kwh: f64,
    #[serde(default)]
    pub negative_import_price_slots: i64,
    #[serde(default)]
    pub positive_battery_export_slots: i64,
}

/// `TailPlanSlot` – one diagnostic slot of the optimal tail path (never a control command).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct TailPlanSlot {
    pub slot: i64,
    pub hour_from_start: f64,
    pub action: String,
    #[serde(default)]
    pub alternative_action: String,
    #[serde(default, deserialize_with = "nan::de_f64")]
    pub decision_margin_euro: f64,
    #[serde(deserialize_with = "nan::de_f64")]
    pub soc_start_percentage: f64,
    #[serde(deserialize_with = "nan::de_f64")]
    pub soc_end_percentage: f64,
    #[serde(deserialize_with = "nan::de_f64")]
    pub pv_wh: f64,
    #[serde(deserialize_with = "nan::de_f64")]
    pub load_wh: f64,
    #[serde(deserialize_with = "nan::de_f64")]
    pub grid_import_wh: f64,
    #[serde(deserialize_with = "nan::de_f64")]
    pub grid_export_wh: f64,
    #[serde(deserialize_with = "nan::de_f64")]
    pub battery_charge_wh: f64,
    #[serde(deserialize_with = "nan::de_f64")]
    pub battery_discharge_wh: f64,
    #[serde(deserialize_with = "nan::de_f64")]
    pub import_price_euro_per_kwh: f64,
    #[serde(deserialize_with = "nan::de_f64")]
    pub feed_in_tariff_euro_per_kwh: f64,
    #[serde(deserialize_with = "nan::de_f64")]
    pub slot_value_euro: f64,
    #[serde(deserialize_with = "nan::de_f64")]
    pub remaining_value_euro: f64,
    #[serde(deserialize_with = "nan::de_f64")]
    pub ac_charge_factor: f64,
    pub dc_charge_allowed: i64,
    pub discharge_allowed: i64,
    #[serde(deserialize_with = "nan::de_f64")]
    pub battery_grid_export_factor: f64,
}

/// `TerminalValueResult` – what the optimizer credited for the remaining energy.
///
/// Note: Python declares the first four fields as `float = 0` (integer default). If
/// the run does not set them, Python writes `0` instead of `0.0`. The GENETIC run always sets them; here
/// they are always `f64`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct TerminalValueResult {
    #[serde(default)]
    pub control_horizon_hours: f64,
    #[serde(default)]
    pub requested_tail_hours: f64,
    #[serde(default)]
    pub effective_tail_hours: f64,
    #[serde(default)]
    pub tail_end_hour: f64,
    /// `FIXED` or `AUTO`.
    #[serde(default = "default_continuation_mode")]
    pub continuation_mode: String,
    /// `TAIL`, `AUTO` or `FIXED`.
    pub mode: String,
    /// Usable AC energy in the battery at the end of the horizon [Wh].
    #[serde(default, deserialize_with = "nan::de_f64")]
    pub battery_energy_wh: f64,
    /// Credit to the balance [EUR].
    #[serde(default, deserialize_with = "nan::de_f64")]
    pub credited_euro: f64,
    #[serde(default, deserialize_with = "nan::de_f64")]
    pub tail_operating_euro: f64,
    #[serde(default, deserialize_with = "nan::de_f64")]
    pub continuation_value_euro: f64,
    /// Combined curve (tail + continuation); `None` in FIXED mode.
    #[serde(default)]
    pub curve: Option<TerminalValueCurve>,
    /// AUTO proxy at the tail end.
    #[serde(default)]
    pub continuation_curve: Option<TerminalValueCurve>,
    #[serde(default)]
    pub tail_diagnostics: Option<TailDiagnostics>,
    #[serde(default)]
    pub tail_plan: Vec<TailPlanSlot>,
    /// Reason for the mode (empty for AUTO/TAIL).
    #[serde(default)]
    pub reason: String,
}

impl GeneticSolution {
    /// Read Python JSON leniently, also with only the old names `eautocharge_hours_float`/
    /// `eauto_obj` (Pydantic `AliasChoices`). The derived serde variant reads the
    /// Python dump directly (there the old names are additional `computed_field`s and are
    /// ignored), but knows no aliases, because serde would report "duplicate field"
    /// when both names are present.
    pub fn from_value(mut v: Value) -> Result<Self, serde_json::Error> {
        if let Value::Object(m) = &mut v {
            normalize_aliases(m, &[("ev_charge_hours_float", "eautocharge_hours_float"), ("ev_obj", "eauto_obj")]);
        }
        serde_json::from_value(v)
    }
}
