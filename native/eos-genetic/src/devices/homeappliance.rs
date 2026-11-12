//! `devices/genetic/homeappliance.py` – schedulable household appliances (fixed run duration, one or
//! more cycles), as far as supported here.
//!
//! Supported: load as `consumption_wh`/`duration_h` or as `load_profile_power_w`,
//! `earliest_start_datetime`, `deadline_datetime` with `BEST_EFFORT`/`STRICT`, `ONCE` and `DAILY`,
//! multiple cycles with `min_cycle_gap_h`. **Not** supported: `time_windows` and
//! `shared_time_windows` (a request containing them is rejected).

use chrono::{DateTime, NaiveDate};
use chrono_tz::Tz;

use crate::params::HomeApplianceParameters;

/// Python `(a - b).total_seconds()` for two timestamps.
pub fn total_seconds(a: &DateTime<Tz>, b: &DateTime<Tz>) -> f64 {
    let d = a.signed_duration_since(*b);
    match d.num_microseconds() {
        Some(us) => us as f64 / 1e6,
        None => d.num_milliseconds() as f64 / 1e3,
    }
}

/// `slot0.add(seconds=s)` (absolute time).
pub fn add_seconds(dt: &DateTime<Tz>, seconds: f64) -> DateTime<Tz> {
    let us = (seconds * 1e6).round() as i64;
    *dt + chrono::Duration::microseconds(us)
}

/// `resample_power_to_slot_energy`.
pub fn resample_power_to_slot_energy(
    power_w: &[f64],
    input_interval_seconds: f64,
    slot_interval_seconds: f64,
) -> Result<Vec<f64>, String> {
    if !input_interval_seconds.is_finite() || input_interval_seconds <= 0.0 {
        return Err("Input interval must be finite and positive.".into());
    }
    if !slot_interval_seconds.is_finite() || slot_interval_seconds <= 0.0 {
        return Err("Slot interval must be finite and positive.".into());
    }
    if power_w.is_empty() || power_w.iter().any(|p| !p.is_finite() || *p < 0.0) {
        return Err("Power profile must contain finite non-negative values.".into());
    }
    let n_in = power_w.len();
    let total_seconds = n_in as f64 * input_interval_seconds;
    let n_slots = (total_seconds / slot_interval_seconds - 1e-9).ceil() as i64;
    let mut out = vec![0.0f64; n_slots.max(0) as usize];
    for (i, &power) in power_w.iter().enumerate() {
        if power == 0.0 {
            continue;
        }
        let seg_start = i as f64 * input_interval_seconds;
        let seg_end = seg_start + input_interval_seconds;
        let first = py_floordiv(seg_start, slot_interval_seconds) as i64;
        let last = py_floordiv(seg_end - 1e-9, slot_interval_seconds) as i64;
        for j in first..=last {
            let slot_start = j as f64 * slot_interval_seconds;
            let slot_end = slot_start + slot_interval_seconds;
            let overlap = seg_end.min(slot_end) - seg_start.max(slot_start);
            if overlap > 0.0 {
                out[j as usize] += power * overlap / 3600.0;
            }
        }
    }
    Ok(out)
}

