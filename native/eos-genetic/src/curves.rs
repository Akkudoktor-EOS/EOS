//! Terminal value of the battery energy: `optimization/genetic/terminalvalue.py`
//! (`build_terminal_value_curve`, `trailing_window`) and `tailvalue.py`
//! (`build_tail_value_curve`, `_simulate_action`, `TailValueCurve.diagnostic_plan`).

use crate::devices::battery::{Battery, BatterySpec, SimError};
use crate::devices::inverter::Inverter;
use crate::numpy::{self, py_max, py_min};
use crate::selfconsumption::SelfConsumption;

/// `TerminalValueCurve` or `TailValueCurve` (data).
#[derive(Debug, Clone, Default)]
pub struct ValueCurve {
    pub energy_wh: Vec<f64>,
    pub value_euro: Vec<f64>,
    pub operating_value_euro: Vec<f64>,
    pub continuation_value_euro: Vec<f64>,
    pub marginal_euro_per_kwh: Vec<f64>,
    pub residual_energy_wh: f64,
    pub window_slots: usize,
    /// TAIL curve (otherwise AUTO approximation).
    pub is_tail: bool,
}

impl ValueCurve {
    /// `TerminalValueCurve.value` / `TailValueCurve.value`.
    pub fn value(&self, energy_wh: f64, fma: bool) -> f64 {
        if self.energy_wh.is_empty() {
            return 0.0;
        }
        if !self.is_tail && energy_wh <= 0.0 {
            return 0.0;
        }
        numpy::interp(energy_wh, &self.energy_wh, &self.value_euro, fma)
    }

    /// `TailValueCurve.component_values`.
    pub fn component_values(&self, energy_wh: f64, fma: bool) -> (f64, f64) {
        if self.energy_wh.is_empty() {
            return (0.0, 0.0);
        }
        (
            numpy::interp(energy_wh, &self.energy_wh, &self.operating_value_euro, fma),
            numpy::interp(energy_wh, &self.energy_wh, &self.continuation_value_euro, fma),
        )
    }
}

/// `trailing_window(values, end_slot, window_slots)`.
pub fn trailing_window(values: &[f64], end_slot: usize, window_slots: usize) -> Vec<f64> {
    let end = end_slot.min(values.len());
    let start = end.saturating_sub(window_slots);
    if end <= start {
        return Vec::new();
    }
    values[start..end].to_vec()
}

