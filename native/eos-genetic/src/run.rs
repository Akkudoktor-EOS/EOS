//! One complete GENETIC run as JSON in, JSON out – the interface of the Python binding.
//!
//! The caller (`akkudoktoreos.optimization.genetic.native`) has already prepared everything
//! Python prepares before `GeneticOptimization._optimize_ems_run()`: the request parameters, the
//! configuration snapshot of the run and the EMS start time. The kernel then does what
//! `_optimize_ems_run()` does and returns the `GeneticSolution` in the form of
//! `GeneticSolution.model_dump(mode="json")`, so Python validates it into the same model.

use chrono::{DateTime, FixedOffset};
use chrono_tz::Tz;
use serde::Deserialize;
use serde_json::{Value, json};

use crate::numpy::{DotKernel, NumericProfile};
use crate::params::GeneticOptimizationParameters;
use crate::settings::{GeneticSettings, RunConfig};

/// Version of the JSON request/response layout below. Bumped on every incompatible change.
pub const INTERFACE_VERSION: u32 = 1;

/// Previous solution of this EOS instance (`EnergyManagement.genetic_solution()`), used to
/// date a warm start that the request carries without `start_solution_datetime`.
#[derive(Debug, Clone, Deserialize)]
pub struct LastSolution {
    pub start_solution: Vec<f64>,
    pub start_solution_datetime: String,
}

/// Numerical profile requested by the caller.
#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ProfileRequest {
    #[serde(default = "default_dot")]
    pub dot: String,
    #[serde(default)]
    pub interp_fma: bool,
}

fn default_dot() -> String {
    "sequential".into()
}

/// The request. Field names follow the Python names they come from.
#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RunRequest {
    pub interface_version: u32,
    /// `GeneticOptimizationParameters.model_dump(mode="json")` as received by `optimize_ems()`.
    pub parameters: Value,
    /// `config.optimization.genetic.model_dump(mode="json")` (the frozen run snapshot).
    pub settings: Value,
    /// `config.prediction.hours` (frozen).
    pub prediction_hours: i64,
    /// `config.feedintariff.direct_marketing_enabled`.
    pub direct_marketing_enabled: bool,
    /// `config.general.timezone`.
    pub timezone: String,
    /// `ems.start_datetime` (ISO 8601 with offset) and the name of its timezone.
    pub start_datetime: String,
    #[serde(default)]
    pub start_datetime_timezone: Option<String>,
    /// `ems.observation_datetime`, if the EMS has one.
    #[serde(default)]
    pub observation_datetime: Option<String>,
    #[serde(default)]
    pub fallback_battery_charge_rates: Option<Vec<f64>>,
    #[serde(default)]
    pub fallback_battery_grid_export_rates: Option<Vec<f64>>,
    #[serde(default)]
    pub fallback_ev_charge_rates: Option<Vec<f64>>,
    #[serde(default)]
    pub last_solution: Option<LastSolution>,
    /// `GeneticOptimization.fix_seed`.
    #[serde(default)]
    pub seed: Option<i64>,
    #[serde(default)]
    pub generations: Option<i64>,
    #[serde(default)]
    pub individuals: Option<i64>,
    #[serde(default)]
    pub worst_case: bool,
    /// Fitness threads; 0 = one core left free, at most two (like `auto_evaluation_workers`).
    #[serde(default)]
    pub threads: usize,
    #[serde(default)]
    pub numeric_profile: Option<ProfileRequest>,
}

/// A failed run. `code` is stable and machine readable, `message` is for the log.
#[derive(Debug, Clone)]
pub struct RunError {
    pub code: &'static str,
    pub message: String,
}

impl std::fmt::Display for RunError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}: {}", self.code, self.message)
    }
}

impl std::error::Error for RunError {}

fn err(code: &'static str, message: impl Into<String>) -> RunError {
    RunError { code, message: message.into() }
}

/// Marker in the message of a request the kernel rejects because it does not support it
/// (error code `unsupported`). Every other `run_failed` is a defect of the kernel.
pub const NOT_SUPPORTED: &str = "not supported natively";

fn run_error(message: String) -> RunError {
    if message.contains(NOT_SUPPORTED) { err("unsupported", message) } else { err("run_failed", message) }
}

/// Fitness threads like EOS `auto_evaluation_workers`: one core stays free, at most two.
pub fn auto_threads() -> usize {
    let cores = std::thread::available_parallelism().map(|n| n.get()).unwrap_or(1);
    cores.saturating_sub(1).clamp(1, 2)
}

fn parse_tz(name: &str) -> Option<Tz> {
    name.trim().parse::<Tz>().ok()
}

