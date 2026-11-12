//! `devices/genetic/inverter.py` – `Inverter` with efficiency curve and AC charging; plus `devicesabc.interpolate_efficiency_curve` and
//! `devicesabc.solve_ac_for_dc_energy`.

use super::battery::{Battery, SimError};
use crate::numpy::{bisect_left, py_max, py_min};
use crate::params::InverterParameters;
use crate::selfconsumption::SelfConsumption;

/// `devicesabc.interpolate_efficiency_curve`.
pub fn interpolate_efficiency_curve(fx: &[f64], fy: &[f64], load_fraction: f64) -> f64 {
    let (mut first_fraction, mut first_efficiency) = (fx[0], fy[0]);
    if load_fraction <= first_fraction {
        return first_efficiency;
    }
    for k in 1..fx.len() {
        let (fraction, efficiency) = (fx[k], fy[k]);
        if load_fraction <= fraction {
            return first_efficiency
                + (load_fraction - first_fraction) / (fraction - first_fraction) * (efficiency - first_efficiency);
        }
        first_fraction = fraction;
        first_efficiency = efficiency;
    }
    first_efficiency
}

/// `devicesabc.solve_ac_for_dc_energy`.
pub fn solve_ac_for_dc_energy(fx: &[f64], fy: &[f64], dc_wh: f64, rated_wh: f64, upper_ac_wh: f64) -> f64 {
    if dc_wh <= 0.0 || rated_wh <= 0.0 || upper_ac_wh <= 0.0 {
        return 0.0;
    }
    let tolerance = 1e-9;
    let mut best = -1.0f64;
    let consider = |best: &mut f64, ac_wh: f64, low: f64, high: f64| {
        let fraction = ac_wh / rated_wh;
        if low - tolerance <= fraction
            && fraction <= high + tolerance
            && 0.0 < ac_wh
            && ac_wh <= upper_ac_wh * (1.0 + tolerance)
            && ac_wh > *best
        {
            *best = ac_wh;
        }
    };
    let n = fx.len();
    consider(&mut best, dc_wh * fy[0], f64::NEG_INFINITY, fx[0]);
    consider(&mut best, dc_wh * fy[n - 1], fx[n - 1], f64::INFINITY);
    for k in 0..n - 1 {
        let (x0, e0, x1, e1) = (fx[k], fy[k], fx[k + 1], fy[k + 1]);
        let slope = (e1 - e0) / (x1 - x0);
        let denominator = 1.0 - dc_wh * slope / rated_wh;
        if denominator.abs() < 1e-12 {
            continue;
        }
        consider(&mut best, dc_wh * (e0 - slope * x0) / denominator, x0, x1);
    }
    if best >= 0.0 {
        return py_min(best, upper_ac_wh);
    }
    let mut ac_wh = py_min(upper_ac_wh, dc_wh);
    for _ in 0..50 {
        let next_ac_wh = py_min(dc_wh * interpolate_efficiency_curve(fx, fy, ac_wh / rated_wh), upper_ac_wh);
        if (next_ac_wh - ac_wh).abs() <= tolerance {
            break;
        }
        ac_wh = next_ac_wh;
    }
    ac_wh
}

/// Constant characteristics of the inverter (`_setup`).
#[derive(Debug, Clone)]
pub struct Inverter {
    pub device_id: String,
    pub battery_id: Option<String>,
    pub slot_duration_h: f64,
    /// Energy per slot [Wh] = max_power_wh × slot duration.
    pub max_power_wh: f64,
    pub dc_to_ac_efficiency: f64,
    pub ac_to_dc_efficiency: f64,
    pub curve_x: Vec<f64>,
    pub curve_y: Vec<f64>,
    pub has_curve: bool,
    pub reference_dc_to_ac_efficiency: f64,
    pub max_ac_charge_power_w: Option<f64>,
    pub ac_charge_limits_total_charge: bool,
}

impl Inverter {
    pub fn new(p: &InverterParameters, slot_duration_h: f64) -> Inverter {
        let (curve_x, curve_y, has_curve) = match &p.dc_to_ac_efficiency_curve {
            Some(c) => (c.iter().map(|x| x.0).collect(), c.iter().map(|x| x.1).collect(), true),
            None => (Vec::new(), Vec::new(), false),
        };
        let mut inv = Inverter {
            device_id: p.device_id.clone(),
            battery_id: p.battery_id.clone(),
            slot_duration_h,
            max_power_wh: p.max_power_wh * slot_duration_h,
            dc_to_ac_efficiency: p.dc_to_ac_efficiency,
            ac_to_dc_efficiency: p.ac_to_dc_efficiency,
            curve_x,
            curve_y,
            has_curve,
            reference_dc_to_ac_efficiency: 0.0,
            max_ac_charge_power_w: p.max_ac_charge_power_w,
            ac_charge_limits_total_charge: p.ac_charge_limits_total_charge,
        };
        inv.reference_dc_to_ac_efficiency =
            inv.dc_to_ac_efficiency_at(p.dc_to_ac_efficiency_reference_load_fraction * inv.max_power_wh);
        inv
    }