/// `build_terminal_value_curve`.
#[allow(clippy::too_many_arguments)]
pub fn build_terminal_value_curve(
    prices: &[f64],
    load: &[f64],
    pv: &[f64],
    feed_in: &[f64],
    max_energy_wh: f64,
    lcos_euro_per_kwh: f64,
    dc_to_ac_efficiency: f64,
    grid_export_allowed: bool,
) -> ValueCurve {
    let window = prices.len().min(load.len()).min(pv.len());
    if window == 0 || max_energy_wh <= 0.0 {
        return ValueCurve::default();
    }
    // np.maximum(load - pv, 0.0)
    let residual: Vec<f64> = (0..window)
        .map(|i| {
            let d = load[i] - pv[i];
            if d.is_nan() || d >= 0.0 { d } else { 0.0 }
        })
        .collect();
    let lcos_per_wh_ac = (lcos_euro_per_kwh / 1000.0) / py_max(dc_to_ac_efficiency, 1e-9);
    let neg: Vec<f64> = prices[..window].iter().map(|p| -p).collect();
    let order = numpy::argsort_quicksort(&neg);
    let mut energy_points = vec![0.0f64];
    let mut value_points = vec![0.0f64];
    let mut marginals: Vec<f64> = Vec::new();
    let mut cumulative_energy = 0.0f64;
    let mut cumulative_value = 0.0f64;
    for index in order {
        let mut slot_energy = residual[index];
        if slot_energy <= 0.0 {
            continue;
        }
        let marginal = py_max(prices[index] - lcos_per_wh_ac, 0.0);
        if marginal <= 0.0 {
            continue;
        }
        slot_energy = py_min(slot_energy, max_energy_wh - cumulative_energy);
        if slot_energy <= 0.0 {
            break;
        }
        cumulative_energy += slot_energy;
        cumulative_value += slot_energy * marginal;
        energy_points.push(cumulative_energy);
        value_points.push(cumulative_value);
        marginals.push(marginal * 1000.0);
    }
    let residual_energy_wh = cumulative_energy;
    if grid_export_allowed && cumulative_energy < max_energy_wh {
        let positive: Vec<f64> = feed_in[..window.min(feed_in.len())].iter().copied().filter(|v| *v > 0.0).collect();
        let med = if positive.is_empty() { 0.0 } else { numpy::median(&positive) };
        let export_marginal = py_max(med - lcos_per_wh_ac, 0.0);
        if export_marginal > 0.0 {
            let remaining = max_energy_wh - cumulative_energy;
            cumulative_energy += remaining;
            cumulative_value += remaining * export_marginal;
            energy_points.push(cumulative_energy);
            value_points.push(cumulative_value);
            marginals.push(export_marginal * 1000.0);
        }
    }
    if energy_points.len() <= 1 {
        return ValueCurve { window_slots: window, ..ValueCurve::default() };
    }
    ValueCurve {
        energy_wh: energy_points,
        value_euro: value_points,
        operating_value_euro: Vec::new(),
        continuation_value_euro: Vec::new(),
        marginal_euro_per_kwh: marginals,
        residual_energy_wh,
        window_slots: window,
        is_tail: false,
    }
}

/// One tail action: (DC charge, discharge, AC rate, export rate).
pub type TailAction = (i32, i32, f64, f64);

pub fn action_name(a: &TailAction) -> &'static str {
    let (dc, discharge, ac_rate, export_rate) = *a;
    if export_rate > 0.0 {
        return "BATTERY_EXPORT";
    }
    if ac_rate > 0.0 {
        return "GRID_CHARGE";
    }
    if dc != 0 && discharge != 0 {
        return "SELF_CONSUMPTION";
    }
    if dc != 0 {
        return "PV_CHARGE_ONLY";
    }
    if discharge != 0 {
        return "DISCHARGE_ONLY";
    }
    "HOLD"
}

/// Result of `_simulate_action`.
#[derive(Debug, Clone, Copy, Default)]
pub struct ActionResult {
    pub next_state_wh: f64,
    pub reward_euro: f64,
    pub grid_export_wh: f64,
    pub grid_import_wh: f64,
    pub battery_charge_wh: f64,
    pub battery_discharge_wh: f64,
    pub losses_wh: f64,
    pub ac_grid_charge_wh: f64,
}

