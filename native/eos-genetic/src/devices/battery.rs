//! `devices/genetic/battery.py` – `Battery` (home battery and EV).
//!
//! Computations as in the Python original, line by line; Python's `min`/`max` via
//! [`py_min`]/[`py_max`] (same behaviour with NaN and ±0).

use crate::numpy::{py_max, py_min};
use crate::params::{ElectricVehicleParameters, SolarPanelBatteryParameters};

/// Python `ValueError` in the simulation (fitness 100000).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct SimError;

/// `BATTERY_DEFAULT_CHARGE_RATES`.
pub const BATTERY_DEFAULT_CHARGE_RATES: [f64; 11] = [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0];

/// Constant characteristics of a battery (from the parameters, `_setup`).
#[derive(Debug, Clone)]
pub struct BatterySpec {
    pub device_id: String,
    pub is_ev: bool,
    pub capacity_wh: f64,
    pub initial_soc_percentage: f64,
    pub charging_efficiency: f64,
    pub discharging_efficiency: f64,
    pub levelized_cost_of_storage_kwh: f64,
    /// sorted, unique (`np.unique`).
    pub charge_rates: Vec<f64>,
    /// `_charge_rates_descending()`.
    pub rates_desc: Vec<f64>,
    pub min_soc_percentage: f64,
    pub max_soc_percentage: f64,
    pub max_charge_power_w: f64,
    pub min_soc_wh: f64,
    pub max_soc_wh: f64,
    pub slot_duration_h: f64,
}

fn unique_sorted(rates: &[f64]) -> Vec<f64> {
    let mut v = rates.to_vec();
    // np.unique sorts (NaN last) and removes duplicates.
    v.sort_by(|a, b| a.partial_cmp(b).unwrap_or_else(|| a.is_nan().cmp(&b.is_nan())));
    v.dedup_by(|a, b| a == b);
    v
}

impl BatterySpec {
    #[allow(clippy::too_many_arguments)]
    fn build(
        device_id: &str,
        is_ev: bool,
        capacity_wh: i64,
        initial_soc: i64,
        charging_efficiency: f64,
        discharging_efficiency: f64,
        lcos: f64,
        charge_rates: Option<&Vec<f64>>,
        min_soc: i64,
        max_soc: i64,
        max_charge_power_w: Option<f64>,
        slot_duration_h: f64,
    ) -> BatterySpec {
        let capacity = capacity_wh as f64;
        let rates = match charge_rates {
            Some(r) if !r.is_empty() => unique_sorted(r),
            _ => BATTERY_DEFAULT_CHARGE_RATES.to_vec(),
        };
        let rates_desc: Vec<f64> = rates.iter().rev().copied().collect();
        let min_soc_percentage = if is_ev { 0.0 } else { min_soc as f64 };
        let max_soc_percentage = max_soc as f64;
        let max_charge_power_w = max_charge_power_w.unwrap_or(capacity);
        BatterySpec {
            device_id: device_id.to_string(),
            is_ev,
            capacity_wh: capacity,
            initial_soc_percentage: initial_soc as f64,
            charging_efficiency,
            discharging_efficiency,
            levelized_cost_of_storage_kwh: if is_ev { 0.0 } else { lcos },
            charge_rates: rates,
            rates_desc,
            min_soc_percentage,
            max_soc_percentage,
            max_charge_power_w,
            min_soc_wh: (min_soc_percentage / 100.0) * capacity,
            max_soc_wh: (max_soc_percentage / 100.0) * capacity,
            slot_duration_h,
        }
    }

    pub fn from_pv(p: &SolarPanelBatteryParameters, slot_duration_h: f64) -> BatterySpec {
        BatterySpec::build(
            &p.device_id,
            false,
            p.capacity_wh,
            p.initial_soc_percentage,
            p.charging_efficiency,
            p.discharging_efficiency,
            p.levelized_cost_of_storage_kwh,
            p.charge_rates.as_ref(),
            p.min_soc_percentage,
            p.max_soc_percentage,
            p.max_charge_power_w,
            slot_duration_h,
        )
    }

    pub fn from_ev(p: &ElectricVehicleParameters, slot_duration_h: f64) -> BatterySpec {
        BatterySpec::build(
            &p.device_id,
            true,
            p.capacity_wh,
            p.initial_soc_percentage,
            p.charging_efficiency,
            p.discharging_efficiency,
            0.0,
            p.charge_rates.as_ref(),
            p.min_soc_percentage,
            p.max_soc_percentage,
            p.max_charge_power_w,
            slot_duration_h,
        )
    }
}

/// State of a battery during a simulation.
#[derive(Debug, Clone)]
pub struct Battery {
    pub s: BatterySpec,
    pub soc_wh: f64,
    pub discharged: Vec<f64>,
    pub charged: Vec<f64>,
    pub limit: Vec<f64>,
    pub charge_array: Vec<f64>,
    pub discharge_array: Vec<f64>,
}