    /// Whether AC charging is possible at all (`ac_charging_possible` in the simulation).
    pub fn ac_charging_possible(&self) -> bool {
        self.ac_to_dc_efficiency > 0.0 && self.max_ac_charge_power_w.is_none_or(|m| m > 0.0)
    }

    /// `dc_to_ac_efficiency_at(ac_wh)`.
    #[inline]
    pub fn dc_to_ac_efficiency_at(&self, ac_wh: f64) -> f64 {
        if !self.has_curve {
            return self.dc_to_ac_efficiency;
        }
        let load_fraction = if self.max_power_wh > 0.0 { ac_wh / self.max_power_wh } else { 0.0 };
        if load_fraction.is_nan() {
            return interpolate_efficiency_curve(&self.curve_x, &self.curve_y, load_fraction);
        }
        let fx = &self.curve_x;
        let fy = &self.curve_y;
        let index = bisect_left(fx, load_fraction);
        if index == 0 {
            return fy[0];
        }
        if index == fx.len() {
            return fy[fy.len() - 1];
        }
        let lower_fraction = fx[index - 1];
        let lower_efficiency = fy[index - 1];
        lower_efficiency
            + (load_fraction - lower_fraction) / (fx[index] - lower_fraction) * (fy[index] - lower_efficiency)
    }

    /// `ac_charge_factor(factor)`.
    #[inline]
    pub fn ac_charge_factor(&self, bat: Option<&Battery>, factor: f64) -> f64 {
        let bat = match bat {
            Some(b) => b,
            None => return 0.0,
        };
        if factor <= 0.0 || self.ac_to_dc_efficiency <= 0.0 {
            return 0.0;
        }
        let mut factor = factor;
        if let Some(max_ac) = self.max_ac_charge_power_w {
            if bat.s.max_charge_power_w > 0.0 {
                factor = py_min(factor, max_ac * self.ac_to_dc_efficiency / bat.s.max_charge_power_w);
            }
        }
        py_max(factor, 0.0)
    }

    /// `begin_ac_charge_slot(hour, factor)`.
    #[inline]
    pub fn begin_ac_charge_slot(&self, bat: Option<&mut Battery>, hour: usize, factor: f64) {
        if self.ac_charge_limits_total_charge && factor > 0.0 {
            if let Some(b) = bat {
                let raw = b.s.max_charge_power_w * self.slot_duration_h * factor;
                b.limit_slot_charge(hour, raw);
            }
        }
    }

    /// `charge_battery_from_grid(hour, factor)` → (AC energy, losses).
    pub fn charge_battery_from_grid(
        &self,
        bat: &mut Battery,
        hour: usize,
        factor: f64,
    ) -> Result<(f64, f64), SimError> {
        if factor <= 0.0 {
            return Ok((0.0, 0.0));
        }
        let (stored, battery_losses) = if self.ac_charge_limits_total_charge {
            bat.charge_energy(Some(bat.s.max_charge_power_w * self.slot_duration_h * factor), hour, 0.0)?
        } else {
            bat.charge_energy(None, hour, factor)?
        };
        let dc_energy = stored + battery_losses;
        let ac_energy = dc_energy / self.ac_to_dc_efficiency;
        Ok((ac_energy, battery_losses + (ac_energy - dc_energy)))
    }

    /// `_discharge_battery_to_ac(requested_ac_wh, hour)` (single battery).
    pub fn discharge_battery_to_ac(&self, bat: &mut Battery, requested_ac_wh: f64, hour: usize) -> (f64, f64) {
        if requested_ac_wh <= 0.0 {
            return (0.0, 0.0);
        }
        if bat.discharge_array[hour] == 0.0 {
            return (0.0, 0.0);
        }
        let dc_to_ac_efficiency =
            if !self.has_curve { self.dc_to_ac_efficiency } else { self.dc_to_ac_efficiency_at(requested_ac_wh) };
        let dc_request = requested_ac_wh / dc_to_ac_efficiency;
        let soc_wh = bat.soc_wh;
        let min_soc_wh = bat.s.min_soc_wh;
        let discharging_efficiency = bat.s.discharging_efficiency;
        let raw_available_wh = py_max(soc_wh - min_soc_wh, 0.0);
        if raw_available_wh == 0.0 {
            return (0.0, 0.0);
        }
        let max_raw_wh = py_max(bat.s.max_charge_power_w * bat.s.slot_duration_h - bat.discharged[hour], 0.0);
        let max_deliverable_wh = py_min(raw_available_wh, max_raw_wh) * discharging_efficiency;
        let battery_discharge_dc = py_min(dc_request, max_deliverable_wh);
        let raw_used_wh = battery_discharge_dc / discharging_efficiency;
        let soc_after = soc_wh - raw_used_wh;
        bat.soc_wh = py_max(soc_after, min_soc_wh);
        bat.discharged[hour] += raw_used_wh;
        let discharge_losses = raw_used_wh - battery_discharge_dc;
        let battery_discharge_ac = if self.has_curve && battery_discharge_dc < dc_request * (1.0 - 1e-12) {
            solve_ac_for_dc_energy(
                &self.curve_x,
                &self.curve_y,
                battery_discharge_dc,
                self.max_power_wh,
                requested_ac_wh,
            )
        } else {
            battery_discharge_dc * dc_to_ac_efficiency
        };
        let inverter_discharge_losses = battery_discharge_dc - battery_discharge_ac;
        (battery_discharge_ac, discharge_losses + inverter_discharge_losses)
    }