/// `_simulate_action` (battery with a single slot).
#[allow(clippy::too_many_arguments)]
pub fn simulate_action(
    bat: &mut Battery,
    inv: &Inverter,
    sc: &mut SelfConsumption,
    energy_wh: f64,
    action: &TailAction,
    price: f64,
    load: f64,
    pv: f64,
    tariff: f64,
    direct_marketing: bool,
    integer_charge_array: bool,
) -> Result<ActionResult, SimError> {
    // `integer_charge_array`: the battery of `TailValueCurve.diagnostic_plan` keeps the integer
    // `charge_array` of `Battery.__init__` (`np.full(n, 0)`), so NumPy truncates a fractional
    // AC rate written into it (0.25 -> 0). The tail solve itself uses a float array.
    let store = |x: f64| if integer_charge_array { x.trunc() } else { x };
    let (dc, discharge, ac_rate, export) = *action;
    bat.soc_wh = energy_wh;
    bat.charged[0] = 0.0;
    bat.discharged[0] = 0.0;
    bat.limit[0] = f64::INFINITY;
    let ac_enabled = inv.ac_charging_possible();
    bat.charge_array[0] = store(if ac_rate > 0.0 && ac_enabled { ac_rate } else { f64::from(dc) });
    bat.discharge_array[0] = if export == 0.0 || tariff > 0.0 { f64::from(discharge) } else { 0.0 };
    let rate = inv.ac_charge_factor(Some(bat), ac_rate);
    if rate > 0.0 {
        inv.begin_ac_charge_slot(Some(bat), 0, rate);
    }
    let allow = direct_marketing && export > 0.0 && tariff > 0.0;
    let (mut sold, mut bought, mut losses, _) = inv.process_energy(Some(bat), sc, pv, load, 0, allow, export)?;
    let mut ac_grid_charge_wh = 0.0;
    if ac_rate > 0.0 && inv.ac_to_dc_efficiency > 0.0 {
        bat.charge_array[0] = store(rate);
        if rate > 0.0 {
            let (a, l) = inv.charge_battery_from_grid(bat, 0, rate)?;
            ac_grid_charge_wh = a;
            bought += a;
            losses += l;
        }
    }
    if direct_marketing && tariff < 0.0 {
        sold = 0.0;
    }
    let discharged_wh = bat.discharged_energy_wh(0);
    let reward = sold * tariff - bought * price - (discharged_wh * bat.s.levelized_cost_of_storage_kwh / 1000.0);
    Ok(ActionResult {
        next_state_wh: bat.soc_wh,
        reward_euro: reward,
        grid_export_wh: sold,
        grid_import_wh: bought,
        battery_charge_wh: bat.charged[0] * bat.s.charging_efficiency,
        battery_discharge_wh: discharged_wh,
        losses_wh: losses,
        ac_grid_charge_wh,
    })
}

/// Everything `diagnostic_plan` needs later (`_trace_context`).
#[derive(Debug, Clone, Default)]
pub struct TailTrace {
    pub states: Vec<f64>,
    pub future_values: Vec<Vec<f64>>,
    pub actions: Vec<TailAction>,
    pub prices: Vec<f64>,
    pub load: Vec<f64>,
    pub pv: Vec<f64>,
    pub tariffs: Vec<f64>,
    pub direct_marketing: bool,
}

/// One row of `diagnostic_plan` (`TailPlanSlot`).
#[derive(Debug, Clone)]
pub struct TailPlanSlot {
    pub slot: usize,
    pub hour_from_start: f64,
    pub action: String,
    pub alternative_action: String,
    pub decision_margin_euro: f64,
    pub soc_start_percentage: f64,
    pub soc_end_percentage: f64,
    pub pv_wh: f64,
    pub load_wh: f64,
    pub grid_import_wh: f64,
    pub grid_export_wh: f64,
    pub battery_charge_wh: f64,
    pub battery_discharge_wh: f64,
    pub import_price_euro_per_kwh: f64,
    pub feed_in_tariff_euro_per_kwh: f64,
    pub slot_value_euro: f64,
    pub remaining_value_euro: f64,
    pub ac_charge_factor: f64,
    pub dc_charge_allowed: i32,
    pub discharge_allowed: i32,
    pub battery_grid_export_factor: f64,
}

