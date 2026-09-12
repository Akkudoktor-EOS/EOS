#!.venv/bin/python
"""Backtest the local PV forecast model against measured PV production.

Answers the question "is my PV forecast actually any good, and does a different
configuration help?" without waiting for new forecasts to come true. Open-Meteo serves
past weather in the same request as the forecast, so every variant can be scored right
now against the meter readings EOS already holds.

The comparison runs on past intervals, where the Open-Meteo rows are analysed rather
than forecast weather. That isolates the error of the *PV model* (wrong peakpower,
soiling, shading the horizon profile misses) from the error of the *weather forecast*.
Only the former is systematic enough to fix by configuration.

Requires:
    - ``general.latitude`` / ``general.longitude`` and ``pvforecast.planes`` configured
    - ``measurement.pv_production_emr_keys`` configured and fed with cumulative PV
      production meter readings [kWh]

Usage:
    python scripts/pvforecast_backtest.py --days 30
    python scripts/pvforecast_backtest.py --days 30 --tilt 88 --azimuth 175
"""

import argparse
import sys
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd

# Add the src directory to sys.path so import akkudoktoreos works in all cases
PROJECT_ROOT = Path(__file__).parent.parent
SRC_DIR = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))

from akkudoktoreos.core.coreabc import get_config, get_measurement, singletons_init
from akkudoktoreos.prediction.pvforecastakkudoktorlocal import (
    PVForecastAkkudoktorLocal,
    PVForecastAkkudoktorLocalCommonSettings,
)
from akkudoktoreos.utils.datetimeutil import to_datetime, to_duration

ENSEMBLE = ["icon_seamless", "ecmwf_ifs025", "gfs_seamless"]

# Variants scored against the meter. Each entry is a label plus the settings overrides
# applied on top of the configured provider settings.
VARIANTS: list[tuple[str, dict[str, Any]]] = [
    (
        "best_match",
        {"weather_models": ["best_match"], "calibration_enabled": False},
    ),
    ("ensemble", {"weather_models": ENSEMBLE, "calibration_enabled": False}),
    ("ensemble + calibration", {"weather_models": ENSEMBLE, "calibration_enabled": True}),
    (
        "ensemble + calibration (global only)",
        {
            "weather_models": ENSEMBLE,
            "calibration_enabled": True,
            "calibration_azimuth_bin_degrees": 0,
        },
    ),
    (
        "ensemble, isotropic sky",
        {
            "weather_models": ENSEMBLE,
            "transposition_model": "isotropic",
            "calibration_enabled": False,
        },
    ),
    (
        "ensemble, no IAM",
        {"weather_models": ENSEMBLE, "apply_iam": False, "calibration_enabled": False},
    ),
]


def score(modelled_kwh: np.ndarray, measured_kwh: np.ndarray) -> dict[str, float]:
    """Mean absolute error, bias and correlation over the daylight hours."""
    error = modelled_kwh - measured_kwh
    measured_total = measured_kwh.sum()
    correlation = 0.0
    if modelled_kwh.std() > 0 and measured_kwh.std() > 0:
        correlation = float(np.corrcoef(modelled_kwh, measured_kwh)[0, 1])
    return {
        "mae": float(np.abs(error).mean()),
        "rmse": float(np.sqrt((error**2).mean())),
        "bias": float(error.mean()),
        "bias_pct": float(100.0 * error.sum() / measured_total) if measured_total > 0 else 0.0,
        "r": correlation,
        "model_kwh": float(modelled_kwh.sum()),
        "measured_kwh": float(measured_total),
    }


def hourly_model(provider: PVForecastAkkudoktorLocal, data: Any, index: pd.DatetimeIndex) -> np.ndarray:
    """Run the chain and resample the AC power onto the measurement's hourly grid."""
    frame = provider._forecast_frame(data)
    if frame.empty:
        return np.full(len(index), np.nan)
    # `ac_power` is a mean power per interval, so an hourly mean in W is Wh per hour.
    hourly = frame["ac_power"].resample("1h").mean().reindex(index)
    return hourly.to_numpy(dtype=float) / 1000.0


