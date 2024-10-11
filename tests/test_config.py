import pytest

from akkudoktoreos.config import (
    output_dir,
    available_charging_rates_in_percentage,
    optimization_hours,
    parse_charging_rates,
    penalty,
    prediction_hours,
)


def test_default_values():
    assert output_dir == "output", "Default output_dir should be 'output'"
    assert prediction_hours == 48, "Default prediction_hours should be 48"
    assert optimization_hours == 24, "Default optimization_hours should be 24"
    assert penalty == 10, "Default penalty should be 10"
    assert available_charging_rates_in_percentage == [
        0.0,
        6.0 / 16.0,
        7.0 / 16.0,
        8.0 / 16.0,
        9.0 / 16.0,
        10.0 / 16.0,
        11.0 / 16.0,
        12.0 / 16.0,
        13.0 / 16.0,
        14.0 / 16.0,
        15.0 / 16.0,
        1.0,
    ], "Default available_charging_rates_in_percentage should match"


def test_invalid_charging_rates_format(monkeypatch):
    invalid_value = "invalid_format"
    monkeypatch.setenv("EOS_AVAILABLE_CHARGING_RATES_PERC", invalid_value)

    with pytest.raises(ValueError) as excinfo:
        parse_charging_rates(invalid_value)

    assert (
        str(excinfo.value)
        == "Invalid format for EOS_AVAILABLE_CHARGING_RATES_PERC. Expected a comma-separated list of floats."
    )


def test_env_values(monkeypatch):
    monkeypatch.setenv("EOS_OUTPUT_DIR", "test_output")
    monkeypatch.setenv("EOS_PREDICTION_HOURS", "72")
    monkeypatch.setenv("EOS_OPTIMIZATION_HOURS", "36")
    monkeypatch.setenv("EOS_PENALTY", "20")
    monkeypatch.setenv("EOS_AVAILABLE_CHARGING_RATES_PERC", "0.0,0.5,1.0")

    # Re-import the config module to apply the environment variables
    import importlib

    import akkudoktoreos.config as config

    importlib.reload(config)
    assert(config.output_dir == "test_output"), "output_dir should be 'test_output' from environment variable"
    assert (
        config.prediction_hours == 72
    ), "prediction_hours should be 72 from environment variable"
    assert (
        config.optimization_hours == 36
    ), "optimization_hours should be 36 from environment variable"
    assert config.penalty == 20, "penalty should be 20 from environment variable"
    assert config.available_charging_rates_in_percentage == [
        0.0,
        0.5,
        1.0,
    ], "available_charging_rates_in_percentage should match environment variable"


if __name__ == "__main__":
    pytest.main()
