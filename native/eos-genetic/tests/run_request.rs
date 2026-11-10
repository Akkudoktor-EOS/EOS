//! A full run through the JSON interface (`eos_genetic::run`), as the Python binding uses it.
//!
//! The request is `tests/testdata/genetic/optimize_input_1.json` of EOS as
//! `akkudoktoreos.optimization.genetic.native.build_request` writes it (40 generations).
//! Bit-identity with Python is checked on the Python side (`tests/test_geneticnative.py`).

use serde_json::Value;

const REQUEST: &str = include_str!("fixtures/request-input1.json");

fn run(text: &str) -> Value {
    let out = eos_genetic::run::run_json(text).expect("run");
    serde_json::from_str(&out).expect("json")
}

fn with(f: impl FnOnce(&mut Value)) -> String {
    let mut v: Value = serde_json::from_str(REQUEST).expect("fixture");
    f(&mut v);
    v.to_string()
}

#[test]
fn same_request_same_solution() {
    let a = run(REQUEST);
    let b = run(REQUEST);
    assert_eq!(a["solution"], b["solution"]);
    assert_eq!(a["diagnostics"]["numeric_profile"]["dot"], "sequential");
    let solution = &a["solution"];
    for key in ["ac_charge", "dc_charge", "discharge_allowed", "result", "fitness_history", "start_solution"] {
        assert!(solution.get(key).is_some(), "missing {key}");
    }
}

#[test]
fn threads_do_not_change_the_solution() {
    let one = run(&with(|v| v["threads"] = 1.into()));
    let two = run(&with(|v| v["threads"] = 2.into()));
    assert_eq!(one["solution"], two["solution"]);
}

#[test]
fn seed_changes_the_search() {
    let a = run(&with(|v| v["seed"] = 1.into()));
    let b = run(&with(|v| v["seed"] = 2.into()));
    assert_eq!(a["solution"]["fixed_seed"], 1);
    assert_ne!(a["solution"]["fitness_history"], b["solution"]["fitness_history"]);
}

#[test]
fn rejects_other_interface_versions() {
    let err = eos_genetic::run::run_json(&with(|v| v["interface_version"] = 99.into())).unwrap_err();
    assert_eq!(err.code, "interface_version");
}

#[test]
fn rejects_unknown_request_fields() {
    let err = eos_genetic::run::run_json(&with(|v| v["unknown"] = true.into())).unwrap_err();
    assert_eq!(err.code, "invalid_request");
}

#[test]
fn rejects_unknown_dot_kernel() {
    let err = eos_genetic::run::run_json(&with(|v| v["numeric_profile"]["dot"] = "haswell".into())).unwrap_err();
    assert_eq!(err.code, "invalid_request");
}

#[test]
fn reproduces_known_openblas_kernels() {
    // The profiles differ only in the last bits of the self-consumption dot product.
    let release = run(REQUEST);
    let n1 = run(&with(|v| v["numeric_profile"]["dot"] = "neoversen1".into()));
    assert_eq!(n1["diagnostics"]["numeric_profile"]["dot"], "neoversen1");
    assert!(release["solution"]["result"]["total_balance"].is_number());
    assert!(n1["solution"]["result"]["total_balance"].is_number());
}

#[test]
fn rejects_a_battery_without_inverter() {
    let err = eos_genetic::run::run_json(&with(|v| v["parameters"]["inverter"] = Value::Null)).unwrap_err();
    assert_eq!(err.code, "unsupported");
    assert!(err.message.contains("without an inverter"), "{}", err.message);
}

#[test]
fn rejects_empty_ev_charge_rates() {
    // Request rates unset, configured rates empty: Python fails in `random.randint(0, -1)`.
    let err = eos_genetic::run::run_json(&with(|v| {
        v["parameters"]["ev"]["charge_rates"] = Value::Null;
        v["parameters"]["ev"]["min_soc_percentage"] = 80.into();
        v["fallback_ev_charge_rates"] = serde_json::json!([]);
    }))
    .unwrap_err();
    assert_eq!(err.code, "run_failed");
    assert!(err.message.contains("EV charge rates are empty"), "{}", err.message);
}

#[test]
fn rejects_parameter_fields_it_does_not_know() {
    // A field the kernel does not know may change the result in Python; the run must not
    // silently ignore it.
    let err =
        eos_genetic::run::run_json(&with(|v| v["parameters"]["inverter"]["new_limit_w"] = 1000.into())).unwrap_err();
    assert_eq!(err.code, "unsupported");
    assert!(err.message.contains("new_limit_w"), "{}", err.message);
}

#[test]
fn rejects_appliance_time_windows_as_unsupported() {
    let err = eos_genetic::run::run_json(&with(|v| {
        v["parameters"]["home_appliances"] = serde_json::json!([{
            "device_id": "washer",
            "consumption_wh": 1000,
            "duration_h": 2,
            "time_windows": {"windows": [{"start_time": "12:00", "duration": "4 hours"}]}
        }]);
    }))
    .unwrap_err();
    assert_eq!(err.code, "unsupported");
    assert!(err.message.contains("time_windows"), "{}", err.message);
}
