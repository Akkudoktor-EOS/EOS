//! `optimization/genetic/genetic.py` – `GeneticOptimization`: setup of a run
//! (`optimize_ems`), start solutions (warm start, heuristic start genomes), mutation/crossover,
//! the adaptive GA loop (`_evolve_population_adaptive`) and the local search.
//!
//! Randomness exclusively via [`PyRandom`], in exactly the order of the Python calls.
//! Individuals are shared objects (`Rc<RefCell<Ind>>`) because Python lists hold references:
//! the same individual can appear several times in a population (tournament selection), and
//! `_age_immigrant_protection` then counts it down several times.

use std::cell::RefCell;
use std::collections::VecDeque;

use crate::numpy::{FxHashMap as HashMap, FxHashSet as HashSet};
use std::rc::Rc;

use chrono::DateTime;
use chrono_tz::Tz;

use crate::curves::{self, TailPlanSlot, TailTrace, ValueCurve};
use crate::devices::battery::{Battery, BatterySpec};
use crate::devices::homeappliance::{self, ApplianceGene, HomeAppliance, StartDecoder, add_seconds, total_seconds};
use crate::devices::inverter::Inverter;
use crate::numpy::{self, NumericProfile, py_max, py_min};
use crate::params::{FeedInTariff, GeneticOptimizationParameters};
use crate::pyrandom::PyRandom;
use crate::selfconsumption::SelfConsumption;
use crate::settings::{RunConfig, TerminalValueMode};
use crate::simulation::{
    BreakEven, DecodeTable, EvPenalty, Evaluation, Evaluator, FullCharge, RunConst, SimResult, Terminal,
    terminal_credit,
};

macro_rules! timed {
    ($self:ident, $idx:expr, $e:expr) => {{
        let t0 = std::time::Instant::now();
        let r = $e;
        $self.prof[$idx] += t0.elapsed().as_nanos() as u64;
        r
    }};
}

const WARM_START_COPIES: i64 = 10;
const WARM_START_MUTATIONS: i64 = 50;
const EDUCATED_GUESS_TARGET: i64 = 100;
const MIN_RANDOM_POPULATION_FRACTION: f64 = 0.25;
const WARM_START_COPY_FRACTION: f64 = 0.10;
const WARM_START_MUTATION_FRACTION: f64 = 0.20;
const EDUCATED_GUESS_FRACTION: f64 = 0.40;
const LOCAL_SEARCH_MAX_EVALUATIONS: i64 = 96;
const LOCAL_SEARCH_MAX_PASSES: usize = 4;
const EDUCATED_GUESS_EXPORT_QUANTILES: [f64; 3] = [0.60, 0.75, 0.90];
const CROSSOVER_PROBABILITY: f64 = 0.50;
const MUTATION_PROBABILITY: f64 = 0.55;
const STAGNATION_MUTATION_PROBABILITY: f64 = 0.80;
const STAGNATION_GENERATIONS: i64 = 8;
const SOFT_RESTART_GENERATIONS: i64 = 20;
const SELECTION_DIVERSITY_FLOOR: f64 = 0.30;
const DIVERSITY_BOOST_THRESHOLD: f64 = 0.25;
const SOFT_RESTART_DIVERSITY_THRESHOLD: f64 = 0.10;
const IMMIGRANT_FRACTION: f64 = 0.12;
const IMMIGRANT_PROTECTION_GENERATIONS: i64 = 2;
const IMMIGRANT_PROTECTION_FRACTION: f64 = 0.25;
const SOFT_RESTART_SURVIVOR_FRACTION: f64 = 0.20;
const POINT_MUTATION_EXPECTED_GENES: f64 = 3.0;

/// An individual (DEAP `creator.Individual`).
#[derive(Debug, Clone)]
pub struct Ind {
    pub genes: Vec<i64>,
    pub fitness: Option<f64>,
    pub extra: Option<(f64, f64, f64)>,
    pub protection: i64,
}

type IRef = Rc<RefCell<Ind>>;

fn new_ind(genes: Vec<i64>) -> IRef {
    Rc::new(RefCell::new(Ind { genes, fitness: None, extra: None, protection: 0 }))
}

/// DEAP `Fitness.__gt__` for FitnessMin (w = -f): `not (w_a <= w_b)`.
#[inline]
fn fit_gt(a: f64, b: f64) -> bool {
    let (wa, wb) = (-a, -b);
    !(wa == wb || wa <= wb)
}

/// `tools.selBest(individuals, k)`: stable, ascending by fitness (best first).
fn sel_best(pop: &[IRef], k: usize) -> Vec<IRef> {
    let mut v: Vec<IRef> = pop.to_vec();
    v.sort_by(|a, b| {
        let fa = a.borrow().fitness.unwrap_or(f64::INFINITY);
        let fb = b.borrow().fitness.unwrap_or(f64::INFINITY);
        // Python: reverse=True on key w = -f, stable → ascending by f, ties in
        // input order.
        (-fb).partial_cmp(&-fa).unwrap_or(std::cmp::Ordering::Equal)
    });
    v.truncate(k);
    v
}

/// First maximum using Python `>` on floats.
fn py_argmax(vals: &[f64]) -> usize {
    let mut best = 0;
    for i in 1..vals.len() {
        if vals[i] > vals[best] {
            best = i;
        }
    }
    best
}

/// First minimum using Python `<`.
fn py_argmin_by<T, F: Fn(&T) -> f64>(items: &[T], key: F) -> usize {
    let mut best = 0;
    let mut bk = key(&items[0]);
    for (i, it) in items.iter().enumerate().skip(1) {
        let k = key(it);
        if k < bk {
            best = i;
            bk = k;
        }
    }
    best
}

/// Lexicographic tuple comparison of two f64 pairs like Python (without NaN).
fn cmp_f64_pair(a: (f64, f64), b: (f64, f64)) -> std::cmp::Ordering {
    match a.0.partial_cmp(&b.0) {
        Some(std::cmp::Ordering::Equal) | None => a.1.partial_cmp(&b.1).unwrap_or(std::cmp::Ordering::Equal),
        Some(o) => o,
    }
}

/// Compact key (`_pack_genes`): one byte per gene if all genes are in 0..=255,
/// otherwise 8 bytes per gene; the first byte distinguishes the two forms.
type PackedKey = Rc<[u8]>;

fn pack_genes(g: &[i64]) -> PackedKey {
    if g.iter().all(|&v| (0..=255).contains(&v)) {
        let mut b = Vec::with_capacity(g.len() + 1);
        b.push(0u8);
        b.extend(g.iter().map(|&v| v as u8));
        b.into()
    } else {
        let mut b = Vec::with_capacity(8 * g.len() + 1);
        b.push(1u8);
        for &v in g {
            b.extend_from_slice(&v.to_le_bytes());
        }
        b.into()
    }
}

fn unpack_genes(k: &[u8]) -> Vec<i64> {
    if k.first() == Some(&0) {
        k[1..].iter().map(|&b| i64::from(b)).collect()
    } else {
        k[1..].chunks_exact(8).map(|c| i64::from_le_bytes(c.try_into().unwrap_or([0; 8]))).collect()
    }
}

/// Entry of the fitness cache (`FitnessCacheEntry`).
#[derive(Debug, Clone)]
struct CacheEntry {
    genome: PackedKey,
    fitness: f64,
    extra: (f64, f64, f64),
}

/// `OrderedDict`/`dict` cache with FIFO eviction.
#[derive(Debug, Default)]
struct FitnessCache {
    enabled: bool,
    max_entries: Option<usize>,
    map: HashMap<PackedKey, Rc<CacheEntry>>,
    order: VecDeque<PackedKey>,
    hits: u64,
    misses: u64,
}

impl FitnessCache {
    fn get(&self, key: &[i64]) -> Option<Rc<CacheEntry>> {
        self.map.get(&pack_genes(key)).cloned()
    }

    fn insert(&mut self, key: PackedKey, entry: Rc<CacheEntry>) {
        if let Some(slot) = self.map.get_mut(&key) {
            *slot = entry;
            return;
        }
        if self.max_entries.is_some() {
            self.order.push_back(key.clone());
        }
        self.map.insert(key, entry);
    }

    fn evict(&mut self) {
        if let Some(max) = self.max_entries {
            while self.map.len() > max {
                match self.order.pop_front() {
                    Some(k) => {
                        self.map.remove(&k);
                    }
                    None => break,
                }
            }
        }
    }
}

/// Battery state layout (`BatteryStateLayout`).
#[derive(Debug, Clone, Default, PartialEq)]
pub struct StateLayout {
    pub total_states: i64,
    pub dc_not_allowed_state: Option<i64>,
    pub dc_allowed_state: Option<i64>,
    pub grid_export_state: Option<i64>,
    pub self_consumption_state: Option<i64>,
    pub grid_export_states: Vec<i64>,
}

/// `TailDiagnostics`.
#[derive(Debug, Clone, Default)]
pub struct TailDiagnostics {
    pub slots: usize,
    pub slot_hours: f64,
    pub soc_grid_points: usize,
    pub min_import_price_euro_per_kwh: f64,
    pub max_import_price_euro_per_kwh: f64,
    pub min_feed_in_tariff_euro_per_kwh: f64,
    pub max_feed_in_tariff_euro_per_kwh: f64,
    pub negative_import_price_slots: usize,
    pub positive_battery_export_slots: usize,
}

/// `TerminalValueResult`.
#[derive(Debug, Clone, Default)]
pub struct TerminalValueResult {
    pub control_horizon_hours: f64,
    pub requested_tail_hours: f64,
    pub effective_tail_hours: f64,
    pub tail_end_hour: f64,
    pub continuation_mode: String,
    pub mode: String,
    pub battery_energy_wh: f64,
    pub credited_euro: f64,
    pub tail_operating_euro: f64,
    pub continuation_value_euro: f64,
    pub curve: Option<ValueCurve>,
    pub continuation_curve: Option<ValueCurve>,
    pub tail_diagnostics: Option<TailDiagnostics>,
    pub tail_plan: Vec<TailPlanSlot>,
    pub reason: String,
}

/// Final state of a battery (`ev_obj`).
#[derive(Debug, Clone)]
pub struct BatteryState {
    pub spec: BatterySpec,
    pub soc_wh: f64,
    pub charge_array: Vec<f64>,
    pub discharge_array: Vec<f64>,
    pub prediction_hours: usize,
}

/// Result of `optimize_ems` (all fields of `GeneticSolution`, on the Rust side).
#[derive(Debug, Clone)]
pub struct CoreSolution {
    /// Parameters mapped onto the slot grid (`_parameters_for_slot_grid`).
    pub parameters: GeneticOptimizationParameters,
    pub interval_seconds: i64,
    pub start_hour: u32,
    /// `extra_data`: bilanz, verluste, nebenbedingung of the final population.
    pub extra_data: (Vec<f64>, Vec<f64>, Vec<f64>),
    pub fitness_history: FitnessHistory,
    pub fixed_seed: Option<i64>,
    pub ac_charge: Vec<f64>,
    pub dc_charge: Vec<f64>,
    pub discharge_allowed: Vec<f64>,
    pub battery_grid_export_allowed: Vec<i64>,
    pub battery_grid_export_factor: Vec<f64>,
    pub terminal_value: TerminalValueResult,
    pub ev_charge_hours_float: Option<Vec<f64>>,
    pub result: SimResult,
    pub home_appliance_energy_wh: Vec<(String, Vec<f64>)>,
    pub home_appliance_running: Vec<(String, Vec<bool>)>,
    pub ev_obj: Option<BatteryState>,
    pub start_solution: Vec<i64>,
    pub start_solution_datetime: DateTime<Tz>,
    pub washingstart: Option<i64>,
    pub appliance_starts: Vec<(String, Vec<DateTime<Tz>>)>,
    pub appliance_deadline_missed: Vec<(String, bool)>,
    /// Diagnostics (not part of the Python solution).
    pub evaluations: u64,
    pub direct_consumption_misses: u64,
    /// Time spent in fitness evaluation [s] (rest = GA bookkeeping).
    pub eval_seconds: f64,
    pub profile_seconds: Vec<f64>,
}

