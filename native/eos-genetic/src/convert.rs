//! Result of the GA core ([`CoreSolution`]) → `GeneticSolution` of the output layer, as
//! Python assembles it at the end of `optimize_ems()`.

use chrono::{DateTime, FixedOffset};
use chrono_tz::Tz;
use serde_json::{Map, Value, json};

use crate::curves::{TailPlanSlot as CoreTailPlanSlot, ValueCurve};
use crate::genetic::{CoreSolution, TailDiagnostics as CoreTailDiagnostics, TerminalValueResult as CoreTerminal};
use crate::params::GeneticOptimizationParameters;
use crate::solution::model::{
    AdaptiveEvolutionMetrics, ElectricVehicleResult, FitnessCacheStats, FitnessHistory, GeneticSimulationResult,
    GeneticSolution, LocalSearchStats, OrderedMap, TailDiagnostics, TailPlanSlot, TerminalValueCurve,
    TerminalValueResult,
};

/// pendulum duration as text (serialized `to_duration(f"{h} hours")`), e.g. "3 days 8 hours".
pub fn duration_text_hours(hours: i64) -> String {
    let weeks = hours / 168;
    let days = (hours % 168) / 24;
    let h = hours % 24;
    let mut parts = Vec::new();
    let mut push = |n: i64, one: &str, many: &str| {
        if n > 0 {
            parts.push(format!("{n} {}", if n == 1 { one } else { many }));
        }
    };
    push(weeks, "week", "weeks");
    push(days, "day", "days");
    push(h, "hour", "hours");
    if parts.is_empty() {
        return "0 seconds".into();
    }
    parts.join(" ")
}

/// Without a time window, `HomeAppliance._setup()` writes a placeholder into the parameters
/// (00:00 + `prediction_hours` hours, where Python actually passes the slot count there).
fn placeholder_windows(prediction_slots: usize) -> Value {
    json!({"windows": [{
        "start_time": "00:00:00.000000",
        "duration": duration_text_hours(prediction_slots as i64),
        "end_time": null, "day_of_week": null, "date": null, "month": null, "locale": null, "value": null
    }]})
}

fn final_parameters(p: &GeneticOptimizationParameters, prediction_slots: usize) -> GeneticOptimizationParameters {
    let mut out = p.clone();
    let fill = |a: &mut crate::params::HomeApplianceParameters| {
        if a.time_windows.as_ref().is_none_or(|v| v.is_null()) {
            a.time_windows = Some(placeholder_windows(prediction_slots));
        }
    };
    if let Some(list) = out.home_appliances.as_mut() {
        list.iter_mut().for_each(fill);
    } else if let Some(d) = out.dishwasher.as_mut() {
        fill(d);
    }
    out
}

fn curve(c: &ValueCurve) -> TerminalValueCurve {
    TerminalValueCurve {
        energy_wh: c.energy_wh.clone(),
        value_euro: c.value_euro.clone(),
        operating_value_euro: c.operating_value_euro.clone(),
        continuation_value_euro: c.continuation_value_euro.clone(),
        marginal_euro_per_kwh: c.marginal_euro_per_kwh.clone(),
        residual_energy_wh: c.residual_energy_wh,
        window_slots: c.window_slots as i64,
    }
}

fn diagnostics(d: &CoreTailDiagnostics) -> TailDiagnostics {
    TailDiagnostics {
        slots: d.slots as i64,
        slot_hours: d.slot_hours,
        soc_grid_points: d.soc_grid_points as i64,
        min_import_price_euro_per_kwh: d.min_import_price_euro_per_kwh,
        max_import_price_euro_per_kwh: d.max_import_price_euro_per_kwh,
        min_feed_in_tariff_euro_per_kwh: d.min_feed_in_tariff_euro_per_kwh,
        max_feed_in_tariff_euro_per_kwh: d.max_feed_in_tariff_euro_per_kwh,
        negative_import_price_slots: d.negative_import_price_slots as i64,
        positive_battery_export_slots: d.positive_battery_export_slots as i64,
    }
}

