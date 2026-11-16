"""Explicit component diagnostics without changing planner inputs or controls."""

from typing import Any

from akkudoktoreos.devices.heatpump import calculate_dew_point


def planning_components(
    *,
    mode: str,
    model: dict[str, Any],
    weather: list[dict[str, Any]],
    maximum_w: float,
    minimum_w: float | None,
    distribution: str,
) -> dict[str, Any]:
    """Expose weather provenance and distinguish effective heat gain from COP."""
    points = []
    for row in weather:
        temperature = row.get("weather_temp_air")
        humidity = row.get("weather_relative_humidity")
        dew = row.get("weather_dew_point")
        origin = "weather_provider" if dew is not None else "unavailable"
        if dew is None and temperature is not None and humidity is not None and 0 < humidity <= 100:
            dew = calculate_dew_point(temperature, humidity)
            origin = "magnus_from_forecast_temperature_and_humidity"
        points.append(
            {
                "date_time": row.get("date_time"),
                "outdoor_temperature_c": temperature,
                "relative_humidity_percent": humidity,
                "dew_point_c": dew,
                "dew_point_source": origin,
            }
        )
    effective = mode == "effective_electric"
    return {
        "mode": mode,
        "model_parameters": model,
        "source_electric_minimum_w": minimum_w,
        "source_electric_maximum_w": maximum_w,
        "distribution": distribution,
        "weather": points,
        "defrost": {
            "separate_source_model_available": not effective,
            "status": "not_separately_modelled" if effective else "see_source_performance",
            "reason": "Effective electric-to-room gain; no separate COP or defrost multiplier"
            if effective
            else "Use source_horizon performance flags for actual per-slot correction",
            "event_prediction_available": False,
            "evaporator_temperature_measured": False,
        },
        "cop_separately_identified": False if effective else None,
        "power_plan_is_measured": False,
        "temperature_plan_is_measured": False,
    }