/// `fitness_history`.
#[derive(Debug, Clone, Default)]
pub struct FitnessHistory {
    pub generation: Vec<i64>,
    pub avg: Vec<f64>,
    pub max: Vec<f64>,
    pub min: Vec<f64>,
    pub diversity: Vec<f64>,
    pub stagnation: Vec<i64>,
    pub immigrants: Vec<i64>,
    pub restart: Vec<i64>,
    pub cache_hits: u64,
    pub cache_misses: u64,
    pub cache_hit_rate: f64,
    pub cache_keys: usize,
    pub soft_restarts: i64,
    pub total_immigrants: i64,
    pub minimum_diversity: f64,
    pub final_diversity: f64,
    pub final_stagnation: i64,
    pub local_evaluations: i64,
    pub local_improvements: i64,
    pub local_initial_fitness: f64,
    pub local_final_fitness: f64,
}

/// HallOfFame(1).
#[derive(Default)]
struct HallOfFame {
    item: Option<Ind>,
}

impl HallOfFame {
    fn update(&mut self, population: &[IRef]) {
        for ind in population {
            if self.item.is_none() {
                self.item = Some(population[0].borrow().clone());
                continue;
            }
            let i = ind.borrow();
            let h = self.item.as_ref().map(|h| h.fitness.unwrap_or(f64::INFINITY)).unwrap_or(f64::INFINITY);
            if fit_gt(i.fitness.unwrap_or(f64::INFINITY), h) {
                let similar = self.item.as_ref().is_some_and(|hh| hh.genes == i.genes);
                if !similar {
                    self.item = Some(i.clone());
                }
            }
        }
    }

    fn best_fitness(&self) -> f64 {
        self.item.as_ref().and_then(|i| i.fitness).unwrap_or(f64::INFINITY)
    }
}

/// The optimizer of a run.
pub struct Optimizer<'a> {
    cfg: &'a RunConfig,
    pub rc: RunConst,
    rng: PyRandom,
    fix_seed: Option<i64>,
    evaluators: Vec<Evaluator>,
    threads: usize,
    // Run configuration
    slot_h: f64,
    slots_per_hour: i64,
    ce: usize,
    control_slots: usize,
    optimize_ev: bool,
    optimize_dc_charge: bool,
    optimize_battery_grid_export: bool,
    bat_possible_charge_values: Vec<f64>,
    bat_possible_grid_export_values: Vec<f64>,
    ev_possible_charge_values: Vec<f64>,
    ev_soc_deadline_slot: Option<i64>,
    layout: StateLayout,
    mutation_probability: f64,
    // Simulation series (truncated to the control horizon)
    prices: Vec<f64>,
    feed_in: Vec<f64>,
    pv: Vec<f64>,
    load: Vec<f64>,
    energy_shift_cache: HashMap<usize, Vec<usize>>,
    cache: FitnessCache,
    // Terminal value
    terminal_curve: Option<ValueCurve>,
    continuation_curve: Option<ValueCurve>,
    tail_trace: Option<TailTrace>,
    tail_diagnostics: Option<TailDiagnostics>,
    terminal_value_reason: String,
    forecast_reason: String,
    effective_tail_slots: usize,
    slot0: DateTime<Tz>,
    #[allow(dead_code)]
    start_day_slot: i64,
    eval_ns: u64,
    /// Time shares [ns]: 0 fresh, 1 selection, 2 offspring, 3 diversity, 4 HallOfFame, 5 start.
    prof: [u64; 8],
}

/// `_start_day_slot()`.
fn start_day_slot(start: &DateTime<Tz>, slot_h: f64) -> i64 {
    let midnight = start
        .date_naive()
        .and_hms_opt(0, 0, 0)
        .and_then(|n| n.and_local_timezone(start.timezone()).earliest())
        .unwrap_or(*start);
    homeappliance::py_floordiv(total_seconds(start, &midnight), slot_h * 3600.0) as i64
}

/// `_parameters_for_slot_grid`.
fn parameters_for_slot_grid(
    p: &GeneticOptimizationParameters,
    interval_sec: i64,
    prediction_hours: i64,
    start_day: i64,
) -> Result<GeneticOptimizationParameters, String> {
    let slots_per_hour = 3600 / interval_sec;
    let prediction_slots = prediction_hours * slots_per_hour;
    let normalize = |values: &[f64], name: &str, energy: bool| -> Result<Vec<f64>, String> {
        let mut data: Vec<f64> = values.to_vec();
        let mut native = p.forecast_interval_seconds == Some(interval_sec);
        if p.forecast_interval_seconds.is_none() {
            let max_hourly = prediction_hours + (start_day as f64 / slots_per_hour as f64).ceil() as i64;
            let n = data.len() as i64;
            if slots_per_hour > 1 && max_hourly < n && n < prediction_slots {
                return Err(format!(
                    "{name}: ambiguous forecast interval; expected either {prediction_hours} hourly values or {prediction_slots} native values. Set forecast_interval_seconds for shortened native forecasts."
                ));
            }
            native = slots_per_hour == 1 || n >= prediction_slots;
        }
        if p.forecast_interval_seconds == Some(900) && slots_per_hour == 1 {
            let rem = data.len() % 4;
            if rem != 0 {
                data.extend(std::iter::repeat_n(f64::NAN, 4 - rem));
            }
            data = data
                .chunks(4)
                .map(|b| {
                    let s = numpy::sum(b);
                    if energy { s } else { s / 4.0 }
                })
                .collect();
        } else if !native {
            let k = slots_per_hour as usize;
            let mut out = Vec::with_capacity(data.len() * k);
            for &v in &data {
                for _ in 0..k {
                    out.push(if energy { v / slots_per_hour as f64 } else { v });
                }
            }
            data = out;
        }
        let s = (start_day.max(0) as usize).min(data.len());
        Ok(data[s..].to_vec())
    };
    let ems = &p.ems;
    let feed_in = match &ems.feed_in_tariff_per_wh {
        FeedInTariff::Series(s) => normalize(s, "feed_in_tariff_per_wh", false)?,
        FeedInTariff::Scalar(v) => vec![*v; prediction_slots.max(0) as usize],
    };
    let mut out = p.clone();
    out.ems.pv_forecast_wh = normalize(&ems.pv_forecast_wh, "pv_forecast_wh", true)?;
    out.ems.total_load = normalize(&ems.total_load, "total_load", true)?;
    out.ems.electricity_price_per_wh = normalize(&ems.electricity_price_per_wh, "electricity_price_per_wh", false)?;
    out.ems.feed_in_tariff_per_wh = FeedInTariff::Series(feed_in);
    if let Some(t) = &p.temperature_forecast {
        let tv: Vec<f64> = t.iter().map(|v| v.unwrap_or(f64::NAN)).collect();
        out.temperature_forecast = Some(normalize(&tv, "temperature_forecast", false)?.into_iter().map(Some).collect());
    }
    out.forecast_interval_seconds = Some(interval_sec);
    Ok(out)
}

impl<'a> Optimizer<'a> {
    // ------------------------------------------------------------------ States

    fn battery_state_layout(&self) -> StateLayout {
        let mut next_state = 3 * self.bat_possible_charge_values.len() as i64;
        let mut l = StateLayout::default();
        if self.optimize_dc_charge {
            l.dc_not_allowed_state = Some(next_state);
            l.dc_allowed_state = Some(next_state + 1);
            next_state += 2;
        }
        if self.optimize_battery_grid_export {
            let count = self.bat_possible_grid_export_values.len().max(1) as i64;
            l.grid_export_states = (next_state..next_state + count).collect();
            l.grid_export_state = Some(l.grid_export_states[0]);
            next_state += count;
        }
        if self.optimize_dc_charge {
            l.self_consumption_state = Some(next_state);
            next_state += 1;
        }
        l.total_states = next_state;
        l
    }

    /// `_decode_states` for one state.
    fn decode_state(&self, s: i64) -> (f64, f64, f64, f64) {
        let len_bat = self.bat_possible_charge_values.len() as i64;
        let l = &self.layout;
        let discharge_mask = s >= len_bat && s < 2 * len_bat;
        let ac_mask = s >= 2 * len_bat && s < 3 * len_bat;
        let dc = if let Some(dca) = l.dc_allowed_state {
            let mut m = s == dca;
            if let Some(sc) = l.self_consumption_state {
                m |= s == sc;
            }
            if m { 1.0 } else { 0.0 }
        } else {
            1.0
        };
        let mut discharge = if discharge_mask { 1.0 } else { 0.0 };
        if l.self_consumption_state == Some(s) {
            discharge = 1.0;
        }
        let ac = if ac_mask { self.bat_possible_charge_values[(s - 2 * len_bat) as usize] } else { 0.0 };
        let mut export = 0.0;
        for (index, &es) in l.grid_export_states.iter().enumerate() {
            let rate = if index < self.bat_possible_grid_export_values.len() {
                self.bat_possible_grid_export_values[index]
            } else {
                1.0
            };
            if s == es {
                export = rate;
            }
        }
        (ac, dc, discharge, export)
    }

    // ------------------------------------------------------------------ Random genomes

    /// `create_individual()`.
    fn create_individual(&mut self) -> IRef {
        let ce = self.ce;
        let total = self.layout.total_states;
        let mut g: Vec<i64> = (0..ce).map(|_| self.rng.randint(0, total - 1)).collect();
        if self.optimize_ev {
            let n = self.ev_possible_charge_values.len() as i64;
            g.extend((0..ce).map(|_| self.rng.randint(0, n - 1)));
        }
        for i in 0..self.rc.layout.len() {
            let n = self.rc.layout[i].allowed_start_slots.len() as i64;
            g.push(self.rng.randint(0, n - 1));
        }
        new_ind(g)
    }

    fn population(&mut self, n: usize) -> Vec<IRef> {
        (0..n).map(|_| self.create_individual()).collect()
    }

    fn ev_zero_index(&self) -> Option<usize> {
        let rates = &self.ev_possible_charge_values;
        if rates.is_empty() {
            return None;
        }
        let zi = py_argmin_by(&(0..rates.len()).collect::<Vec<_>>(), |&i| rates[i].abs());
        if rates[zi].abs() > 1e-12 { None } else { Some(zi) }
    }

    // ------------------------------------------------------------------ Mutation

    /// `tools.mutUniformInt(part, low, up, indpb)`.
    fn mut_uniform_int(&mut self, part: &mut [i64], low: i64, up: i64) {
        let p = self.mutation_probability;
        for v in part.iter_mut() {
            if self.rng.random() < p {
                *v = self.rng.randint(low, up);
            }
        }
    }

    /// `_force_segment_change`.
    fn force_segment_change(&mut self, values: &mut [i64], low: i64, up: i64) -> bool {
        if values.is_empty() || up <= low {
            return false;
        }
        let position = self.rng.randrange(values.len());
        let old = values[position];
        let mut replacement = self.rng.randint(low, up - 1);
        if replacement >= old {
            replacement += 1;
        }
        values[position] = replacement;
        true
    }

    fn mutate_point_controls(&mut self, ind: &mut Vec<i64>) -> bool {
        let mut changed = false;
        let ce = self.ce;
        let total = self.layout.total_states;
        let mut part: Vec<i64> = ind[0..ce].to_vec();
        let before = part.clone();
        self.mut_uniform_int(&mut part, 0, total - 1);
        if part == before {
            self.force_segment_change(&mut part, 0, total - 1);
        }
        if part != before {
            ind[0..ce].copy_from_slice(&part);
            changed = true;
        }
        if self.optimize_ev && self.rng.random() < 0.40 {
            changed = self.mutate_ev_controls(ind) || changed;
        }
        changed
    }

    fn mutate_ev_controls(&mut self, ind: &mut [i64]) -> bool {
        let ce = self.ce;
        let n = self.ev_possible_charge_values.len() as i64;
        let mut part: Vec<i64> = ind[ce..ce * 2].to_vec();
        let before = part.clone();
        self.mut_uniform_int(&mut part, 0, n - 1);
        if part == before {
            self.force_segment_change(&mut part, 0, n - 1);
        }
        if part != before {
            ind[ce..ce * 2].copy_from_slice(&part);
            return true;
        }
        false
    }

