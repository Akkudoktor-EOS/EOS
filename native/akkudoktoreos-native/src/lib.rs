//! Python binding of the native EOS kernels (`akkudoktoreos_native._core`).
//!
//! The binding is deliberately thin: one run goes in as JSON and comes out as JSON
//! (`eos_genetic::run`). The GIL is released while the optimizer runs, so the EOS server keeps
//! serving requests. Panics never cross into Python as `PanicException` (a `BaseException`);
//! they become `NativeRunError` like every other failure, so EOS can fall back to Python.

use std::panic::{AssertUnwindSafe, catch_unwind};

use pyo3::create_exception;
use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;

create_exception!(_core, NativeRunError, PyRuntimeError, "A native GENETIC run failed (args: code, message).");

fn panic_message(payload: &(dyn std::any::Any + Send)) -> String {
    if let Some(s) = payload.downcast_ref::<&str>() {
        (*s).to_string()
    } else if let Some(s) = payload.downcast_ref::<String>() {
        s.clone()
    } else {
        "unknown panic".to_string()
    }
}

/// Run one GENETIC optimization. `request` and the result are JSON text (see `eos_genetic::run`).
#[pyfunction]
fn run_genetic(py: Python<'_>, request: &str) -> PyResult<String> {
    let request = request.to_owned();
    let outcome = py.detach(move || catch_unwind(AssertUnwindSafe(|| eos_genetic::run::run_json(&request))));
    match outcome {
        Ok(Ok(text)) => Ok(text),
        Ok(Err(e)) => Err(NativeRunError::new_err((e.code, e.message))),
        Err(payload) => Err(NativeRunError::new_err(("panic", panic_message(payload.as_ref())))),
    }
}

/// Dot product with one of the kernels the optimizer can reproduce (for profile detection).
#[pyfunction]
fn dot(kernel: &str, a: Vec<f64>, b: Vec<f64>) -> PyResult<f64> {
    if a.len() != b.len() {
        return Err(PyValueError::new_err("vectors differ in length"));
    }
    let k = eos_genetic::numpy::DotKernel::parse(kernel)
        .ok_or_else(|| PyValueError::new_err(format!("unknown dot kernel {kernel:?}")))?;
    Ok(k.dot(&a, &b))
}

/// `np.interp(x, xp, fp)` with or without a fused multiply-add (for profile detection).
#[pyfunction]
fn interp(x: f64, xp: Vec<f64>, fp: Vec<f64>, fma: bool) -> PyResult<f64> {
    if xp.is_empty() || xp.len() != fp.len() {
        return Err(PyValueError::new_err("xp and fp must be non-empty and of equal length"));
    }
    Ok(eos_genetic::numpy::interp(x, &xp, &fp, fma))
}

/// `SelfConsumptionProbabilityInterpolator._expected_direct_consumption` on the bundled table
/// with the given dot kernel (for profile detection).
#[pyfunction]
fn expected_direct_consumption(kernel: &str, mean_load_power_w: f64, pv_power_w: f64) -> PyResult<f64> {
    let k = eos_genetic::numpy::DotKernel::parse(kernel)
        .ok_or_else(|| PyValueError::new_err(format!("unknown dot kernel {kernel:?}")))?;
    Ok(eos_genetic::selfconsumption::SelfConsumption::new(k).expected_direct_consumption(mean_load_power_w, pv_power_w))
}

#[pymodule]
fn _core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("KERNEL_VERSION", env!("CARGO_PKG_VERSION"))?;
    m.add("INTERFACE_VERSION", eos_genetic::run::INTERFACE_VERSION)?;
    m.add("SELF_CONSUMPTION_TABLE_SHA256", eos_genetic::selfconsumption::SOURCE_SHA256)?;
    m.add("DOT_KERNELS", vec!["sequential", "cortexa53", "neoversen1"])?;
    m.add("NativeRunError", m.py().get_type::<NativeRunError>())?;
    m.add_function(wrap_pyfunction!(run_genetic, m)?)?;
    m.add_function(wrap_pyfunction!(dot, m)?)?;
    m.add_function(wrap_pyfunction!(interp, m)?)?;
    m.add_function(wrap_pyfunction!(expected_direct_consumption, m)?)?;
    Ok(())
}