/// `build_tail_value_curve`. Returns the curve and the trace for `diagnostic_plan`.
#[allow(clippy::too_many_arguments)]
pub fn build_tail_value_curve(
    battery: &BatterySpec,
    inverter: &Inverter,
    sc: &mut SelfConsumption,
    prices: &[f64],
    load: &[f64],
    pv: &[f64],
    feed_in: &[f64],
    continuation: &ValueCurve,
    charge_rates: &[f64],
    export_rates: &[f64],
    direct_marketing: bool,
    fma: bool,
) -> Result<(ValueCurve, TailTrace), String> {
    let n = prices.len();
    if load.len() != n || pv.len() != n || feed_in.len() != n {
        return Err("Tail forecasts must have equal lengths and contain only finite values".into());
    }
    if [prices, load, pv, feed_in].iter().any(|a| a.iter().any(|v| !v.is_finite())) {
        return Err("Tail forecasts must have equal lengths and contain only finite values".into());
    }
    let grid_points = 101usize;
    let mut bat = Battery::new(battery.clone(), 1);
    let inv = inverter.clone();
    let states = numpy::linspace(bat.s.min_soc_wh, bat.s.max_soc_wh, grid_points);
    let usable: Vec<f64> = states
        .iter()
        .map(|s| ((s - bat.s.min_soc_wh) * bat.s.discharging_efficiency) * inv.reference_dc_to_ac_efficiency)
        .collect();
    let mut continuation_values: Vec<f64> = usable.iter().map(|&e| continuation.value(e, fma)).collect();
    let mut operating_values = vec![0.0f64; states.len()];
    let mut values = continuation_values.clone();
    let mut actions: Vec<TailAction> = vec![(1, 0, 0.0, 0.0), (1, 1, 0.0, 0.0)];
    actions.extend(charge_rates.iter().filter(|r| **r > 0.0).map(|&r| (1, 0, r, 0.0)));
    if direct_marketing {
        actions.push((0, 0, 0.0, 0.0));
        actions.push((0, 1, 0.0, 0.0));
        actions.extend(charge_rates.iter().filter(|r| **r > 0.0).map(|&r| (0, 0, r, 0.0)));
        for dc in [0, 1] {
            actions.extend(export_rates.iter().filter(|r| **r > 0.0).map(|&r| (dc, 1, 0.0, r)));
        }
    }
    let mut future_values: Vec<Vec<f64>> = vec![Vec::new(); n + 1];
    future_values[n] = continuation_values.clone();
    let ns = states.len();
    let mut next_states = vec![0.0f64; ns];
    let mut rewards = vec![0.0f64; ns];
    for slot in (0..n).rev() {
        let (price, ld, p, tariff) = (prices[slot], load[slot], pv[slot], feed_in[slot]);
        let mut best = vec![f64::NEG_INFINITY; ns];
        let mut best_operating = vec![0.0f64; ns];
        let mut best_continuation = vec![0.0f64; ns];
        for action in &actions {
            for (i, &energy) in states.iter().enumerate() {
                let r =
                    simulate_action(&mut bat, &inv, sc, energy, action, price, ld, p, tariff, direct_marketing, false)
                        .map_err(|_| "battery capacity exceeded in the tail".to_string())?;
                rewards[i] = r.reward_euro;
                next_states[i] = r.next_state_wh;
            }
            for i in 0..ns {
                let cand_op = rewards[i] + numpy::interp(next_states[i], &states, &operating_values, fma);
                let cand_cont = numpy::interp(next_states[i], &states, &continuation_values, fma);
                let cand = cand_op + cand_cont;
                if cand > best[i] {
                    best[i] = cand;
                    best_operating[i] = cand_op;
                    best_continuation[i] = cand_cont;
                }
            }
        }
        values = best;
        operating_values = best_operating;
        continuation_values = best_continuation;
        future_values[slot] = values.clone();
    }
    let marginal: Vec<f64> = (0..ns.saturating_sub(1))
        .map(|i| {
            let dv = values[i + 1] - values[i];
            let du = usable[i + 1] - usable[i];
            let den = if du.is_nan() || du >= 1e-9 { du } else { 1e-9 };
            dv / den * 1000.0
        })
        .collect();
    let curve = ValueCurve {
        energy_wh: usable,
        value_euro: values,
        operating_value_euro: operating_values,
        continuation_value_euro: continuation_values,
        marginal_euro_per_kwh: marginal,
        residual_energy_wh: 0.0,
        window_slots: n,
        is_tail: true,
    };
    let trace = TailTrace {
        states,
        future_values,
        actions,
        prices: prices.to_vec(),
        load: load.to_vec(),
        pv: pv.to_vec(),
        tariffs: feed_in.to_vec(),
        direct_marketing,
    };
    Ok((curve, trace))
}