    fn mutate_battery_block(&mut self, ind: &mut [i64]) {
        let ce = self.ce as i64;
        if 0 >= ce {
            return;
        }
        let len_bat = self.bat_possible_charge_values.len() as i64;
        let mut policy = vec![0, len_bat];
        if let Some(s) = self.layout.self_consumption_state {
            policy.push(s);
        }
        if let Some(s) = self.layout.dc_allowed_state {
            policy.push(s);
        }
        policy.extend(self.layout.grid_export_states.iter().copied());
        let block_start = self.rng.randint(0, ce - 1);
        let max_length = 12.min(ce - block_start);
        let block_length = if max_length > 1 { self.rng.randint(2, 2.max(max_length)) } else { 1 };
        let state = policy[self.rng.choice_index(policy.len())];
        let bs = block_start as usize;
        let be = (block_start + block_length) as usize;
        let be = be.min(ind.len());
        for v in ind[bs..be].iter_mut() {
            *v = state;
        }
    }

    /// `_energy_shift_target_slots`.
    fn energy_shift_target_slots(&mut self, ind: &[i64], source: usize) -> Vec<usize> {
        let ce = self.ce;
        if self.prices.len() < ce || self.feed_in.len() < ce || self.pv.len() < ce || self.load.len() < ce {
            return Vec::new();
        }
        if !self.energy_shift_cache.contains_key(&source) {
            let source_tariff = self.feed_in[source];
            let mut cands: Vec<usize> =
                (source + 1..ce).filter(|&s| self.load[s] > self.pv[s] && self.prices[s] > source_tariff).collect();
            let prices = &self.prices;
            let load = &self.load;
            let pv = &self.pv;
            cands.sort_by(|&a, &b| cmp_f64_pair((prices[b], load[b] - pv[b]), (prices[a], load[a] - pv[a])));
            self.energy_shift_cache.insert(source, cands);
        }
        let len_bat = self.bat_possible_charge_values.len() as i64;
        self.energy_shift_cache[&source].iter().copied().filter(|&s| 0 <= ind[s] && ind[s] < len_bat).collect()
    }

    fn mutate_energy_shift(&mut self, ind: &mut [i64]) -> bool {
        let export_states: Vec<i64> = self.layout.grid_export_states.clone();
        let Some(self_state) = self.layout.self_consumption_state else {
            return false;
        };
        if export_states.is_empty() {
            return false;
        }
        let mut viable: Vec<(usize, Vec<usize>)> = Vec::new();
        for source in 0..self.ce {
            if !export_states.contains(&ind[source]) {
                continue;
            }
            let t = self.energy_shift_target_slots(ind, source);
            if !t.is_empty() {
                viable.push((source, t));
            }
        }
        if viable.is_empty() {
            return false;
        }
        let feed_in = &self.feed_in;
        viable.sort_by(|a, b| cmp_f64_pair((feed_in[a.0], -(a.0 as f64)), (feed_in[b.0], -(b.0 as f64))));
        let k = viable.len().min(6);
        let pick = self.rng.choice_index(k);
        let (source, targets) = viable[pick].clone();
        ind[source] = self_state;
        let target_count = (targets.len() as i64).min(self.rng.randint(4, 10)) as usize;
        let len_bat = self.bat_possible_charge_values.len() as i64;
        for &t in targets.iter().take(target_count) {
            ind[t] = if self.pv[t] > 0.0 { self_state } else { len_bat };
        }
        true
    }

    fn mutate_flexible_controls(&mut self, ind: &mut Vec<i64>) -> bool {
        let mut changed = false;
        if self.optimize_ev {
            changed = self.mutate_ev_controls(ind);
        }
        let n_app = self.rc.layout.len();
        if n_app > 0 {
            let base = ind.len() - n_app;
            let mutable: Vec<(usize, i64)> = self
                .rc
                .layout
                .iter()
                .enumerate()
                .filter(|(_, g)| g.allowed_start_slots.len() > 1)
                .map(|(p, g)| (base + p, g.allowed_start_slots.len() as i64 - 1))
                .collect();
            if !mutable.is_empty() {
                let (position, upper) = mutable[self.rng.choice_index(mutable.len())];
                let old = ind[position];
                let mut replacement = self.rng.randint(0, upper - 1);
                if replacement >= old {
                    replacement += 1;
                }
                ind[position] = replacement;
                changed = true;
            }
        }
        changed
    }

    /// `mutate(individual)`.
    fn mutate(&mut self, ind: &mut Vec<i64>) {
        let operation = self.rng.random();
        let changed = if operation < 0.50 {
            self.mutate_point_controls(ind)
        } else if operation < 0.70 {
            let before = ind.clone();
            self.mutate_battery_block(ind);
            *ind != before
        } else if operation < 0.90 {
            self.mutate_energy_shift(ind)
        } else {
            self.mutate_flexible_controls(ind)
        };
        if !changed {
            self.mutate_point_controls(ind);
        }
    }

    /// `tools.cxTwoPoint(ind1, ind2)`.
    fn cx_two_point(&mut self, a: &mut [i64], b: &mut [i64]) {
        let size = a.len().min(b.len()) as i64;
        let mut cx1 = self.rng.randint(1, size);
        let mut cx2 = self.rng.randint(1, size - 1);
        if cx2 >= cx1 {
            cx2 += 1;
        } else {
            std::mem::swap(&mut cx1, &mut cx2);
        }
        let (c1, c2) = (cx1 as usize, cx2 as usize);
        for i in c1..c2 {
            std::mem::swap(&mut a[i], &mut b[i]);
        }
    }

    // ------------------------------------------------------------------ Heuristics

    fn heuristic_ev_schedule(&self, prefer_pv: bool) -> Vec<i64> {
        if !self.optimize_ev || self.ev_possible_charge_values.is_empty() {
            return Vec::new();
        }
        let rates = &self.ev_possible_charge_values;
        let zero_index = py_argmin_by(&(0..rates.len()).collect::<Vec<_>>(), |&i| rates[i].abs()) as i64;
        let mut schedule = vec![zero_index; self.ce];
        let Some(ev) = self.rc.ev.as_ref() else {
            return schedule;
        };
        // As in Python: the EV battery's own minimum SoC.
        let required = py_max(ev.min_soc_wh - ev.capacity_wh * ev.initial_soc_percentage / 100.0, 0.0);
        if required <= 0.0 {
            return schedule;
        }
        let mut end = self.ce as i64;
        if let Some(d) = self.ev_soc_deadline_slot {
            end = 0.max(end.min(d));
        }
        let prices = &self.prices;
        let feed_in = &self.feed_in;
        let pv = &self.pv;
        let load = &self.load;
        let marginal = |slot: usize| -> (f64, f64) {
            let surplus = pv[slot] - load[slot];
            if prefer_pv && surplus > 0.0 { (feed_in[slot], -surplus) } else { (prices[slot], -surplus) }
        };
        let mut candidates: Vec<usize> = (0..end.max(0) as usize).collect();
        candidates.sort_by(|&a, &b| cmp_f64_pair(marginal(a), marginal(b)));
        let mut positive: Vec<(f64, usize)> =
            rates.iter().enumerate().filter(|(_, r)| **r > 0.0).map(|(i, r)| (*r, i)).collect();
        positive.sort_by(|a, b| a.0.partial_cmp(&b.0).unwrap_or(std::cmp::Ordering::Equal));
        if positive.is_empty() {
            return schedule;
        }
        let max_stored = ev.max_charge_power_w * self.slot_h * ev.charging_efficiency;
        let mut remaining = required;
        for slot in candidates {
            let required_rate = remaining / py_max(max_stored, 1e-9);
            let (rate, idx) =
                positive.iter().find(|it| it.0 >= required_rate).copied().unwrap_or(positive[positive.len() - 1]);
            schedule[slot] = idx as i64;
            remaining -= max_stored * rate;
            if remaining <= 1e-9 {
                break;
            }
        }
        schedule
    }

    fn heuristic_appliance_genes(&self) -> Vec<i64> {
        self.rc
            .layout
            .iter()
            .map(|g| {
                let cost = |pos: &usize| {
                    let slot = g.allowed_start_slots[*pos];
                    if self.pv[slot] > self.load[slot] { self.feed_in[slot] } else { self.prices[slot] }
                };
                let positions: Vec<usize> = (0..g.allowed_start_slots.len()).collect();
                py_argmin_by(&positions, cost) as i64
            })
            .collect()
    }

    /// `_grid_export_shift_candidates(individual, max_sources)`.
    fn grid_export_shift_candidates(&mut self, ind: &[i64], max_sources: usize) -> Vec<Vec<i64>> {
        let export_states = self.layout.grid_export_states.clone();
        let Some(self_state) = self.layout.self_consumption_state else {
            return Vec::new();
        };
        if export_states.is_empty() {
            return Vec::new();
        }
        if self.feed_in.len() < self.ce || self.pv.len() < self.ce {
            return Vec::new();
        }
        let mut sources: Vec<usize> = (0..self.ce).filter(|&s| export_states.contains(&ind[s])).collect();
        let feed_in = &self.feed_in;
        sources.sort_by(|&a, &b| cmp_f64_pair((feed_in[a], -(a as f64)), (feed_in[b], -(b as f64))));
        let len_bat = self.bat_possible_charge_values.len() as i64;
        let mut candidates: Vec<Vec<i64>> = Vec::new();
        let mut seen: HashSet<Vec<i64>> = HashSet::default();
        let mut viable = 0usize;
        for source in sources {
            let targets = self.energy_shift_target_slots(ind, source);
            if targets.is_empty() {
                continue;
            }
            viable += 1;
            let mut counts: Vec<usize> = [2usize, 4, 6, 8, 10, 12].iter().map(|&c| targets.len().min(c)).collect();
            counts.sort_unstable();
            counts.dedup();
            for count in counts {
                let mut cand = ind.to_vec();
                cand[source] = self_state;
                for &t in targets.iter().take(count) {
                    cand[t] = if self.pv[t] > 0.0 { self_state } else { len_bat };
                }
                if seen.contains(&cand) {
                    continue;
                }
                seen.insert(cand.clone());
                candidates.push(cand);
            }
            if viable >= max_sources {
                break;
            }
        }
        candidates
    }