/// A timestamp of the request in `tz`; without a usable zone name the rule of the parity tools
/// applies (offset 0 → UTC, otherwise the run zone).
fn parse_datetime(text: &str, zone: Option<&str>, run_tz: Tz) -> Result<DateTime<Tz>, RunError> {
    let fixed: DateTime<FixedOffset> = DateTime::parse_from_rfc3339(text.trim())
        .map_err(|e| err("invalid_request", format!("datetime {text:?}: {e}")))?;
    if let Some(tz) = zone.and_then(parse_tz) {
        return Ok(fixed.with_timezone(&tz));
    }
    if fixed.offset().local_minus_utc() == 0 {
        Ok(fixed.with_timezone(&chrono_tz::UTC))
    } else {
        Ok(fixed.with_timezone(&run_tz))
    }
}

/// Numerical profile of a request (default: the fixed release profile).
pub fn numeric_profile(req: Option<&ProfileRequest>) -> Result<NumericProfile, RunError> {
    let Some(p) = req else {
        return Ok(NumericProfile::RELEASE);
    };
    let dot =
        DotKernel::parse(&p.dot).ok_or_else(|| err("invalid_request", format!("unknown dot kernel {:?}", p.dot)))?;
    Ok(NumericProfile { dot, interp_fma: p.interp_fma })
}

/// Turn a request into the parameters and the run configuration of the kernel.
pub fn prepare(req: &RunRequest) -> Result<(GeneticOptimizationParameters, RunConfig, NumericProfile), RunError> {
    if req.interface_version != INTERFACE_VERSION {
        return Err(err(
            "interface_version",
            format!("request interface {} is not supported (kernel speaks {INTERFACE_VERSION})", req.interface_version),
        ));
    }
    let params = GeneticOptimizationParameters::from_json_value(req.parameters.clone())
        .map_err(|e| err("unsupported_parameters", e.to_string()))?;
    let mut genetic: GeneticSettings =
        serde_json::from_value(req.settings.clone()).map_err(|e| err("unsupported_settings", e.to_string()))?;
    genetic.seed = req.seed;
    let timezone = parse_tz(&req.timezone)
        .ok_or_else(|| err("invalid_request", format!("unknown timezone {:?}", req.timezone)))?;
    let start = parse_datetime(&req.start_datetime, req.start_datetime_timezone.as_deref(), timezone)?;
    let observation = match &req.observation_datetime {
        Some(t) => parse_datetime(t, req.start_datetime_timezone.as_deref(), timezone)?,
        None => start,
    };
    let last_solution = match &req.last_solution {
        Some(l) => Some((l.start_solution.clone(), parse_datetime(&l.start_solution_datetime, None, timezone)?)),
        None => None,
    };
    let numeric = numeric_profile(req.numeric_profile.as_ref())?;
    let cfg = RunConfig {
        genetic,
        prediction_hours: req.prediction_hours,
        direct_marketing_enabled: req.direct_marketing_enabled,
        timezone,
        start_datetime: start,
        observation_datetime: observation,
        fallback_battery_charge_rates: req.fallback_battery_charge_rates.clone(),
        fallback_battery_grid_export_rates: req.fallback_battery_grid_export_rates.clone(),
        fallback_ev_charge_rates: req.fallback_ev_charge_rates.clone(),
        last_solution,
        dot_kernel: numeric.dot,
        threads: if req.threads == 0 { auto_threads() } else { req.threads },
        worst_case: req.worst_case,
    };
    Ok((params, cfg, numeric))
}

/// Run a request. Returns `{"solution": <GeneticSolution>, "diagnostics": {...}}`.
pub fn run(req: &RunRequest) -> Result<Value, RunError> {
    let (params, cfg, numeric) = prepare(req)?;
    let t0 = std::time::Instant::now();
    let core =
        crate::genetic::optimize_ems(&params, &cfg, req.generations, req.individuals, numeric).map_err(run_error)?;
    let elapsed = t0.elapsed().as_secs_f64();
    let solution = crate::convert::genetic_solution(&core, cfg.timezone);
    let solution = serde_json::to_value(&solution).map_err(|e| err("serialization", e.to_string()))?;
    let h = &core.fitness_history;
    Ok(json!({
        "solution": solution,
        "diagnostics": {
            "engine": "eos-genetic",
            "version": env!("CARGO_PKG_VERSION"),
            "interface_version": INTERFACE_VERSION,
            "seed": cfg.genetic.seed,
            "threads": cfg.threads,
            "numeric_profile": {"dot": numeric.dot.name(), "interp_fma": numeric.interp_fma},
            "evaluations": core.evaluations,
            "eval_seconds": core.eval_seconds,
            "elapsed_seconds": elapsed,
            "best_fitness": h.local_final_fitness,
        },
    }))
}

/// [`run`] on JSON text.
pub fn run_json(text: &str) -> Result<String, RunError> {
    let req: RunRequest = serde_json::from_str(text).map_err(|e| err("invalid_request", e.to_string()))?;
    let out = run(&req)?;
    serde_json::to_string(&out).map_err(|e| err("serialization", e.to_string()))
}
