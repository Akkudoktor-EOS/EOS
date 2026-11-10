//! # eos-genetic – native GENETIC optimizer of Akkudoktor-EOS
//!
//! A port of `akkudoktoreos.optimization.genetic` to Rust. With a fixed seed and the same numerical
//! profile, a run is bit-identical to the Python original (see `README.md`, "Numerical profile").
//!
//! Layout (Python original in brackets):
//! * [`pyrandom`] – CPython `random` (MT19937), [`numpy`] – the NumPy arithmetic GENETIC uses,
//! * [`params`] (`geneticparams.py`, device parameters), [`settings`] (`geneticsettings.py`),
//! * [`devices`] (`devices/genetic/*`), [`selfconsumption`] (`prediction/interpolator.py`),
//! * [`simulation`] (`GeneticSimulation`), [`genetic`] (`GeneticOptimization`), [`curves`]
//!   (`terminalvalue.py`, `tailvalue.py`),
//! * [`solution`], [`convert`] (`geneticsolution.py`: the `GeneticSolution` model),
//! * [`run`] – one run as JSON in, JSON out (used by the Python binding `akkudoktoreos-native`).

// The code mirrors the Python original statement by statement, which keeps it reviewable
// against Python. Some lints would ask for rewrites that change exactly that, or the NaN
// behaviour (`!(a <= b)` is not `a > b` with NaN; `f64::clamp` treats NaN differently).
#![allow(
    clippy::neg_cmp_op_on_partial_ord,
    clippy::manual_clamp,
    clippy::needless_range_loop,
    clippy::type_complexity,
    clippy::too_many_arguments,
    clippy::result_unit_err,
    clippy::ptr_arg,
    clippy::let_and_return,
    clippy::map_entry,
    clippy::unnecessary_unwrap,
    clippy::needless_option_as_deref,
    clippy::items_after_test_module
)]

pub mod convert;
pub mod curves;
pub mod datetime;
pub mod devices;
pub mod genetic;
pub mod numpy;
pub mod params;
pub mod pyrandom;
pub mod run;
pub mod selfconsumption;
pub mod settings;
pub mod simulation;
pub mod solution;