    /// `_educated_guess_individuals(target_count)`.
    fn educated_guess_individuals(&mut self, target_count: i64) -> Vec<Vec<i64>> {
        if target_count <= 0 {
            return Vec::new();
        }
        let slots = self.ce;
        let len_bat = self.bat_possible_charge_values.len() as i64;
        let idle = 0i64;
        let discharge_state = len_bat;
        let ac_charge_state = 3 * len_bat - 1;
        let dc_allowed = self.layout.dc_allowed_state;
        let export_state = self.layout.grid_export_state;
        let self_state = self.layout.self_consumption_state;
        let future_prices: Vec<f64> = self.prices[0..slots].to_vec();
        let future_feed_in: Vec<f64> = self.feed_in[0..slots].to_vec();
        let high_import = numpy::quantile(&future_prices, 0.70);
        let low_import = numpy::quantile(&future_prices, 0.25);
        let feed_spread = if future_feed_in.is_empty() { 0.0 } else { numpy::ptp(&future_feed_in) };
        let ev_price = self.heuristic_ev_schedule(false);
        let ev_pv = self.heuristic_ev_schedule(true);
        let appliance_genes = self.heuristic_appliance_genes();
        let optimize_ev = self.optimize_ev;
        let compose = |battery: &[i64], ev: &[i64]| -> Vec<i64> {
            let mut v = battery.to_vec();
            if optimize_ev {
                v.extend_from_slice(ev);
            }
            v.extend_from_slice(&appliance_genes);
            v
        };
        let mut unique: Vec<Vec<i64>> = Vec::new();
        let mut unique_set: HashSet<Vec<i64>> = HashSet::default();
        let add_guess = |battery: &[i64], ev: &[i64], unique: &mut Vec<Vec<i64>>, set: &mut HashSet<Vec<i64>>| {
            let g = compose(battery, ev);
            if set.insert(g.clone()) {
                unique.push(g);
            }
        };
        let prices = self.prices.clone();
        let feed_in = self.feed_in.clone();
        let pv = self.pv.clone();
        let load = self.load.clone();
        let policy_guess =
            |import_quantile: f64, export_quantile: Option<f64>, ratio: f64, allow_ac: bool| -> Vec<i64> {
                let import_threshold = numpy::quantile(&future_prices, import_quantile);
                let export_threshold = match export_quantile {
                    Some(q) if !future_feed_in.is_empty() => numpy::quantile(&future_feed_in, q),
                    _ => f64::INFINITY,
                };
                let low_price_threshold = numpy::quantile(&future_prices, py_max(0.05, 1.0 - import_quantile));
                let mut g = vec![idle; slots];
                for slot in 0..slots {
                    let high_feed_in = export_quantile.is_some()
                        && export_state.is_some()
                        && feed_spread > 1e-12
                        && feed_in[slot] > 0.0
                        && feed_in[slot] >= export_threshold;
                    let pv_surplus = pv[slot] > load[slot] * ratio;
                    if high_feed_in && export_state.is_some() {
                        g[slot] = export_state.unwrap_or(idle);
                    } else if self_state.is_some() && pv[slot] > 0.0 && load[slot] > 0.0 {
                        g[slot] = self_state.unwrap_or(idle);
                    } else if dc_allowed.is_some() && pv_surplus {
                        g[slot] = dc_allowed.unwrap_or(idle);
                    } else if allow_ac && prices[slot] <= low_price_threshold {
                        g[slot] = ac_charge_state;
                    } else if prices[slot] >= import_threshold && load[slot] > pv[slot] {
                        g[slot] = discharge_state;
                    }
                }
                g
            };
        add_guess(&vec![idle; slots], &ev_price, &mut unique, &mut unique_set);
        add_guess(&policy_guess(0.70, None, 1.0, false), &ev_pv, &mut unique, &mut unique_set);
        if self.optimize_battery_grid_export && !future_feed_in.is_empty() {
            for q in EDUCATED_GUESS_EXPORT_QUANTILES {
                let export_guess = policy_guess(0.70, Some(q), 1.0, false);
                add_guess(&export_guess, &ev_pv, &mut unique, &mut unique_set);
                let shifted = self.grid_export_shift_candidates(&export_guess, 2);
                for s in shifted.iter().take(6) {
                    add_guess(s, &ev_pv, &mut unique, &mut unique_set);
                }
            }
        }
        let ac_arbitrage_possible =
            self.rc.inverter.as_ref().is_some_and(|i| i.max_ac_charge_power_w.is_none_or(|m| m > 0.0));
        if ac_arbitrage_possible {
            let mut arb = vec![idle; slots];
            for slot in 0..slots {
                if prices[slot] <= low_import {
                    arb[slot] = ac_charge_state;
                } else if prices[slot] >= high_import {
                    arb[slot] = discharge_state;
                }
            }
            add_guess(&arb, &ev_price, &mut unique, &mut unique_set);
        }
        let attempts = (target_count * 20).max(100);
        for _ in 0..attempts {
            let export_quantile = if self.optimize_battery_grid_export && feed_spread > 1e-12 {
                Some(self.rng.uniform(0.50, 0.98))
            } else {
                None
            };
            let import_q = self.rng.uniform(0.55, 0.95);
            let ratio = self.rng.uniform(0.80, 1.20);
            let allow_ac = ac_arbitrage_possible && self.rng.random() < 0.35;
            let mut randomized = policy_guess(import_q, export_quantile, ratio, allow_ac);
            let future_slots: Vec<usize> = (0..slots).collect();
            let perturbations = self.rng.randint(1, 2.max(future_slots.len() as i64 / 12));
            let k = (perturbations as usize).min(future_slots.len());
            for idx in self.rng.sample_indices(future_slots.len(), k) {
                let slot = future_slots[idx];
                if randomized[slot] != idle {
                    randomized[slot] = idle;
                } else if self_state.is_some() && pv[slot] > 0.0 && load[slot] > 0.0 {
                    randomized[slot] = self_state.unwrap_or(idle);
                } else if dc_allowed.is_some() && pv[slot] > load[slot] {
                    randomized[slot] = dc_allowed.unwrap_or(idle);
                } else if load[slot] > pv[slot] && prices[slot] >= high_import {
                    randomized[slot] = discharge_state;
                }
            }
            if self.rng.random() < 0.5 {
                self.mutate_energy_shift(&mut randomized);
            }
            let ev = if self.rng.random() < 0.5 { &ev_pv } else { &ev_price };
            add_guess(&randomized, ev, &mut unique, &mut unique_set);
            if unique.len() as i64 >= target_count {
                break;
            }
        }
        unique.truncate(target_count as usize);
        unique
    }

    /// `_mutated_warm_start_neighbors(start_solution, count)`.
    fn mutated_warm_start_neighbors(&mut self, start: &[i64], count: i64) -> Vec<Vec<i64>> {
        let original: Vec<i64> = start.to_vec();
        let mut seen: HashSet<Vec<i64>> = HashSet::default();
        seen.insert(original.clone());
        let mut neighbors: Vec<Vec<i64>> = Vec::new();
        for _ in 0..(count * 10).max(1) {
            let mut n = original.clone();
            self.mutate(&mut n);
            if seen.contains(&n) {
                continue;
            }
            seen.insert(n.clone());
            neighbors.push(n);
            if neighbors.len() as i64 >= count {
                break;
            }
        }
        neighbors
    }

    // ------------------------------------------------------------------ Evaluation

    /// Evaluates genomes in parallel (result independent of the thread count).
    fn eval_many(&mut self, genomes: Vec<Vec<i64>>) -> Vec<(Vec<i64>, Evaluation)> {
        let t0 = std::time::Instant::now();
        let r = self.eval_many_inner(genomes);
        self.eval_ns += t0.elapsed().as_nanos() as u64;
        r
    }

    fn eval_many_inner(&mut self, genomes: Vec<Vec<i64>>) -> Vec<(Vec<i64>, Evaluation)> {
        let n = genomes.len();
        if n == 0 {
            return Vec::new();
        }
        let threads = self.threads.min(n).max(1);
        if threads == 1 {
            let rc = &self.rc;
            let ev = &mut self.evaluators[0];
            return genomes
                .into_iter()
                .map(|mut g| {
                    let e = ev.evaluate(rc, &mut g);
                    (g, e)
                })
                .collect();
        }
        let rc = &self.rc;
        let mut work: Vec<(usize, Vec<i64>)> = genomes.into_iter().enumerate().collect();
        let chunk = n.div_ceil(threads);
        let mut out: Vec<Option<(Vec<i64>, Evaluation)>> = vec![None; n];
        std::thread::scope(|scope| {
            let mut handles = Vec::new();
            let mut rest: &mut [(usize, Vec<i64>)] = &mut work;
            let mut evs: &mut [Evaluator] = &mut self.evaluators[..threads];
            while !rest.is_empty() {
                let take = chunk.min(rest.len());
                let (head, tail) = rest.split_at_mut(take);
                rest = tail;
                let (e0, etail) = evs.split_at_mut(1);
                evs = etail;
                let ev = &mut e0[0];
                handles.push(scope.spawn(move || {
                    head.iter_mut()
                        .map(|(i, g)| {
                            let e = ev.evaluate(rc, g);
                            (*i, std::mem::take(g), e)
                        })
                        .collect::<Vec<_>>()
                }));
            }
            for h in handles {
                if let Ok(res) = h.join() {
                    for (i, g, e) in res {
                        out[i] = Some((g, e));
                    }
                }
            }
        });
        out.into_iter().map(|o| o.expect("evaluation result")).collect()
    }

    fn apply(&self, ind: &IRef, genome: Vec<i64>, e: &Evaluation) {
        let mut i = ind.borrow_mut();
        i.genes = genome;
        i.fitness = Some(e.fitness);
        i.extra = e.extra;
    }

    /// `_evaluate_invalid(population)` (serial Python semantics including the cache).
    fn evaluate_invalid(&mut self, population: &[IRef]) -> usize {
        let invalid: Vec<IRef> = population.iter().filter(|i| i.borrow().fitness.is_none()).cloned().collect();
        let n = invalid.len();
        if self.cache.enabled {
            // Speculatively evaluate all missing keys in parallel, then replay serially.
            let mut wanted: Vec<Vec<i64>> = Vec::new();
            let mut wanted_set: HashSet<Vec<i64>> = HashSet::default();
            for ind in &invalid {
                let g = ind.borrow().genes.clone();
                if self.cache.get(&g).is_none() && wanted_set.insert(g.clone()) {
                    wanted.push(g);
                }
            }
            let results = self.eval_many(wanted.clone());
            let mut spec: HashMap<Vec<i64>, (Vec<i64>, Evaluation)> =
                HashMap::with_capacity_and_hasher(results.len(), Default::default());
            for (k, r) in wanted.into_iter().zip(results) {
                spec.insert(k, r);
            }
            for ind in &invalid {
                let key = ind.borrow().genes.clone();
                self.evaluate_cached(ind, key, &mut spec);
            }
            return n;
        }
        // Without cache: identical genomes of the batch only once.
        let mut groups: Vec<Vec<IRef>> = Vec::new();
        let mut by_genome: HashMap<Vec<i64>, usize> = HashMap::default();
        for ind in &invalid {
            let g = ind.borrow().genes.clone();
            match by_genome.get(&g) {
                Some(&gi) => groups[gi].push(ind.clone()),
                None => {
                    by_genome.insert(g, groups.len());
                    groups.push(vec![ind.clone()]);
                }
            }
        }
        let firsts: Vec<Vec<i64>> = groups.iter().map(|g| g[0].borrow().genes.clone()).collect();
        let results = self.eval_many(firsts);
        for (group, (genome, e)) in groups.iter().zip(results) {
            for ind in group {
                self.apply(ind, genome.clone(), &e);
            }
        }
        n
    }

    /// `evaluate(individual)` with cache; `spec` holds precomputed results.
    fn evaluate_cached(&mut self, ind: &IRef, key: Vec<i64>, spec: &mut HashMap<Vec<i64>, (Vec<i64>, Evaluation)>) {
        if let Some(entry) = self.cache.get(&key) {
            let mut i = ind.borrow_mut();
            i.genes = unpack_genes(&entry.genome);
            i.extra = Some(entry.extra);
            i.fitness = Some(entry.fitness);
            self.cache.hits += 1;
            return;
        }
        self.cache.misses += 1;
        let (genome, e) = match spec.get(&key) {
            Some(r) => r.clone(),
            None => {
                let mut r = self.eval_many(vec![key.clone()]);
                r.pop().expect("one result")
            }
        };
        self.apply(ind, genome.clone(), &e);
        if let Some(extra) = e.extra {
            let packed = pack_genes(&genome);
            let entry = Rc::new(CacheEntry { genome: packed.clone(), fitness: e.fitness, extra });
            self.cache.insert(pack_genes(&key), entry.clone());
            self.cache.insert(packed, entry);
            self.cache.evict();
        }
    }

    /// `toolbox.evaluate(individual)` for a single individual (local search).
    fn evaluate_one(&mut self, ind: &IRef) {
        let key = ind.borrow().genes.clone();
        if self.cache.enabled {
            let mut spec = HashMap::default();
            self.evaluate_cached(ind, key, &mut spec);
        } else {
            let mut r = self.eval_many(vec![key]);
            let (g, e) = r.pop().expect("one result");
            self.apply(ind, g, &e);
        }
    }

    fn population_diversity(&self, population: &[IRef]) -> f64 {
        if population.is_empty() {
            return 0.0;
        }
        let set: HashSet<Vec<i64>> = population.iter().map(|i| i.borrow().genes.clone()).collect();
        set.len() as f64 / population.len() as f64
    }