    /// `process_energy(...)` → (feed-in, import, losses, self-consumption).
    #[allow(clippy::too_many_arguments)]
    pub fn process_energy(
        &self,
        mut bat: Option<&mut Battery>,
        sc: &mut SelfConsumption,
        generation: f64,
        consumption: f64,
        hour: usize,
        allow_battery_grid_export: bool,
        battery_grid_export_factor: f64,
    ) -> Result<(f64, f64, f64, f64), SimError> {
        let mut losses = 0.0;
        let mut grid_export = 0.0;
        let mut generation = generation;
        if generation < 0.0 {
            generation = 0.0;
        }
        let mut consumption = consumption;
        if consumption < 0.0 {
            consumption = 0.0;
        }
        let max_power_wh = self.max_power_wh;
        let mut direct_pv_energy = if generation > 0.0 && consumption > 0.0 {
            let slot_duration_h = self.slot_duration_h;
            let expected = sc.expected_direct_consumption(consumption / slot_duration_h, generation / slot_duration_h);
            expected * slot_duration_h
        } else {
            0.0
        };
        if direct_pv_energy < 0.0 {
            direct_pv_energy = 0.0;
        }
        if generation < direct_pv_energy {
            direct_pv_energy = generation;
        }
        if consumption < direct_pv_energy {
            direct_pv_energy = consumption;
        }
        if max_power_wh < direct_pv_energy {
            direct_pv_energy = max_power_wh;
        }
        let mut remaining_load = consumption - direct_pv_energy;
        if remaining_load < 0.0 {
            remaining_load = 0.0;
        }
        let mut pv_surplus = generation - direct_pv_energy;
        if pv_surplus < 0.0 {
            pv_surplus = 0.0;
        }
        let mut remaining_cap = max_power_wh - direct_pv_energy;
        if remaining_cap < 0.0 {
            remaining_cap = 0.0;
        }
        let mut battery_discharge_ac = 0.0;
        if remaining_load > 0.0 && remaining_cap > 0.0 {
            if let Some(b) = bat.as_deref_mut() {
                let requested = if remaining_cap < remaining_load { remaining_cap } else { remaining_load };
                let (ac, l) = self.discharge_battery_to_ac(b, requested, hour);
                battery_discharge_ac = ac;
                remaining_load -= ac;
                if remaining_load < 0.0 {
                    remaining_load = 0.0;
                }
                remaining_cap -= ac;
                if remaining_cap < 0.0 {
                    remaining_cap = 0.0;
                }
                losses += l;
            }
        }
        let grid_import = remaining_load;
        if pv_surplus != 0.0 {
            let mut remaining_surplus = pv_surplus;
            if remaining_surplus > 0.0 {
                if let Some(b) = bat.as_deref_mut() {
                    let (charged, charge_losses) = b.charge_energy(Some(remaining_surplus), hour, 0.0)?;
                    remaining_surplus = remaining_surplus - charged - charge_losses;
                    if remaining_surplus < 0.0 {
                        remaining_surplus = 0.0;
                    }
                    losses += charge_losses;
                }
            }
            let pv_grid_export = if remaining_cap < remaining_surplus { remaining_cap } else { remaining_surplus };
            grid_export += pv_grid_export;
            remaining_cap -= pv_grid_export;
            if remaining_cap < 0.0 {
                remaining_cap = 0.0;
            }
            let mut curtailed = remaining_surplus - pv_grid_export;
            if curtailed < 0.0 {
                curtailed = 0.0;
            }
            losses += curtailed;
        }
        if allow_battery_grid_export && remaining_cap > 0.0 {
            if let Some(b) = bat.as_deref_mut() {
                let mut export_factor = battery_grid_export_factor;
                if export_factor < 0.0 {
                    export_factor = 0.0;
                }
                if 1.0 < export_factor {
                    export_factor = 1.0;
                }
                let remaining_battery_dc = b.remaining_discharge_energy_wh(hour);
                let remaining_battery_ac = remaining_battery_dc * self.dc_to_ac_efficiency_at(remaining_battery_dc);
                let rated_export_dc = b.rated_discharge_energy_wh() * export_factor;
                let rated_export_ac = rated_export_dc * self.dc_to_ac_efficiency_at(rated_export_dc);
                let mut export_capacity = remaining_cap;
                if remaining_battery_ac < export_capacity {
                    export_capacity = remaining_battery_ac;
                }
                if rated_export_ac < export_capacity {
                    export_capacity = rated_export_ac;
                }
                let (eac, el) = self.discharge_battery_to_ac(b, export_capacity, hour);
                grid_export += eac;
                losses += el;
            }
        }
        Ok((grid_export, grid_import, losses, direct_pv_energy + battery_discharge_ac))
    }
}