def main(days: int, tilt: Optional[float], azimuth: Optional[float]) -> int:
    singletons_init()
    config = get_config()
    measurement = get_measurement()
    if measurement.max_datetime is None:
        measurement.load()

    if not config.measurement.pv_production_emr_keys:
        print(
            "measurement.pv_production_emr_keys is not configured - nothing to compare "
            "against. Configure it and feed cumulative PV production readings [kWh]."
        )
        return 1
    if not config.pvforecast.planes:
        print("pvforecast.planes is not configured.")
        return 1
    if measurement.max_datetime is None:
        print("No measurements stored yet.")
        return 1

    if tilt is not None:
        config.pvforecast.planes[0].surface_tilt = tilt
    if azimuth is not None:
        config.pvforecast.planes[0].surface_azimuth = azimuth

    end = measurement.max_datetime.start_of("hour")
    start = end.subtract(days=days)
    if start < measurement.min_datetime:
        start = measurement.min_datetime.start_of("hour").add(hours=1)

    measured_kwh = np.asarray(
        measurement.pv_production_total_kwh(
            start_datetime=start, end_datetime=end, interval=to_duration("1 hour")
        ),
        dtype=float,
    )
    grid = pd.date_range(
        start=pd.Timestamp(start.in_timezone("UTC").isoformat()),
        periods=len(measured_kwh),
        freq="1h",
    )
    print(f"Window: {start} .. {end}  ({len(measured_kwh)} h)")
    print(f"Measured PV production: {measured_kwh.sum():.1f} kWh")

    provider = PVForecastAkkudoktorLocal(config=config, start_datetime=to_datetime())
    baseline_settings = config.pvforecast.provider_settings.PVForecastAkkudoktorLocal
    if baseline_settings is None:
        baseline_settings = PVForecastAkkudoktorLocalCommonSettings()
    common = {"past_days": min(days + 1, 92), "calibration_days": days}

    rows = []
    for label, overrides in VARIANTS:
        settings = baseline_settings.model_copy(update={**common, **overrides})
        config.pvforecast.provider_settings.PVForecastAkkudoktorLocal = settings
        try:
            data = provider._request_forecast(force_update=True)
            modelled_kwh = hourly_model(provider, data, grid)
        except Exception as exc:  # noqa: BLE001 - one bad variant must not stop the rest
            print(f"  {label}: failed ({exc})")
            continue

        # Only score hours where both sides exist and something was actually produced.
        usable = np.isfinite(modelled_kwh) & np.isfinite(measured_kwh)
        usable &= (modelled_kwh > 0.05) | (measured_kwh > 0.05)
        if usable.sum() < 12:
            print(f"  {label}: too few usable hours ({int(usable.sum())})")
            continue
        rows.append((label, score(modelled_kwh[usable], measured_kwh[usable]), int(usable.sum())))

    if not rows:
        print("No variant could be scored.")
        return 1

    print(f"\n{'variant':38s} {'MAE':>7s} {'RMSE':>7s} {'bias':>8s} {'bias%':>7s} {'r':>6s}")
    print("-" * 78)
    for label, result, _ in sorted(rows, key=lambda row: row[1]["mae"]):
        print(
            f"{label:38s} {result['mae']:7.3f} {result['rmse']:7.3f} "
            f"{result['bias']:+8.3f} {result['bias_pct']:+6.1f}% {result['r']:6.3f}"
        )
    print("\nMAE/RMSE/bias in kWh per hour. Lower MAE is better; bias% is the total")
    print("over- (+) or under-estimate (-) relative to the measured energy.")
    print(
        "\nNote: the calibrated variants are fitted on the same window they are scored\n"
        "on, so their advantage here is optimistic. Re-run with a longer --days to see\n"
        "how much of it survives."
    )
    print(
        "Outage or curtailment periods are excluded from calibration but remain in these\n"
        "scores, because the script cannot prove the plant's availability without an\n"
        "explicit availability measurement."
    )

    best_label, best, hours = min(rows, key=lambda row: row[1]["mae"])
    print(
        f"\nBest: {best_label} - {best['model_kwh']:.1f} kWh modelled vs. "
        f"{best['measured_kwh']:.1f} kWh measured over {hours} scored hours."
    )
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backtest the local PV forecast model.")
    parser.add_argument("--days", type=int, default=30, help="Length of the window (default 30).")
    parser.add_argument("--tilt", type=float, default=None, help="Override plane 0 surface_tilt.")
    parser.add_argument(
        "--azimuth", type=float, default=None, help="Override plane 0 surface_azimuth."
    )
    args = parser.parse_args()
    sys.exit(main(args.days, args.tilt, args.azimuth))