    // ------------------------------------------------------------------ Selection

    fn fresh_population(&mut self, count: i64, educated_fraction: f64) -> Vec<IRef> {
        if count <= 0 {
            return Vec::new();
        }
        let educated_target = count.min((count as f64 * educated_fraction + 0.5) as i64);
        let educated = self.educated_guess_individuals(educated_target);
        let mut fresh: Vec<IRef> = educated.into_iter().take(count as usize).map(new_ind).collect();
        let rest = count as usize - fresh.len();
        fresh.extend(self.population(rest));
        fresh
    }

    fn best_unique(&self, population: &[IRef], count: usize) -> Vec<IRef> {
        let mut selected = Vec::new();
        let mut seen: HashSet<Vec<i64>> = HashSet::default();
        for c in sel_best(population, population.len()) {
            let key = c.borrow().genes.clone();
            if seen.contains(&key) {
                continue;
            }
            seen.insert(key);
            selected.push(c);
            if selected.len() >= count {
                break;
            }
        }
        selected
    }

    fn reserve_immigrant_slots(
        &self,
        candidates: &[IRef],
        selected: &mut [IRef],
        keys: &[Vec<i64>],
        best_key: &[i64],
    ) -> bool {
        let protected: Vec<IRef> = candidates.iter().filter(|c| c.borrow().protection > 0).cloned().collect();
        if protected.is_empty() {
            return false;
        }
        let limit = 1usize.max((selected.len() as f64 * IMMIGRANT_PROTECTION_FRACTION) as usize);
        let chosen: HashSet<*const RefCell<Ind>> = selected.iter().map(Rc::as_ptr).collect();
        let seated = protected.iter().filter(|c| chosen.contains(&Rc::as_ptr(c))).count();
        let missing: Vec<IRef> = protected.iter().filter(|c| !chosen.contains(&Rc::as_ptr(c))).cloned().collect();
        if seated >= limit || missing.is_empty() {
            return false;
        }
        let mut evictable: Vec<usize> = (0..selected.len())
            .filter(|&i| keys[i].as_slice() != best_key && selected[i].borrow().protection <= 0)
            .collect();
        let fit: Vec<f64> = selected.iter().map(|s| s.borrow().fitness.unwrap_or(f64::INFINITY)).collect();
        evictable.sort_by(|&a, &b| fit[b].partial_cmp(&fit[a]).unwrap_or(std::cmp::Ordering::Equal));
        let mut reassigned = false;
        for (imm, &idx) in missing.iter().take(limit - seated).zip(evictable.iter()) {
            selected[idx] = imm.clone();
            reassigned = true;
        }
        reassigned
    }

    fn age_immigrant_protection(population: &[IRef]) {
        for ind in population {
            let mut i = ind.borrow_mut();
            if i.protection > 0 {
                i.protection -= 1;
            }
        }
    }

    /// `_select_diverse(candidates, count)`.
    fn select_diverse(&mut self, candidates: &[IRef], count: usize) -> Vec<IRef> {
        if candidates.is_empty() || count == 0 {
            return Vec::new();
        }
        // selTournament(candidates, count, tournsize=3)
        let mut selected: Vec<IRef> = Vec::with_capacity(count);
        for _ in 0..count {
            let mut best: Option<IRef> = None;
            for _ in 0..3 {
                let c = candidates[self.rng.choice_index(candidates.len())].clone();
                best = match best {
                    None => Some(c),
                    Some(b) => {
                        let (fc, fb) =
                            (c.borrow().fitness.unwrap_or(f64::INFINITY), b.borrow().fitness.unwrap_or(f64::INFINITY));
                        if fit_gt(fc, fb) { Some(c) } else { Some(b) }
                    }
                };
            }
            selected.push(best.expect("aspirant"));
        }
        // selBest(candidates, 1)[0]: first individual with the lowest fitness (stable sort).
        let best = {
            let mut bi = 0usize;
            let mut bf = candidates[0].borrow().fitness.unwrap_or(f64::INFINITY);
            for (i, c) in candidates.iter().enumerate().skip(1) {
                let f = c.borrow().fitness.unwrap_or(f64::INFINITY);
                if (-f) > (-bf) {
                    bi = i;
                    bf = f;
                }
            }
            candidates[bi].clone()
        };
        let best_key = best.borrow().genes.clone();
        let mut keys: Vec<Vec<i64>> = selected.iter().map(|s| s.borrow().genes.clone()).collect();
        if !keys.contains(&best_key) {
            let fit: Vec<f64> = selected.iter().map(|s| s.borrow().fitness.unwrap_or(f64::INFINITY)).collect();
            let worst = py_argmax(&fit);
            selected[worst] = best.clone();
            keys[worst] = best_key.clone();
        }
        if self.reserve_immigrant_slots(candidates, &mut selected, &keys, &best_key) {
            keys = selected.iter().map(|s| s.borrow().genes.clone()).collect();
        }
        let target_unique = count.min(1usize.max((count as f64 * SELECTION_DIVERSITY_FLOOR + 0.999999) as usize));
        // defaultdict(int): entries are kept even at 0.
        let mut key_counts: HashMap<Vec<i64>, i64> = HashMap::default();
        for k in &keys {
            *key_counts.entry(k.clone()).or_insert(0) += 1;
        }
        if key_counts.len() >= target_unique {
            return selected;
        }
        for cand in sel_best(candidates, candidates.len()) {
            let ck = cand.borrow().genes.clone();
            if key_counts.contains_key(&ck) {
                continue;
            }
            let replaceable: Vec<usize> =
                (0..keys.len()).filter(|&i| key_counts.get(&keys[i]).copied().unwrap_or(0) > 1).collect();
            if replaceable.is_empty() {
                break;
            }
            let fit: Vec<f64> =
                replaceable.iter().map(|&i| selected[i].borrow().fitness.unwrap_or(f64::INFINITY)).collect();
            let ri = replaceable[py_argmax(&fit)];
            let replaced = keys[ri].clone();
            if let Some(c) = key_counts.get_mut(&replaced) {
                *c -= 1;
            }
            selected[ri] = cand.clone();
            keys[ri] = ck.clone();
            key_counts.insert(ck, 1);
            if key_counts.len() >= target_unique {
                break;
            }
        }
        selected
    }

    /// `_make_offspring(population, count, mutation_probability)`.
    fn make_offspring(&mut self, population: &[IRef], count: i64, mutation_probability: f64) -> Vec<IRef> {
        let mut offspring = Vec::with_capacity(count.max(0) as usize);
        for _ in 0..count.max(0) {
            let mut child = population[self.rng.choice_index(population.len())].borrow().genes.clone();
            let mut crossed = false;
            if child.len() > 1 && population.len() > 1 && self.rng.random() < CROSSOVER_PROBABILITY {
                let mut partner = population[self.rng.choice_index(population.len())].borrow().genes.clone();
                self.cx_two_point(&mut child, &mut partner);
                crossed = true;
            }
            if !crossed || self.rng.random() < mutation_probability {
                self.mutate(&mut child);
            }
            offspring.push(new_ind(child));
        }
        offspring
    }

    fn compile_stats(population: &[IRef], h: &mut FitnessHistory) {
        let vals: Vec<f64> = population.iter().map(|i| i.borrow().fitness.unwrap_or(f64::NAN)).collect();
        h.min.push(numpy::min(&vals));
        h.avg.push(numpy::mean(&vals));
        h.max.push(numpy::max(&vals));
    }

    /// `_evolve_population_adaptive`.
    fn evolve(
        &mut self,
        mut population: Vec<IRef>,
        mu: i64,
        lambda: i64,
        ngen: i64,
        hof: &mut HallOfFame,
        h: &mut FitnessHistory,
    ) -> Vec<IRef> {
        self.evaluate_invalid(&population);
        hof.update(&population);
        let mut best_fitness = hof.best_fitness();
        let mut stagnation: i64 = 0;
        let mut diversity = self.population_diversity(&population);
        Self::compile_stats(&population, h);
        h.generation.push(0);
        h.diversity.push(diversity);
        h.stagnation.push(stagnation);
        h.immigrants.push(0);
        h.restart.push(0);
        let mut diversity_boost_active;
        let mut soft_restarts = 0i64;
        let mut total_immigrants = 0i64;
        let mut minimum_diversity = diversity;
        for generation in 1..=ngen {
            diversity = self.population_diversity(&population);
            let soft_restart = stagnation >= SOFT_RESTART_GENERATIONS || diversity < SOFT_RESTART_DIVERSITY_THRESHOLD;
            let mut immigrants = 0i64;
            if soft_restart {
                let survivor_count = 1.max((mu as f64 * SOFT_RESTART_SURVIVOR_FRACTION) as i64) as usize;
                let survivors = self.best_unique(&population, survivor_count);
                immigrants = mu - survivors.len() as i64;
                let fresh = timed!(self, 0, self.fresh_population(immigrants, 0.40));
                population = survivors;
                population.extend(fresh);
                self.evaluate_invalid(&population);
                hof.update(&population);
                soft_restarts += 1;
                total_immigrants += immigrants;
                stagnation = 0;
                diversity_boost_active = false;
                let _ = diversity_boost_active;
                Self::age_immigrant_protection(&population);
            } else {
                let diversity_boost = stagnation >= STAGNATION_GENERATIONS || diversity < DIVERSITY_BOOST_THRESHOLD;
                diversity_boost_active = diversity_boost;
                let _ = diversity_boost_active;
                let mp = if diversity_boost { STAGNATION_MUTATION_PROBABILITY } else { MUTATION_PROBABILITY };
                if diversity_boost {
                    immigrants = 1.max((lambda as f64 * IMMIGRANT_FRACTION + 0.5) as i64);
                }
                let mut offspring = timed!(self, 2, self.make_offspring(&population, lambda - immigrants, mp));
                let fresh = timed!(self, 0, self.fresh_population(immigrants, 0.50));
                for f in &fresh {
                    f.borrow_mut().protection = IMMIGRANT_PROTECTION_GENERATIONS;
                }
                offspring.extend(fresh);
                self.evaluate_invalid(&offspring);
                timed!(self, 4, hof.update(&offspring));
                let mut candidates = population.clone();
                candidates.extend(offspring);
                population = timed!(self, 1, self.select_diverse(&candidates, mu as usize));
                Self::age_immigrant_protection(&population);
                total_immigrants += immigrants;
            }
            let current_best = hof.best_fitness();
            if current_best < best_fitness - 1e-9 {
                best_fitness = current_best;
                stagnation = 0;
            } else if !soft_restart {
                stagnation += 1;
            }
            diversity = timed!(self, 3, self.population_diversity(&population));
            minimum_diversity = minimum_diversity.min(diversity);
            if diversity < minimum_diversity {
                minimum_diversity = diversity;
            }
            Self::compile_stats(&population, h);
            h.generation.push(generation);
            h.diversity.push(diversity);
            h.stagnation.push(stagnation);
            h.immigrants.push(immigrants);
            h.restart.push(i64::from(soft_restart));
        }
        h.soft_restarts = soft_restarts;
        h.total_immigrants = total_immigrants;
        h.minimum_diversity = minimum_diversity;
        h.final_diversity = self.population_diversity(&population);
        h.final_stagnation = stagnation;
        population
    }