/// Python's float `//`.
pub fn py_floordiv(vx: f64, wx: f64) -> f64 {
    let m = vx % wx;
    let mut div = (vx - m) / wx;
    if m != 0.0 && ((wx < 0.0) != (m < 0.0)) {
        div -= 1.0;
    }
    if div != 0.0 {
        let mut floordiv = div.floor();
        if div - floordiv > 0.5 {
            floordiv += 1.0;
        }
        floordiv
    } else {
        0.0f64.copysign(vx / wx)
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ScheduleMode {
    Once,
    Daily,
}

/// One appliance for a run (`HomeAppliance`).
#[derive(Debug, Clone)]
pub struct HomeAppliance {
    pub device_id: String,
    pub total_slots: usize,
    pub slot_interval_seconds: f64,
    pub run_energy_wh: Vec<f64>,
    pub run_slots: usize,
    pub num_cycles: usize,
    pub completed_cycles: usize,
    pub min_cycle_gap_h: f64,
    pub schedule_mode: ScheduleMode,
    pub earliest_start_datetime: Option<DateTime<Tz>>,
    pub deadline_datetime: Option<DateTime<Tz>>,
    pub strict_deadline: bool,
}

impl HomeAppliance {
    pub fn new(
        p: &HomeApplianceParameters,
        prediction_slots: usize,
        slot_duration_h: f64,
        tz: Tz,
    ) -> Result<HomeAppliance, String> {
        if p.time_windows.as_ref().is_some_and(|v| !v.is_null())
            || p.shared_time_windows.as_ref().is_some_and(|v| !v.is_null())
        {
            return Err(format!("home appliance '{}': time_windows are {}", p.device_id, crate::run::NOT_SUPPORTED));
        }
        // validate_home_appliance_load_definition
        let profile_given = p.load_profile_power_w.is_some();
        let fallback_partial = p.consumption_wh.is_some() || p.duration_h.is_some();
        let fallback_given = p.consumption_wh.is_some() && p.duration_h.is_some();
        if profile_given && fallback_partial {
            return Err("Conflicting home appliance load definition: provide either load_profile_power_w or consumption_wh together with duration_h, not both.".into());
        }
        if !profile_given && !fallback_given {
            return Err("Incomplete home appliance load definition: provide a full load_profile_power_w or both consumption_wh and duration_h.".into());
        }
        let slot_interval_seconds = slot_duration_h * 3600.0;
        let (power, input_interval) = if let Some(profile) = &p.load_profile_power_w {
            let iv = p.load_profile_interval_seconds.map(|v| v as f64).unwrap_or(slot_interval_seconds);
            (profile.clone(), iv)
        } else {
            let duration_h = p.duration_h.unwrap_or(1);
            let consumption = p.consumption_wh.unwrap_or(0);
            if duration_h <= 0 || consumption <= 0 {
                return Err("consumption_wh and duration_h must be > 0".into());
            }
            (vec![consumption as f64 / duration_h as f64], (duration_h * 3600) as f64)
        };
        let run_energy_wh = resample_power_to_slot_energy(&power, input_interval, slot_interval_seconds)?;
        let run_slots = run_energy_wh.len();
        let schedule_mode = match p.schedule_mode.to_ascii_uppercase().as_str() {
            "DAILY" => ScheduleMode::Daily,
            "ONCE" => ScheduleMode::Once,
            other => return Err(format!("unknown schedule_mode {other}")),
        };
        let strict_deadline = p.deadline_policy.eq_ignore_ascii_case("STRICT");
        let earliest = p.earliest_start_datetime.map(|d| d.resolve(tz));
        let deadline = p.deadline_datetime.map(|d| d.resolve(tz));
        if let (Some(e), Some(d)) = (earliest, deadline) {
            if d <= e {
                return Err(format!("deadline_datetime {d} must be after earliest_start_datetime {e}."));
            }
        }
        if p.num_cycles <= 0 {
            return Err("num_cycles must be > 0".into());
        }
        if p.completed_cycles < 0 || p.completed_cycles > p.num_cycles {
            return Err("completed_cycles must not exceed num_cycles.".into());
        }
        Ok(HomeAppliance {
            device_id: p.device_id.clone(),
            total_slots: prediction_slots,
            slot_interval_seconds,
            run_energy_wh,
            run_slots,
            num_cycles: p.num_cycles as usize,
            completed_cycles: p.completed_cycles as usize,
            min_cycle_gap_h: p.min_cycle_gap_h as f64,
            schedule_mode,
            earliest_start_datetime: earliest,
            deadline_datetime: deadline,
            strict_deadline,
        })
    }

    /// `_slot_offset`.
    fn slot_offset(&self, moment: &DateTime<Tz>, slot0: &DateTime<Tz>, round_up: bool) -> i64 {
        let exact = total_seconds(moment, slot0) / self.slot_interval_seconds;
        if round_up { (exact - 1e-9).ceil() as i64 } else { (exact + 1e-9).floor() as i64 }
    }

    fn allowed_inner(
        &self,
        slot0: &DateTime<Tz>,
        earliest_slot: i64,
        horizon_end_slot: i64,
        apply_deadline: bool,
    ) -> Vec<usize> {
        let run_slots = self.run_slots as i64;
        if run_slots <= 0 {
            return Vec::new();
        }
        let mut first_start = earliest_slot.max(0);
        if let Some(e) = &self.earliest_start_datetime {
            first_start = first_start.max(self.slot_offset(e, slot0, true));
        }
        let mut end_bound = horizon_end_slot.min(self.total_slots as i64);
        if apply_deadline {
            if let Some(d) = &self.deadline_datetime {
                end_bound = end_bound.min(self.slot_offset(d, slot0, false));
            }
        }
        let last_start = end_bound - run_slots;
        if last_start < first_start {
            return Vec::new();
        }
        // Without time windows every start within the range is allowed.
        (first_start..=last_start).map(|s| s as usize).collect()
    }

    /// `allowed_start_slots(...)` → (starts, deadline_relaxed).
    pub fn allowed_start_slots(
        &self,
        slot0: &DateTime<Tz>,
        earliest_slot: i64,
        horizon_end_slot: i64,
    ) -> (Vec<usize>, bool) {
        let allowed = self.allowed_inner(slot0, earliest_slot, horizon_end_slot, true);
        if !allowed.is_empty() || self.deadline_datetime.is_none() || self.strict_deadline {
            return (allowed, false);
        }
        let relaxed = self.allowed_inner(slot0, earliest_slot, horizon_end_slot, false);
        if relaxed.is_empty() {
            return (relaxed, false);
        }
        let earliest = if self.num_cycles == 1 { relaxed[..1].to_vec() } else { relaxed };
        (earliest, true)
    }

    /// Local date of the slot (DAILY grouping).
    pub fn slot_date(&self, slot0: &DateTime<Tz>, slot: usize) -> NaiveDate {
        add_seconds(slot0, slot as f64 * self.slot_interval_seconds).date_naive()
    }

    /// `run_end_datetime`.
    pub fn run_end_datetime(&self, start_slot: usize, slot0: &DateTime<Tz>) -> DateTime<Tz> {
        add_seconds(slot0, (start_slot + self.run_slots) as f64 * self.slot_interval_seconds)
    }

    /// `deadline_missed`.
    pub fn deadline_missed(&self, starts: &[usize], slot0: &DateTime<Tz>) -> bool {
        let Some(d) = &self.deadline_datetime else {
            return false;
        };
        if starts.is_empty() {
            return true;
        }
        starts.iter().any(|&s| self.run_end_datetime(s, slot0) > *d)
    }

    /// `build_load_curve(starts)` → load curve (length total_slots).
    pub fn build_load_curve(&self, starts: &[usize]) -> Result<Vec<f64>, ()> {
        if starts.iter().any(|&s| s + self.run_slots > self.total_slots) {
            return Err(());
        }
        let mut curve = vec![0.0f64; self.total_slots];
        for &start in starts {
            let length = self.run_slots.min(self.total_slots - start);
            if length > 0 {
                for k in 0..length {
                    curve[start + k] += self.run_energy_wh[k];
                }
            }
        }
        Ok(curve)
    }
}

/// One start gene (`ApplianceGeneSlot`).
#[derive(Debug, Clone)]
pub struct ApplianceGene {
    pub appliance_index: usize,
    pub device_id: String,
    pub run_index: usize,
    pub run_date: Option<NaiveDate>,
    pub allowed_start_slots: Vec<usize>,
    pub cycle_index: usize,
    pub deadline_relaxed: bool,
}

/// `_build_appliance_layout` (without the final trial decoding).
pub fn build_layout(
    appliances: &[HomeAppliance],
    slot0: &DateTime<Tz>,
    horizon_end_slot: i64,
) -> Result<Vec<ApplianceGene>, String> {
    let mut genes: Vec<ApplianceGene> = Vec::new();
    let first_date = slot0.date_naive();
    for (appliance_index, a) in appliances.iter().enumerate() {
        let mut allowed_by_cycle: Vec<(usize, Vec<usize>, bool)> = Vec::new();
        for cycle in 0..a.num_cycles {
            let (allowed, relaxed) = a.allowed_start_slots(slot0, 0, horizon_end_slot);
            allowed_by_cycle.push((cycle, allowed, relaxed));
        }
        let groups: Vec<(Option<NaiveDate>, usize, Vec<usize>)> = match a.schedule_mode {
            ScheduleMode::Once => allowed_by_cycle
                .iter()
                .filter(|(c, _, _)| *c >= a.completed_cycles)
                .map(|(c, al, _)| (None, *c, al.clone()))
                .collect(),
            ScheduleMode::Daily => {
                let mut by_date: std::collections::BTreeMap<NaiveDate, std::collections::BTreeMap<usize, Vec<usize>>> =
                    std::collections::BTreeMap::new();
                for (cycle, allowed, _) in &allowed_by_cycle {
                    for &slot in allowed {
                        let date = a.slot_date(slot0, slot);
                        if date == first_date && *cycle < a.completed_cycles {
                            continue;
                        }
                        by_date.entry(date).or_default().entry(*cycle).or_default().push(slot);
                    }
                }
                for (date, cycle_slots) in &by_date {
                    let expected: Vec<usize> =
                        (0..a.num_cycles).filter(|c| *date != first_date || *c >= a.completed_cycles).collect();
                    let have: Vec<usize> = cycle_slots.keys().copied().collect();
                    if have != expected {
                        return Err(format!(
                            "Home appliance '{}' has no complete cycle schedule on {date}.",
                            a.device_id
                        ));
                    }
                }
                by_date
                    .into_iter()
                    .flat_map(|(date, cs)| cs.into_iter().map(move |(c, al)| (Some(date), c, al)))
                    .collect()
            }
        };
        for (run_index, (date, cycle, allowed)) in groups.into_iter().enumerate() {
            if allowed.is_empty() {
                return Err(format!(
                    "Home appliance '{}' cycle {cycle} has no valid start within its windows, deadline and control horizon.",
                    a.device_id
                ));
            }
            let relaxed = allowed_by_cycle[cycle].2;
            genes.push(ApplianceGene {
                appliance_index,
                device_id: a.device_id.clone(),
                run_index,
                run_date: date,
                allowed_start_slots: allowed,
                cycle_index: cycle,
                deadline_relaxed: relaxed,
            });
        }
    }
    Ok(genes)
}

/// Start decoding (`_decode_appliance_starts`) with the per-run cache of the
/// feasible orders (`_appliance_order_cache`).
#[derive(Debug, Clone, Default)]
pub struct StartDecoder {
    order_cache: std::collections::HashMap<usize, Vec<usize>>,
}

impl StartDecoder {
    /// Repairs the genes jointly; writes the canonical values back. Result: starts
    /// per appliance (in decision order).
    pub fn decode(
        &mut self,
        layout: &[ApplianceGene],
        appliances: &[HomeAppliance],
        values: &mut [i64],
        slot_duration_h: f64,
        control_end_slot: usize,
    ) -> Result<Vec<Vec<usize>>, String> {
        let mut starts: Vec<Vec<usize>> = vec![Vec::new(); appliances.len()];
        // grouped: appliance → [(position, gene)] in position order; iteration in
        // order of first occurrence (defaultdict).
        let mut order_of_first: Vec<usize> = Vec::new();
        let mut grouped: std::collections::HashMap<usize, Vec<usize>> = std::collections::HashMap::new();
        for (pos, g) in layout.iter().enumerate() {
            if !grouped.contains_key(&g.appliance_index) {
                order_of_first.push(g.appliance_index);
            }
            grouped.entry(g.appliance_index).or_default().push(pos);
        }
        for appliance_index in order_of_first {
            let a = &appliances[appliance_index];
            let separation = a.run_slots as i64 + (a.min_cycle_gap_h / slot_duration_h).ceil() as i64;
            let mut entries: Vec<usize> = grouped[&appliance_index].clone();
            let requested_start = |pos: usize| -> i64 {
                let g = &layout[pos];
                let index = values[pos].max(0).min(g.allowed_start_slots.len() as i64 - 1) as usize;
                g.allowed_start_slots[if g.deadline_relaxed { 0 } else { index }] as i64
            };
            let latest_bounds = |order: &[usize]| -> Option<Vec<i64>> {
                let mut bounds: Vec<i64> = Vec::new();
                let mut limit = control_end_slot as i64;
                for &pos in order.iter().rev() {
                    let g = &layout[pos];
                    let last = g.allowed_start_slots.iter().rev().find(|&&s| s as i64 <= limit)?;
                    bounds.push(*last as i64);
                    limit = *last as i64 - separation;
                }
                bounds.reverse();
                Some(bounds)
            };
            let mut keyed: Vec<(i64, usize)> = entries.iter().map(|&p| (requested_start(p), p)).collect();
            keyed.sort();
            entries = keyed.into_iter().map(|(_, p)| p).collect();
            let mut latest = latest_bounds(&entries);
            if latest.is_none() {
                if !self.order_cache.contains_key(&appliance_index) {
                    let mut by_position: Vec<usize> = grouped[&appliance_index].clone();
                    by_position.sort();
                    let mut memo: std::collections::HashMap<(Vec<usize>, i64), Option<Vec<usize>>> =
                        std::collections::HashMap::new();
                    let order = feasible_order(layout, &by_position, 0, separation, &mut memo).ok_or_else(|| {
                        format!("Home appliance '{}' cycles cannot fit their windows and minimum gaps.", a.device_id)
                    })?;
                    self.order_cache.insert(appliance_index, order);
                }
                entries = self.order_cache[&appliance_index].clone();
                latest = latest_bounds(&entries);
                if latest.is_none() {
                    return Err(format!("Home appliance '{}' has no feasible cycle schedule.", a.device_id));
                }
            }
            let latest = latest.unwrap_or_default();
            let mut earliest: i64 = 0;
            for (k, &pos) in entries.iter().enumerate() {
                let g = &layout[pos];
                let allowed = &g.allowed_start_slots;
                let last = latest[k];
                let requested_index = values[pos].max(0).min(allowed.len() as i64 - 1) as usize;
                let requested = allowed[requested_index] as i64;
                let candidates: Vec<i64> =
                    allowed.iter().map(|&s| s as i64).filter(|&s| earliest <= s && s <= last).collect();
                if candidates.is_empty() {
                    return Err(format!("Home appliance '{}' has no feasible cycle start.", g.device_id));
                }
                let chosen = if g.deadline_relaxed {
                    candidates[0]
                } else {
                    // min(candidates, key=(abs(slot - requested), slot)) – first minimum
                    let mut best = candidates[0];
                    for &c in &candidates[1..] {
                        if ((c - requested).abs(), c) < ((best - requested).abs(), best) {
                            best = c;
                        }
                    }
                    best
                };
                starts[appliance_index].push(chosen as usize);
                values[pos] = allowed.iter().position(|&s| s as i64 == chosen).unwrap_or(0) as i64;
                earliest = chosen + separation;
            }
        }
        Ok(starts)
    }
}

/// `feasible_order(remaining, earliest)` (lru_cache recursion).
fn feasible_order(
    layout: &[ApplianceGene],
    remaining: &[usize],
    earliest: i64,
    separation: i64,
    memo: &mut std::collections::HashMap<(Vec<usize>, i64), Option<Vec<usize>>>,
) -> Option<Vec<usize>> {
    if remaining.is_empty() {
        return Some(Vec::new());
    }
    let key = (remaining.to_vec(), earliest);
    if let Some(v) = memo.get(&key) {
        return v.clone();
    }
    let mut choices: Vec<(i64, i64, usize)> = Vec::new();
    for &pos in remaining {
        let allowed: Vec<i64> =
            layout[pos].allowed_start_slots.iter().map(|&s| s as i64).filter(|&s| s >= earliest).collect();
        if allowed.is_empty() {
            memo.insert(key, None);
            return None;
        }
        choices.push((allowed[0], allowed[allowed.len() - 1], pos));
    }
    choices.sort();
    for (first, _, pos) in choices {
        let rest: Vec<usize> = remaining.iter().copied().filter(|&p| p != pos).collect();
        if let Some(r) = feasible_order(layout, &rest, first + separation, separation, memo) {
            let mut out = vec![pos];
            out.extend(r);
            memo.insert(key, Some(out.clone()));
            return Some(out);
        }
    }
    memo.insert(key, None);
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn resample_flat_profile() {
        let e = resample_power_to_slot_energy(&[1000.0], 3.0 * 3600.0, 900.0).unwrap();
        assert_eq!(e.len(), 12);
        assert!((e.iter().sum::<f64>() - 3000.0).abs() < 1e-9);
    }

    #[test]
    fn floordiv_like_python() {
        assert_eq!(py_floordiv(7.0, 2.0), 3.0);
        assert_eq!(py_floordiv(-7.0, 2.0), -4.0);
        assert_eq!(py_floordiv(36420.0, 900.0), 40.0);
    }
}