impl Battery {
    /// `Battery(parameters, prediction_hours=n, slot_duration_h)`.
    pub fn new(s: BatterySpec, n: usize) -> Battery {
        let soc_wh = (s.initial_soc_percentage / 100.0) * s.capacity_wh;
        Battery {
            s,
            soc_wh,
            discharged: vec![0.0; n],
            charged: vec![0.0; n],
            limit: vec![f64::INFINITY; n],
            charge_array: vec![0.0; n],
            discharge_array: vec![0.0; n],
        }
    }

    pub fn len(&self) -> usize {
        self.charge_array.len()
    }

    pub fn is_empty(&self) -> bool {
        self.charge_array.is_empty()
    }

    /// `reset()`.
    pub fn reset(&mut self) {
        let soc = (self.s.initial_soc_percentage / 100.0) * self.s.capacity_wh;
        self.soc_wh = py_min(soc, self.s.max_soc_wh);
        self.discharged.iter_mut().for_each(|v| *v = 0.0);
        self.charged.iter_mut().for_each(|v| *v = 0.0);
        self.limit.iter_mut().for_each(|v| *v = f64::INFINITY);
        self.charge_array.iter_mut().for_each(|v| *v = 0.0);
        self.discharge_array.iter_mut().for_each(|v| *v = 0.0);
    }

    #[inline]
    pub fn current_soc_percentage(&self) -> f64 {
        (self.soc_wh / self.s.capacity_wh) * 100.0
    }

    #[inline]
    pub fn limit_slot_charge(&mut self, hour: usize, raw_wh: f64) {
        self.limit[hour] = py_min(self.limit[hour], py_max(raw_wh, 0.0));
    }

    #[inline]
    pub fn rated_discharge_energy_wh(&self) -> f64 {
        self.s.max_charge_power_w * self.s.slot_duration_h * self.s.discharging_efficiency
    }

    #[inline]
    pub fn remaining_discharge_energy_wh(&self, hour: usize) -> f64 {
        let raw_power_budget_wh = self.s.max_charge_power_w * self.s.slot_duration_h;
        let raw_power_remaining_wh = py_max(raw_power_budget_wh - self.discharged[hour], 0.0);
        let raw_soc_available_wh = py_max(self.soc_wh - self.s.min_soc_wh, 0.0);
        py_min(raw_power_remaining_wh, raw_soc_available_wh) * self.s.discharging_efficiency
    }

    /// `discharged_energy_wh(hour)`.
    #[inline]
    pub fn discharged_energy_wh(&self, hour: usize) -> f64 {
        self.discharged[hour] * self.s.discharging_efficiency
    }

    /// `current_energy_content()`.
    #[inline]
    pub fn current_energy_content(&self) -> f64 {
        let usable = (self.soc_wh - self.s.min_soc_wh) * self.s.discharging_efficiency;
        py_max(usable, 0.0)
    }

    /// `charge_energy(wh, hour, charge_factor)`.
    pub fn charge_energy(&mut self, wh: Option<f64>, hour: usize, charge_factor: f64) -> Result<(f64, f64), SimError> {
        if self.charge_array[hour] == 0.0 {
            return Ok((0.0, 0.0));
        }
        let soc = self.soc_wh;
        let max_per_slot = py_max(
            py_min(self.s.max_charge_power_w * self.s.slot_duration_h, self.limit[hour]) - self.charged[hour],
            0.0,
        );
        let eff = self.s.charging_efficiency;
        let mut raw_request;
        let raw_charge;
        match wh {
            Some(w) if charge_factor == 0.0 => {
                raw_request = w;
                raw_charge = py_max(self.s.max_soc_wh - soc, 0.0) / eff;
            }
            None if charge_factor > 0.0 => {
                raw_request = max_per_slot * charge_factor;
                raw_charge = py_max(self.s.max_soc_wh - soc, 0.0) / eff;
                if self.s.is_ev {
                    if raw_charge <= 0.0 {
                        self.charge_array[hour] = 0.0;
                        return Ok((0.0, 0.0));
                    }
                } else if raw_request > raw_charge {
                    let requested = charge_factor;
                    for k in 0..self.s.rates_desc.len() {
                        let lower = self.s.rates_desc[k];
                        if lower >= requested {
                            continue;
                        }
                        raw_request = max_per_slot * lower;
                        if raw_request <= raw_charge {
                            self.charge_array[hour] = lower;
                            break;
                        }
                    }
                    if raw_request > raw_charge {
                        self.charge_array[hour] = 0.0;
                        return Ok((0.0, 0.0));
                    }
                }
            }
            _ => return Err(SimError),
        }
        let max_raw = py_min(raw_charge, max_per_slot);
        let raw_input = if raw_request < max_raw { raw_request } else { max_raw };
        let stored = raw_input * eff;
        let new_soc = soc + stored;
        if new_soc > self.s.capacity_wh {
            return Err(SimError);
        }
        self.soc_wh = new_soc;
        self.charged[hour] += raw_input;
        Ok((stored, raw_input - stored))
    }
}