    /// `_locally_improve_grid_export(individual, max_evaluations)`.
    fn locally_improve(&mut self, hof0: &Ind, max_evaluations: i64) -> (Ind, i64, i64, f64, f64) {
        let mut best = Ind { genes: hof0.genes.clone(), fitness: hof0.fitness, extra: hof0.extra, protection: 0 };
        if best.fitness.is_none() {
            let r = new_ind(best.genes.clone());
            self.evaluate_one(&r);
            best = r.borrow().clone();
            best.protection = 0;
        }
        let initial = best.fitness.unwrap_or(f64::NAN);
        let mut evaluations = 0i64;
        let mut improvements = 0i64;
        for _ in 0..LOCAL_SEARCH_MAX_PASSES {
            let mut pass_best: Option<Ind> = None;
            let cands = self.grid_export_shift_candidates(&best.genes, 6);
            for genome in cands {
                if evaluations >= max_evaluations {
                    break;
                }
                let c = new_ind(genome);
                self.evaluate_one(&c);
                evaluations += 1;
                let cf = c.borrow().fitness.unwrap_or(f64::INFINITY);
                let pb = pass_best
                    .as_ref()
                    .map_or(best.fitness.unwrap_or(f64::INFINITY), |p| p.fitness.unwrap_or(f64::INFINITY));
                if cf < pb - 1e-9 {
                    let mut ci = c.borrow().clone();
                    ci.protection = 0;
                    pass_best = Some(ci);
                }
            }
            match pass_best {
                None => break,
                Some(p) => {
                    best = p;
                    improvements += 1;
                }
            }
            if evaluations >= max_evaluations {
                break;
            }
        }
        let final_value = best.fitness.unwrap_or(f64::NAN);
        (best, evaluations, improvements, initial, final_value)
    }
}

/// Result of the run preparation, before the GA starts.
struct Prepared {
    params: GeneticOptimizationParameters,
    appliances: Vec<HomeAppliance>,
    layout: Vec<ApplianceGene>,
    battery: Option<BatterySpec>,
    ev: Option<BatterySpec>,
    inverter: Option<Inverter>,
}

/// What `build` returns besides the optimizer.
pub struct Built {
    prep: Prepared,
    generations: i64,
    sc_tail: SelfConsumption,
    slot0: DateTime<Tz>,
    sds: i64,
    slot_h: f64,
    slots_per_hour: i64,
    control_slots: usize,
    ce: usize,
    direct_marketing: bool,
    optimize_ev: bool,
    interval_sec: i64,
}

/// `optimize_ems(parameters, ngen, individuals)` – a complete run.
pub fn optimize_ems(
    parameters: &GeneticOptimizationParameters,
    cfg: &RunConfig,
    ngen: Option<i64>,
    individuals: Option<i64>,
    numeric: NumericProfile,
) -> Result<CoreSolution, String> {
    let t0 = std::time::Instant::now();
    let (mut opt, b) = build(parameters, cfg, ngen, numeric)?;
    opt.prof[6] = t0.elapsed().as_nanos() as u64;
    run_built(&mut opt, b, individuals)
}