/// `TailValueCurve.diagnostic_plan(energy_wh, control_horizon_hours)`.
pub fn diagnostic_plan(
    trace: &TailTrace,
    battery: &BatterySpec,
    inverter: &Inverter,
    sc: &mut SelfConsumption,
    energy_wh: f64,
    control_horizon_hours: f64,
    fma: bool,
) -> Result<Vec<TailPlanSlot>, SimError> {
    if trace.states.is_empty() {
        return Ok(Vec::new());
    }
    let mut bat = Battery::new(battery.clone(), 1);
    let inv = inverter.clone();
    let slot_h = battery.slot_duration_h;
    let conversion = bat.s.discharging_efficiency * inv.reference_dc_to_ac_efficiency;
    let mut state_wh = bat.s.min_soc_wh + if conversion > 0.0 { energy_wh / conversion } else { 0.0 };
    state_wh = numpy::clip(state_wh, bat.s.min_soc_wh, bat.s.max_soc_wh);
    let mut plan = Vec::new();
    let n = trace.prices.len().min(trace.load.len()).min(trace.pv.len()).min(trace.tariffs.len());
    for slot in 0..n {
        let (price, load, pv, tariff) = (trace.prices[slot], trace.load[slot], trace.pv[slot], trace.tariffs[slot]);
        let mut candidates: Vec<(f64, TailAction, ActionResult, f64)> = Vec::new();
        for action in &trace.actions {
            let r = simulate_action(
                &mut bat,
                &inv,
                sc,
                state_wh,
                action,
                price,
                load,
                pv,
                tariff,
                trace.direct_marketing,
                true,
            )?;
            let remaining = numpy::interp(r.next_state_wh, &trace.states, &trace.future_values[slot + 1], fma);
            candidates.push((r.reward_euro + remaining, *action, r, remaining));
        }
        // sort(key=value, reverse=True), stable
        candidates.sort_by(|a, b| b.0.partial_cmp(&a.0).unwrap_or(std::cmp::Ordering::Equal));
        let (chosen_value, chosen_action, chosen_result, remaining) = candidates[0];
        let alternative = candidates[1..]
            .iter()
            .find(|c| action_name(&c.1) != action_name(&chosen_action))
            .copied()
            .unwrap_or(if candidates.len() > 1 { candidates[1] } else { candidates[0] });
        let (dc, discharge, ac_rate, export_rate) = chosen_action;
        let start_soc = state_wh / bat.s.capacity_wh * 100.0;
        state_wh = chosen_result.next_state_wh;
        plan.push(TailPlanSlot {
            slot,
            hour_from_start: control_horizon_hours + slot as f64 * slot_h,
            action: action_name(&chosen_action).to_string(),
            alternative_action: action_name(&alternative.1).to_string(),
            decision_margin_euro: py_max(chosen_value - alternative.0, 0.0),
            soc_start_percentage: start_soc,
            soc_end_percentage: state_wh / bat.s.capacity_wh * 100.0,
            pv_wh: pv,
            load_wh: load,
            grid_import_wh: chosen_result.grid_import_wh,
            grid_export_wh: chosen_result.grid_export_wh,
            battery_charge_wh: chosen_result.battery_charge_wh,
            battery_discharge_wh: chosen_result.battery_discharge_wh,
            import_price_euro_per_kwh: price * 1000.0,
            feed_in_tariff_euro_per_kwh: tariff * 1000.0,
            slot_value_euro: chosen_result.reward_euro,
            remaining_value_euro: remaining,
            ac_charge_factor: ac_rate,
            dc_charge_allowed: dc,
            discharge_allowed: discharge,
            battery_grid_export_factor: export_rate,
        });
    }
    Ok(plan)
}