fn plan_slot(s: &CoreTailPlanSlot) -> TailPlanSlot {
    TailPlanSlot {
        slot: s.slot as i64,
        hour_from_start: s.hour_from_start,
        action: s.action.clone(),
        alternative_action: s.alternative_action.clone(),
        decision_margin_euro: s.decision_margin_euro,
        soc_start_percentage: s.soc_start_percentage,
        soc_end_percentage: s.soc_end_percentage,
        pv_wh: s.pv_wh,
        load_wh: s.load_wh,
        grid_import_wh: s.grid_import_wh,
        grid_export_wh: s.grid_export_wh,
        battery_charge_wh: s.battery_charge_wh,
        battery_discharge_wh: s.battery_discharge_wh,
        import_price_euro_per_kwh: s.import_price_euro_per_kwh,
        feed_in_tariff_euro_per_kwh: s.feed_in_tariff_euro_per_kwh,
        slot_value_euro: s.slot_value_euro,
        remaining_value_euro: s.remaining_value_euro,
        ac_charge_factor: s.ac_charge_factor,
        dc_charge_allowed: i64::from(s.dc_charge_allowed),
        discharge_allowed: i64::from(s.discharge_allowed),
        battery_grid_export_factor: s.battery_grid_export_factor,
    }
}

fn terminal(t: &CoreTerminal) -> TerminalValueResult {
    TerminalValueResult {
        control_horizon_hours: t.control_horizon_hours,
        requested_tail_hours: t.requested_tail_hours,
        effective_tail_hours: t.effective_tail_hours,
        tail_end_hour: t.tail_end_hour,
        continuation_mode: t.continuation_mode.clone(),
        mode: t.mode.clone(),
        battery_energy_wh: t.battery_energy_wh,
        credited_euro: t.credited_euro,
        tail_operating_euro: t.tail_operating_euro,
        continuation_value_euro: t.continuation_value_euro,
        curve: t.curve.as_ref().map(curve),
        continuation_curve: t.continuation_curve.as_ref().map(curve),
        tail_diagnostics: t.tail_diagnostics.as_ref().map(diagnostics),
        tail_plan: t.tail_plan.iter().map(plan_slot).collect(),
        reason: t.reason.clone(),
    }
}

fn fixed(dt: &DateTime<Tz>, tz: Tz) -> DateTime<FixedOffset> {
    dt.with_timezone(&tz).fixed_offset()
}