/// Prepares a run (everything up to `optimize()`), e.g. for P1 single evaluations.
pub fn build<'a>(
    parameters: &GeneticOptimizationParameters,
    cfg: &'a RunConfig,
    ngen: Option<i64>,
    numeric: NumericProfile,
) -> Result<(Optimizer<'a>, Built), String> {
    parameters.validate()?;
    let g = &cfg.genetic;
    let mut interval_sec = g.interval_sec;
    if interval_sec != 900 && interval_sec != 3600 {
        interval_sec = 3600;
    }
    let slot_h = interval_sec as f64 / 3600.0;
    let slots_per_hour = 3600 / interval_sec;
    let control_slots = (g.horizon_hours * slots_per_hour).max(0) as usize;
    let ce = control_slots;
    if ce == 0 {
        return Err("GENETIC control horizon must be positive.".into());
    }
    let prediction_slots = (cfg.prediction_hours * slots_per_hour).max(0) as usize;
    let tail_requested = (g.tail_horizon_hours * slots_per_hour).max(0) as usize;
    let tail_slots = tail_requested.min(prediction_slots.saturating_sub(control_slots));
    let direct_marketing = cfg.direct_marketing_enabled;
    let slot0 = cfg.start_datetime;
    let sds = start_day_slot(&slot0, slot_h);
    let params = parameters_for_slot_grid(parameters, interval_sec, cfg.prediction_hours, sds)?;
    let appliance_params = params.resolved_home_appliances();
    let generations = ngen.or(g.generations).unwrap_or(400);

    // Devices
    let battery = params.pv_battery.as_ref().map(|p| BatterySpec::from_pv(p, slot_h));
    let ev = params.ev.as_ref().map(|p| BatterySpec::from_ev(p, slot_h));
    let mut optimize_ev = false;
    let mut ev_rates: Vec<f64> = vec![1.0];
    if let Some(p) = &params.ev {
        optimize_ev = p.min_soc_percentage > p.initial_soc_percentage;
        ev_rates = if let Some(r) = &p.charge_rates {
            r.clone()
        } else if let Some(r) = &cfg.fallback_ev_charge_rates {
            r.clone()
        } else {
            vec![0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
        };
        if optimize_ev && ev_rates.is_empty() {
            // Python fails in `random.randint(0, -1)` when it builds the first genome.
            return Err("EV charge rates are empty".into());
        }
    }
    let bat_rates: Vec<f64> = match (&params.pv_battery, &cfg.fallback_battery_charge_rates) {
        (Some(p), _) if p.charge_rates.as_ref().is_some_and(|r| !r.is_empty()) => {
            let r: Vec<f64> =
                p.charge_rates.as_ref().map(|r| r.iter().copied().filter(|v| *v > 0.0).collect()).unwrap_or_default();
            if r.is_empty() { vec![1.0] } else { r }
        }
        (_, Some(f)) if !f.is_empty() => {
            let r: Vec<f64> = f.iter().copied().filter(|v| *v > 0.0).collect();
            if r.is_empty() { vec![1.0] } else { r }
        }
        _ => vec![1.0],
    };
    let export_rates: Option<Vec<f64>> = match (&params.pv_battery, &cfg.fallback_battery_grid_export_rates) {
        (Some(p), _) if p.grid_export_rates.as_ref().is_some_and(|r| !r.is_empty()) => p.grid_export_rates.clone(),
        (_, Some(f)) => Some(f.clone()),
        _ => None,
    };
    let mut bat_export: Vec<f64> = export_rates.unwrap_or_else(|| vec![1.0]).into_iter().filter(|r| *r > 0.0).collect();
    bat_export.sort_by(|a, b| b.partial_cmp(a).unwrap_or(std::cmp::Ordering::Equal));
    if bat_export.is_empty() {
        bat_export = vec![1.0];
    }

    // Household appliances
    let appliances: Vec<HomeAppliance> =
        appliance_params.iter().map(|a| HomeAppliance::new(a, ce, slot_h, cfg.timezone)).collect::<Result<_, _>>()?;
    let layout = homeappliance::build_layout(&appliances, &slot0, ce as i64)?;
    {
        let mut probe = vec![0i64; layout.len()];
        StartDecoder::default().decode(&layout, &appliances, &mut probe, slot_h, ce)?;
    }

    // EV deadline
    let slot_seconds = slot_h * 3600.0;
    if ce == 0 {
        optimize_ev = false;
    }
    let ev_deadline: Option<i64> = params.ev.as_ref().and_then(|p| {
        let mut cands: Vec<i64> = Vec::new();
        if let Some(d) = &p.min_soc_deadline_datetime {
            let seconds = total_seconds(&d.resolve(cfg.timezone), &slot0);
            cands.push((seconds / slot_seconds + 1e-9).floor() as i64);
        }
        if let Some(dur) = p.min_soc_max_duration_h {
            cands.push((dur * 3600.0 / slot_seconds + 1e-9).floor() as i64);
        }
        let dl = *cands.iter().min()?;
        if dl >= ce as i64 {
            return None;
        }
        Some(dl.max(0))
    });
    let full_charge_slot: Option<i64> = params.pv_battery.as_ref().and_then(|b| {
        b.full_charge_soc_percentage?;
        match &b.full_charge_deadline_datetime {
            None => Some(ce as i64),
            Some(d) => {
                let seconds = total_seconds(&d.resolve(cfg.timezone), &slot0);
                Some(((seconds / slot_seconds + 1e-9).floor() as i64).max(0).min(ce as i64))
            }
        }
    });
    let full_charge_target =
        full_charge_slot.and(params.pv_battery.as_ref().and_then(|b| b.full_charge_soc_percentage));

    // Inverter
    let inverter = match &params.inverter {
        Some(ip) => {
            let inv = Inverter::new(ip, slot_h);
            if let Some(b) = &battery {
                if inv.battery_id.as_deref() != Some(b.device_id.as_str()) {
                    return Err(format!(
                        "Battery ID mismatch - {:?} is configured; got {}.",
                        inv.battery_id, b.device_id
                    ));
                }
            }
            Some(inv)
        }
        None => None,
    };
    let prep = Prepared { params, appliances, layout, battery, ev, inverter };

    // Simulation series (full length for validation and terminal value)
    let load_full = prep.params.ems.total_load.clone();
    let pv_full = prep.params.ems.pv_forecast_wh.clone();
    let price_full = prep.params.ems.electricity_price_per_wh.clone();
    let feed_full = match &prep.params.ems.feed_in_tariff_per_wh {
        FeedInTariff::Series(s) => s.clone(),
        FeedInTariff::Scalar(v) => vec![*v; load_full.len()],
    };
    // No length check here: like Python, providers may deliver different lengths; the
    // common finite prefix decides the tail (`_validate_forecast_availability`).
    // _validate_forecast_availability
    let required = ce;
    let requested = required + tail_slots;
    let mut available = requested;
    let mut limiting: Vec<&str> = Vec::new();
    for (name, values) in
        [("load", &load_full), ("pv", &pv_full), ("import price", &price_full), ("feed-in tariff", &feed_full)]
    {
        let mut end = values.len().min(requested);
        if let Some(i) = values[..end].iter().position(|v| !v.is_finite()) {
            end = i;
        }
        if end < required {
            return Err(format!(
                "Incomplete control forecast: {name} ends at slot {end}; control requires slot {required}."
            ));
        }
        if end < requested {
            limiting.push(name);
        }
        available = available.min(end);
    }
    let effective_tail_slots = available.saturating_sub(required);
    let forecast_reason = if available < requested {
        format!(
            "Tail forecast shortened: requested {} h, effective {} h; limited by {}. Continuation starts at slot {available}.",
            g.tail_horizon_hours,
            fmt_g(effective_tail_slots as f64 * slot_h),
            limiting.join(", ")
        )
    } else {
        String::new()
    };

    let optimize_dc = direct_marketing;
    let optimize_export = direct_marketing;
    let mut sc_tail = SelfConsumption::new(numeric.dot);

    // Terminal value curve
    let mut terminal_value_reason = String::new();
    let mut continuation_curve: Option<ValueCurve> = None;
    let mut tail_diag: Option<TailDiagnostics> = None;
    let mut tail_trace: Option<TailTrace> = None;
    let terminal_curve: Option<ValueCurve> = match &prep.battery {
        None => {
            terminal_value_reason = "no battery in this optimization".into();
            None
        }
        Some(bat) => {
            if g.terminal_value_mode != TerminalValueMode::Auto {
                terminal_value_reason = "terminal_value_mode is FIXED".into();
                None
            } else {
                let dc_to_ac = prep.inverter.as_ref().map(|i| i.reference_dc_to_ac_efficiency).unwrap_or(1.0);
                let max_energy = py_max(bat.max_soc_wh - bat.min_soc_wh, 0.0) * bat.discharging_efficiency * dc_to_ac;
                let window_slots = 1.max(g.terminal_value_window_hours * slots_per_hour) as usize;
                let end_slot = ce + effective_tail_slots;
                let curve = curves::build_terminal_value_curve(
                    &curves::trailing_window(&price_full, end_slot, window_slots),
                    &curves::trailing_window(&load_full, end_slot, window_slots),
                    &curves::trailing_window(&pv_full, end_slot, window_slots),
                    &curves::trailing_window(&feed_full, end_slot, window_slots),
                    max_energy,
                    bat.levelized_cost_of_storage_kwh,
                    dc_to_ac,
                    optimize_export,
                );
                continuation_curve = Some(curve.clone());
                if tail_slots > 0 && prep.inverter.is_some() {
                    let inv = prep.inverter.as_ref().expect("inverter");
                    let tp = &price_full[ce..end_slot];
                    let tt = &feed_full[ce..end_slot];
                    let minmax = |a: &[f64], f: fn(&[f64]) -> f64| if a.is_empty() { 0.0 } else { f(a) * 1000.0 };
                    tail_diag = Some(TailDiagnostics {
                        slots: tp.len(),
                        slot_hours: slot_h,
                        soc_grid_points: 101,
                        min_import_price_euro_per_kwh: minmax(tp, numpy::min),
                        max_import_price_euro_per_kwh: minmax(tp, numpy::max),
                        min_feed_in_tariff_euro_per_kwh: minmax(tt, numpy::min),
                        max_feed_in_tariff_euro_per_kwh: minmax(tt, numpy::max),
                        negative_import_price_slots: tp.iter().filter(|v| **v < 0.0).count(),
                        positive_battery_export_slots: if optimize_export {
                            tt.iter().filter(|v| **v > 0.0).count()
                        } else {
                            0
                        },
                    });
                    let (tail, trace) = curves::build_tail_value_curve(
                        bat,
                        inv,
                        &mut sc_tail,
                        tp,
                        &load_full[ce..end_slot],
                        &pv_full[ce..end_slot],
                        tt,
                        &curve,
                        &bat_rates,
                        &bat_export,
                        optimize_export,
                        numeric.interp_fma,
                    )?;
                    tail_trace = Some(trace);
                    Some(tail)
                } else {
                    if curve.energy_wh.is_empty() {
                        terminal_value_reason = format!(
                            "AUTO could not derive a curve: the last {window_slots} slots of the horizon carry no priced residual load (check the electricity price forecast) - falling back to the fixed value"
                        );
                    }
                    Some(curve)
                }
            }
        }
    };

    // Truncate to the control horizon
    let trim = |v: &[f64]| v[..control_slots.min(v.len())].to_vec();
    let load = trim(&load_full);
    let pv = trim(&pv_full);
    let prices = trim(&price_full);
    let feed_in = trim(&feed_full);

    // Fitness constants
    let terminal = match (&prep.battery, &terminal_curve) {
        (Some(_), Some(c)) if !c.energy_wh.is_empty() => {
            if c.is_tail {
                Terminal::Tail { x: c.energy_wh.clone(), y: c.value_euro.clone() }
            } else {
                Terminal::Auto { x: c.energy_wh.clone(), y: c.value_euro.clone() }
            }
        }
        _ => Terminal::Fixed(prep.params.ems.price_per_wh_battery),
    };
    let is_tail = matches!(terminal, Terminal::Tail { .. });
    let break_even = match (&prep.battery, &prep.inverter) {
        (Some(b), Some(inv)) if !is_tail => {
            let rt = inv.ac_to_dc_efficiency
                * b.charging_efficiency
                * b.discharging_efficiency
                * inv.reference_dc_to_ac_efficiency;
            let factor = cfg.genetic.penalty_float("ac_charge_break_even").unwrap_or(1.0);
            if rt > 0.0 && factor != 0.0 {
                let initial_soc_wh = (b.initial_soc_percentage / 100.0) * b.capacity_wh;
                let free = py_max(0.0, initial_soc_wh - b.min_soc_wh)
                    * b.discharging_efficiency
                    * inv.reference_dc_to_ac_efficiency;
                let n = prices.len().min(ce);
                let best_prices = ac_break_even_prices(&prices, &load, free, n);
                Some(BreakEven {
                    rt_eff: rt,
                    factor,
                    best_prices,
                    n,
                    lcos_per_wh_dc: b.levelized_cost_of_storage_kwh / 1000.0,
                })
            } else {
                None
            }
        }
        _ => None,
    };
    let full_charge = match (full_charge_slot, full_charge_target) {
        (Some(slot), Some(target)) if prep.battery.is_some() => Some(FullCharge {
            deadline_slot: slot,
            target: target as f64,
            penalty: cfg.genetic.penalty_float("battery_full_charge_miss").unwrap_or(10.0),
        }),
        _ => None,
    };
    let ev_penalty = if optimize_ev && prep.params.ev.is_some() && prep.ev.is_some() {
        Some(EvPenalty {
            penalty: cfg.genetic.penalty_float("ev_soc_miss").unwrap_or(10.0),
            min_soc: prep.params.ev.as_ref().map(|p| p.min_soc_percentage as f64).unwrap_or(0.0),
            deadline_slot: ev_deadline,
        })
    } else {
        None
    };
    let ev_extra = if prep.params.ev.is_some() && prep.ev.is_some() {
        prep.params.ev.as_ref().map(|p| p.min_soc_percentage as f64)
    } else {
        None
    };

    let mut opt = Optimizer {
        cfg,
        rc: RunConst {
            ce,
            slot_h,
            load: load.clone(),
            pv: pv.clone(),
            price: prices.clone(),
            feed_in: feed_in.clone(),
            direct_marketing,
            optimize_ev,
            optimize_dc_charge: optimize_dc,
            decode: DecodeTable::default(),
            ev_rates: if optimize_ev { ev_rates.clone() } else { Vec::new() },
            ev_zero_index: None,
            battery: prep.battery.clone(),
            ev: prep.ev.clone(),
            inverter: prep.inverter.clone(),
            appliances: prep.appliances.clone(),
            layout: prep.layout.clone(),
            worst_case: cfg.worst_case,
            terminal,
            break_even,
            full_charge,
            ev_penalty,
            ev_extra_min_soc: ev_extra,
            numeric,
        },
        rng: match cfg.genetic.seed {
            Some(s) => PyRandom::seed(s.unsigned_abs()),
            None => PyRandom::from_entropy(),
        },
        fix_seed: cfg.genetic.seed,
        evaluators: Vec::new(),
        threads: cfg.threads.max(1),
        slot_h,
        slots_per_hour,
        ce,
        control_slots,
        optimize_ev,
        optimize_dc_charge: optimize_dc,
        optimize_battery_grid_export: optimize_export,
        bat_possible_charge_values: bat_rates,
        bat_possible_grid_export_values: bat_export,
        ev_possible_charge_values: ev_rates,
        ev_soc_deadline_slot: ev_deadline,
        layout: StateLayout::default(),
        mutation_probability: 0.0,
        prices,
        feed_in,
        pv,
        load,
        energy_shift_cache: HashMap::default(),
        cache: FitnessCache::default(),
        terminal_curve: terminal_curve.clone(),
        continuation_curve,
        tail_trace,
        tail_diagnostics: tail_diag,
        terminal_value_reason,
        forecast_reason,
        effective_tail_slots,
        slot0,
        start_day_slot: sds,
        eval_ns: 0,
        prof: [0; 8],
    };
    opt.layout = opt.battery_state_layout();
    let n_states = opt.layout.total_states.max(1);
    let table: Vec<(f64, f64, f64, f64)> = (0..n_states).map(|s| opt.decode_state(s)).collect();
    opt.rc.decode = DecodeTable {
        ac: table.iter().map(|t| t.0).collect(),
        dc: table.iter().map(|t| t.1).collect(),
        dis: table.iter().map(|t| t.2).collect(),
        exp: table.iter().map(|t| t.3).collect(),
        outside_dc: if opt.layout.dc_allowed_state.is_some() { 0.0 } else { 1.0 },
    };
    opt.rc.ev_zero_index = if optimize_ev { opt.ev_zero_index() } else { None };
    let active_slots = ce.max(1) as f64;
    opt.mutation_probability = py_min(0.10, POINT_MUTATION_EXPECTED_GENES / active_slots);
    let threads = opt.threads;
    opt.evaluators = (0..threads).map(|_| Evaluator::new(&opt.rc)).collect();
    let b = Built {
        prep,
        generations,
        sc_tail,
        slot0,
        sds,
        slot_h,
        slots_per_hour,
        control_slots,
        ce,
        direct_marketing,
        optimize_ev,
        interval_sec,
    };
    Ok((opt, b))
}

/// Runs a prepared run.
pub fn run_built(opt: &mut Optimizer<'_>, b: Built, individuals: Option<i64>) -> Result<CoreSolution, String> {
    let Built {
        prep,
        generations,
        mut sc_tail,
        slot0,
        sds,
        slot_h,
        slots_per_hour,
        control_slots,
        ce,
        direct_marketing,
        optimize_ev,
        interval_sec,
    } = b;
    let cfg = opt.cfg;
    let g = &cfg.genetic;

    // Warm start
    let start_solution_dt: Option<DateTime<Tz>> = match &prep.params.start_solution_datetime {
        Some(d) => Some(d.resolve(cfg.timezone)),
        None => match (&prep.params.start_solution, &cfg.last_solution) {
            (Some(s), Some((last, dt))) if s == last => Some(*dt),
            _ => None,
        },
    };
    let start_solution = opt.start_solution_for_run_start(prep.params.start_solution.as_deref(), start_solution_dt);
    let individuals = individuals.or(g.individuals).unwrap_or(300);
    let (best, member, history) = opt.optimize(start_solution, generations, individuals);

    // Final evaluation
    let mut best_genes = best.genes.clone();
    let ok = opt.evaluators[0].evaluate_inner(&opt.rc, &mut best_genes);
    if ok.is_err() {
        return Err("final evaluation of the best solution failed".into());
    }
    let ev0 = &opt.evaluators[0];
    let result = ev0.res.clone();
    let terminal_value = opt.terminal_value_result(&mut sc_tail)?;
    let appliance_genes: Vec<i64> = best.genes[best.genes.len() - opt.rc.layout.len()..].to_vec();
    let mut ag = appliance_genes.clone();
    let starts =
        StartDecoder::default().decode(&opt.rc.layout, &opt.rc.appliances, &mut ag, slot_h, ce).unwrap_or_default();
    let mut ha_energy = Vec::new();
    let mut ha_running = Vec::new();
    let mut app_starts = Vec::new();
    let mut app_missed = Vec::new();
    for (i, a) in opt.rc.appliances.iter().enumerate() {
        let st = starts.get(i).cloned().unwrap_or_default();
        let curve = a.build_load_curve(&st).unwrap_or_else(|_| vec![0.0; ce]);
        ha_energy.push((a.device_id.clone(), curve));
        let mut sorted = st.clone();
        sorted.sort_unstable();
        ha_running.push((
            a.device_id.clone(),
            (0..control_slots).map(|slot| sorted.iter().any(|&s| s <= slot && slot < s + a.run_slots)).collect(),
        ));
        app_starts.push((
            a.device_id.clone(),
            sorted.iter().map(|&s| add_seconds(&slot0, s as f64 * a.slot_interval_seconds)).collect(),
        ));
        if a.deadline_datetime.is_some() {
            app_missed.push((a.device_id.clone(), a.deadline_missed(&st, &slot0)));
        }
    }
    let washingstart = if slots_per_hour == 1 && opt.rc.appliances.len() == 1 {
        starts.first().and_then(|s| s.iter().min()).map(|m| sds + *m as i64)
    } else {
        None
    };
    let ev0 = &opt.evaluators[0];
    let ev_charge_hours_float = if optimize_ev { ev0.ev.as_ref().map(|e| e.charge_array.clone()) } else { None };
    let export_factor = if direct_marketing { ev0.exp.clone() } else { Vec::new() };
    let ev_obj = ev0.ev.as_ref().map(|e| BatteryState {
        spec: e.s.clone(),
        soc_wh: e.soc_wh,
        charge_array: e.charge_array.clone(),
        discharge_array: e.discharge_array.clone(),
        prediction_hours: e.len(),
    });
    let evaluations: u64 = opt.evaluators.iter().map(|e| e.evaluations).sum();
    let direct_misses: u64 = opt.evaluators.iter().map(|e| e.sc.misses).sum();
    Ok(CoreSolution {
        parameters: prep.params.clone(),
        interval_seconds: interval_sec,
        start_hour: chrono::Timelike::hour(&slot0),
        extra_data: member,
        fitness_history: history,
        fixed_seed: opt.fix_seed,
        ac_charge: ev0.ac.clone(),
        dc_charge: ev0.dc.clone(),
        discharge_allowed: ev0.dis.clone(),
        battery_grid_export_allowed: export_factor.iter().map(|v| i64::from(*v > 0.0)).collect(),
        battery_grid_export_factor: export_factor,
        terminal_value,
        ev_charge_hours_float,
        result,
        home_appliance_energy_wh: ha_energy,
        home_appliance_running: ha_running,
        ev_obj,
        start_solution: best.genes.clone(),
        start_solution_datetime: slot0,
        washingstart,
        appliance_starts: app_starts,
        appliance_deadline_missed: app_missed,
        evaluations,
        direct_consumption_misses: direct_misses,
        eval_seconds: opt.eval_ns as f64 / 1e9,
        profile_seconds: opt.prof.iter().map(|v| *v as f64 / 1e9).collect(),
    })
}

/// `_ac_break_even_prices`.
fn ac_break_even_prices(prices: &[f64], load: &[f64], free_ac_wh: f64, n: usize) -> Vec<f64> {
    let mut best = vec![0.0f64; n];
    for hour in 0..n {
        let mut future: Vec<(f64, f64)> = (hour + 1..n).map(|h| (prices[h], load[h])).collect();
        // sort(key=-price): stable
        future.sort_by(|a, b| (-a.0).partial_cmp(&-b.0).unwrap_or(std::cmp::Ordering::Equal));
        let mut remaining = free_ac_wh;
        let mut best_uncovered = 0.0;
        for (fp, fl) in future {
            if remaining >= fl {
                remaining -= fl;
            } else {
                best_uncovered = fp;
                break;
            }
        }
        best[hour] = best_uncovered;
    }
    best
}

/// Python's `f"{x:g}"` for the message.
fn fmt_g(x: f64) -> String {
    if x == x.trunc() && x.abs() < 1e16 {
        format!("{}", x as i64)
    } else {
        let s = format!("{x:.6}");
        let s = s.trim_end_matches('0').trim_end_matches('.').to_string();
        s
    }
}

impl<'a> Optimizer<'a> {
    /// `_start_solution_for_slot_grid`.
    fn start_solution_for_slot_grid(&self, s: &[i64]) -> Vec<i64> {
        let n_app = self.rc.layout.len();
        let blocks = if self.optimize_ev { 2 } else { 1 };
        let expected = self.ce * blocks + n_app;
        let horizon_hours = self.cfg.genetic.horizon_hours.max(0) as usize;
        let hourly = horizon_hours * blocks + n_app;
        if s.len() == expected || self.slots_per_hour == 1 {
            return s.to_vec();
        }
        if s.len() != hourly {
            return s.to_vec();
        }
        let k = self.slots_per_hour as usize;
        let mut migrated: Vec<i64> = s[..horizon_hours].iter().flat_map(|&v| std::iter::repeat_n(v, k)).collect();
        if self.optimize_ev {
            migrated.extend(s[horizon_hours..2 * horizon_hours].iter().flat_map(|&v| std::iter::repeat_n(v, k)));
        }
        if n_app > 0 {
            migrated.extend_from_slice(&s[s.len() - n_app..]);
        }
        migrated
    }

    /// `_start_solution_for_run_start`.
    fn start_solution_for_run_start(&self, s: Option<&[f64]>, dt: Option<DateTime<Tz>>) -> Option<Vec<i64>> {
        let s: Vec<i64> = s?.iter().map(|v| v.trunc() as i64).collect();
        let Some(dt) = dt else {
            return Some(s);
        };
        let s = self.start_solution_for_slot_grid(&s);
        let blocks = if self.optimize_ev { 2 } else { 1 };
        if s.len() != self.ce * blocks + self.rc.layout.len() {
            return Some(s);
        }
        let elapsed = total_seconds(&self.slot0, &dt);
        if elapsed < 0.0 {
            return None;
        }
        let elapsed_slots = homeappliance::py_floordiv(elapsed, self.slot_h * 3600.0) as usize;
        if elapsed_slots == 0 {
            return Some(s);
        }
        if elapsed_slots >= self.control_slots {
            return None;
        }
        let mut aligned = s.clone();
        for block in 0..blocks {
            let begin = block * self.ce;
            let end = begin + self.control_slots;
            let genes: Vec<i64> = aligned[begin..end].to_vec();
            let last = genes[genes.len() - 1];
            let mut shifted: Vec<i64> = genes[elapsed_slots..].to_vec();
            shifted.extend(std::iter::repeat_n(last, elapsed_slots));
            aligned[begin..end].copy_from_slice(&shifted);
        }
        Some(aligned)
    }

    /// `_start_solution_matches_layout`.
    fn start_solution_matches_layout(&self, s: &[i64]) -> bool {
        let n = self.rc.layout.len();
        if n == 0 {
            return true;
        }
        if s.len() < n {
            return false;
        }
        let tail = &s[s.len() - n..];
        tail.iter()
            .zip(self.rc.layout.iter())
            .all(|(&v, g)| !g.allowed_start_slots.is_empty() && 0 <= v && (v as usize) < g.allowed_start_slots.len())
    }

    /// `optimize(start_solution, ngen, individuals)`.
    fn optimize(
        &mut self,
        start_solution: Option<Vec<i64>>,
        ngen: i64,
        individuals: i64,
    ) -> (Ind, (Vec<f64>, Vec<f64>, Vec<f64>), FitnessHistory) {
        if let Some(seed) = self.fix_seed {
            self.rng = PyRandom::seed(seed.unsigned_abs());
        }
        let mut valid: Option<Vec<i64>> = None;
        if let Some(s) = start_solution {
            let expected = self.ce * if self.optimize_ev { 2 } else { 1 } + self.rc.layout.len();
            let s = self.start_solution_for_slot_grid(&s);
            if s.len() == expected && self.start_solution_matches_layout(&s) {
                valid = Some(s);
            }
        }
        let ind_f = individuals as f64;
        let exact_target = WARM_START_COPIES.min(1.max((ind_f * WARM_START_COPY_FRACTION + 0.999999) as i64));
        let mutation_target = WARM_START_MUTATIONS.min(1.max((ind_f * WARM_START_MUTATION_FRACTION + 0.999999) as i64));
        let educated_target = EDUCATED_GUESS_TARGET.min(1.max((ind_f * EDUCATED_GUESS_FRACTION + 0.999999) as i64));
        let minimum_random = ((ind_f * MIN_RANDOM_POPULATION_FRACTION + 0.999999) as i64)
            .max(individuals - (exact_target + mutation_target + educated_target));
        let seed_budget = (individuals - minimum_random).max(0);
        let mut seeded: Vec<Vec<i64>> = Vec::new();
        if let Some(v) = &valid {
            if seed_budget > 0 {
                let exact = exact_target.min(seed_budget);
                for _ in 0..exact {
                    seeded.push(v.clone());
                }
                let remaining = seed_budget - seeded.len() as i64;
                let neighbors = self.mutated_warm_start_neighbors(v, mutation_target.min(remaining));
                seeded.extend(neighbors);
            }
        }
        let remaining = seed_budget - seeded.len() as i64;
        let educated = timed!(self, 5, self.educated_guess_individuals(educated_target.min(remaining)));
        seeded.extend(educated);
        let random_count = (individuals - seeded.len() as i64).max(0) as usize;
        let mut population: Vec<IRef> = seeded.into_iter().map(new_ind).collect();
        let rnd = self.population(random_count);
        population.extend(rnd);

        // Cache
        let max_entries = self.cfg.genetic.fitness_cache_max_entries.map(|m| m.max(0) as usize);
        self.cache = FitnessCache { enabled: max_entries != Some(0), max_entries, ..FitnessCache::default() };
        self.energy_shift_cache.clear();
        let mut hof = HallOfFame::default();
        let mut history = FitnessHistory::default();
        let population = self.evolve(population, individuals, individuals, ngen, &mut hof, &mut history);
        let hof0 = hof.item.clone().expect("hall of fame");
        let max_local = LOCAL_SEARCH_MAX_EVALUATIONS.min(individuals.max(1));
        let (best, le, li, lif, lff) = self.locally_improve(&hof0, max_local);
        let lookups = self.cache.hits + self.cache.misses;
        history.cache_hits = self.cache.hits;
        history.cache_misses = self.cache.misses;
        history.cache_hit_rate = if lookups > 0 { self.cache.hits as f64 / lookups as f64 } else { 0.0 };
        history.cache_keys = self.cache.map.len();
        history.local_evaluations = le;
        history.local_improvements = li;
        history.local_initial_fitness = lif;
        history.local_final_fitness = lff;
        let mut member = (Vec::new(), Vec::new(), Vec::new());
        for ind in &population {
            if let Some((a, b, c)) = ind.borrow().extra {
                member.0.push(a);
                member.1.push(b);
                member.2.push(c);
            }
        }
        self.cache = FitnessCache::default();
        (best, member, history)
    }

    /// `_terminal_value(parameters, include_tail_plan=True)` after the final evaluation.
    fn terminal_value_result(&self, sc: &mut SelfConsumption) -> Result<TerminalValueResult, String> {
        let g = &self.cfg.genetic;
        let fma = self.rc.numeric.interp_fma;
        let mut r = TerminalValueResult {
            control_horizon_hours: g.horizon_hours as f64,
            requested_tail_hours: g.tail_horizon_hours as f64,
            effective_tail_hours: 0.0,
            tail_end_hour: g.horizon_hours as f64,
            continuation_mode: "FIXED".into(),
            ..TerminalValueResult::default()
        };
        let Some(bat) = self.evaluators[0].bat.as_ref() else {
            r.mode = "FIXED".into();
            r.reason = "no battery in this optimization".into();
            return Ok(r);
        };
        let mut energy = bat.current_energy_content();
        if let Some(inv) = self.rc.inverter.as_ref() {
            energy *= inv.reference_dc_to_ac_efficiency;
        }
        if let Some(c) = self.terminal_curve.as_ref().filter(|c| !c.energy_wh.is_empty()) {
            let credit = c.value(energy, fma);
            let (op, cont, plan) = if c.is_tail {
                let (op, cont) = c.component_values(energy, fma);
                let plan = match (&self.tail_trace, &self.rc.battery, &self.rc.inverter) {
                    (Some(t), Some(b), Some(inv)) => {
                        curves::diagnostic_plan(t, b, inv, sc, energy, g.horizon_hours as f64, fma)
                            .map_err(|_| "tail plan failed".to_string())?
                    }
                    _ => Vec::new(),
                };
                (op, cont, plan)
            } else {
                (0.0, credit, Vec::new())
            };
            return Ok(TerminalValueResult {
                control_horizon_hours: g.horizon_hours as f64,
                requested_tail_hours: g.tail_horizon_hours as f64,
                effective_tail_hours: self.effective_tail_slots as f64 * self.slot_h,
                tail_end_hour: (self.ce + self.effective_tail_slots) as f64 * self.slot_h,
                continuation_mode: "AUTO".into(),
                mode: if c.is_tail { "TAIL".into() } else { "AUTO".into() },
                battery_energy_wh: energy,
                credited_euro: credit,
                tail_operating_euro: op,
                continuation_value_euro: cont,
                curve: Some(c.clone()),
                continuation_curve: self.continuation_curve.clone(),
                tail_diagnostics: self.tail_diagnostics.clone(),
                tail_plan: plan,
                reason: self.forecast_reason.clone(),
            });
        }
        let credit = energy * self.rc_price_per_wh_battery();
        let first = if self.terminal_value_reason.is_empty() {
            "terminal_value_mode is FIXED".to_string()
        } else {
            self.terminal_value_reason.clone()
        };
        let reason: Vec<String> = [first, self.forecast_reason.clone()].into_iter().filter(|s| !s.is_empty()).collect();
        r.mode = "FIXED".into();
        r.battery_energy_wh = energy;
        r.credited_euro = credit;
        r.continuation_value_euro = credit;
        r.reason = reason.join(" ");
        Ok(r)
    }

    fn rc_price_per_wh_battery(&self) -> f64 {
        match self.rc.terminal {
            Terminal::Fixed(p) => p,
            _ => 0.0,
        }
    }
}

/// Terminal value credit (for tests).
pub fn credit_for(rc: &RunConst, b: &Battery) -> f64 {
    terminal_credit(rc, b)
}