/// `GeneticSolution(**{...})` at the end of `optimize_ems()`.
pub fn genetic_solution(c: &CoreSolution, tz: Tz) -> GeneticSolution {
    let r = &c.result;
    let n = r.load.len();
    let has_appliances = !c.home_appliance_energy_wh.is_empty();
    let result = GeneticSimulationResult {
        load_wh_per_hour: r.load.clone(),
        ev_soc_per_hour: r.ev_soc.clone(),
        revenue_per_hour: r.revenue.clone(),
        total_losses: r.total_losses,
        total_balance: r.total_cost - r.total_revenue,
        total_revenue: r.total_revenue,
        total_costs: r.total_cost,
        home_appliance_wh_per_hour: if has_appliances {
            r.ha.iter().map(|v| Some(*v)).collect()
        } else {
            vec![Some(0.0); n]
        },
        costs_per_hour: r.costs.clone(),
        grid_consumption_wh_per_hour: r.import.clone(),
        grid_feed_in_wh_per_hour: r.feedin.clone(),
        losses_per_hour: r.losses.clone(),
        battery_soc_per_hour: r.soc.clone(),
        electricity_price: r.price.clone(),
        home_appliance_energy_wh: OrderedMap(c.home_appliance_energy_wh.clone()),
        feed_in_tariff: r.tariff.clone(),
        home_appliance_running: OrderedMap(c.home_appliance_running.clone()),
    };
    let ev_obj = c.ev_obj.as_ref().map(|e| ElectricVehicleResult {
        device_id: e.spec.device_id.clone(),
        hours: e.prediction_hours as i64,
        charge_array: e.charge_array.clone(),
        discharge_array: e.discharge_array.iter().map(|v| *v as i64).collect(),
        discharging_efficiency: e.spec.discharging_efficiency,
        capacity_wh: e.spec.capacity_wh as i64,
        charging_efficiency: e.spec.charging_efficiency,
        max_charge_power_w: e.spec.max_charge_power_w as i64,
        soc_wh: e.soc_wh,
        initial_soc_percentage: e.spec.initial_soc_percentage as i64,
    });
    let h = &c.fitness_history;
    let fitness_history = FitnessHistory {
        generation: h.generation.clone(),
        avg: h.avg.clone(),
        max: h.max.clone(),
        min: h.min.clone(),
        diversity: h.diversity.clone(),
        stagnation: h.stagnation.clone(),
        immigrants: h.immigrants.clone(),
        restart: h.restart.clone(),
        fitness_cache: FitnessCacheStats {
            hits: h.cache_hits as i64,
            misses: h.cache_misses as i64,
            hit_rate: h.cache_hit_rate,
            keys: h.cache_keys as i64,
        },
        adaptive_evolution: AdaptiveEvolutionMetrics {
            soft_restarts: Some(h.soft_restarts),
            immigrants: Some(h.total_immigrants),
            minimum_diversity: Some(h.minimum_diversity),
            final_diversity: Some(h.final_diversity),
            final_stagnation: Some(h.final_stagnation),
        },
        local_search: LocalSearchStats {
            evaluations: h.local_evaluations,
            improvements: h.local_improvements,
            initial_fitness: h.local_initial_fitness,
            final_fitness: h.local_final_fitness,
        },
        extra: Map::new(),
    };
    let prediction_slots = c.result.load.len();
    GeneticSolution {
        parameters: final_parameters(&c.parameters, prediction_slots),
        ac_charge: c.ac_charge.clone(),
        dc_charge: c.dc_charge.clone(),
        discharge_allowed: c.discharge_allowed.iter().map(|v| *v as i64).collect(),
        ev_charge_hours_float: c.ev_charge_hours_float.clone(),
        result,
        ev_obj,
        start_hour: i64::from(c.start_hour),
        start_solution: Some(c.start_solution.iter().map(|v| *v as f64).collect()),
        washingstart: c.washingstart,
        extra_data: Some(OrderedMap(vec![
            ("bilanz".into(), c.extra_data.0.clone()),
            ("verluste".into(), c.extra_data.1.clone()),
            ("nebenbedingung".into(), c.extra_data.2.clone()),
        ])),
        fitness_history: Some(fitness_history),
        fixed_seed: c.fixed_seed,
        controls_start_at_now: true,
        battery_grid_export_allowed: c.battery_grid_export_allowed.clone(),
        terminal_value: Some(terminal(&c.terminal_value)),
        battery_grid_export_factor: c.battery_grid_export_factor.clone(),
        start_solution_datetime: Some(c.start_solution_datetime.fixed_offset()),
        appliance_starts: OrderedMap(
            c.appliance_starts.iter().map(|(k, v)| (k.clone(), v.iter().map(|d| fixed(d, tz)).collect())).collect(),
        ),
        appliance_deadline_missed: OrderedMap(c.appliance_deadline_missed.clone()),
        interval_seconds: c.interval_seconds,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn durations_like_pendulum() {
        for (h, want) in [
            (1, "1 hour"),
            (2, "2 hours"),
            (24, "1 day"),
            (25, "1 day 1 hour"),
            (48, "2 days"),
            (80, "3 days 8 hours"),
            (168, "1 week"),
            (192, "1 week 1 day"),
            (200, "1 week 1 day 8 hours"),
        ] {
            assert_eq!(duration_text_hours(h), want);
        }
    }
}
