#!/usr/bin/env python3

import os
from datetime import datetime
from typing import Any, TypeGuard

import matplotlib

# Sets the Matplotlib backend to 'Agg' for rendering plots in environments without a display
matplotlib.use("Agg")

import pandas as pd
from flask import Flask, jsonify, redirect, request, send_from_directory, url_for

from akkudoktoreos.class_load import LoadForecast
from akkudoktoreos.class_load_container import LoadAggregator
from akkudoktoreos.class_load_corrector import LoadPredictionAdjuster
from akkudoktoreos.class_optimize import optimization_problem
from akkudoktoreos.class_pv_forecast import PVForecast
from akkudoktoreos.class_strompreis import HourlyElectricityPriceForecast
from akkudoktoreos.config import (
    get_start_enddate,
    optimization_hours,
    output_dir,
    prediction_hours,
)

app = Flask(__name__)

opt_class = optimization_problem(
    prediction_hours=prediction_hours, strafe=10, optimization_hours=optimization_hours
)


def isfloat(num: Any) -> TypeGuard[float]:
    """Check if a given input can be converted to float."""
    if num is None:
        return False

    if isinstance(num, str):
        num = num.strip()  # Strip any surrounding whitespace

    try:
        float_value = float(num)
        return not (
            float_value == float("inf")
            or float_value == float("-inf")
            or float_value != float_value
        )  # Excludes NaN or Infinity
    except (ValueError, TypeError):
        return False


@app.route("/strompreis", methods=["GET"])
def flask_strompreis():
    # Get the current date and the end date based on prediction hours
    date_now, date = get_start_enddate(prediction_hours, startdate=datetime.now().date())
    filepath = os.path.join(
        r"test_data", r"strompreise_akkudokAPI.json"
    )  # Adjust the path to the JSON file
    price_forecast = HourlyElectricityPriceForecast(
        source=f"https://api.akkudoktor.net/prices?start={date_now}&end={date}",
        prediction_hours=prediction_hours,
    )
    specific_date_prices = price_forecast.get_price_for_daterange(
        date_now, date
    )  # Fetch prices for the specified date range
    return jsonify(specific_date_prices.tolist())


# Endpoint to handle total load calculation based on the latest measured data
@app.route("/gesamtlast", methods=["POST"])
def flask_gesamtlast():
    # Retrieve data from the JSON body
    data = request.get_json()

    # Extract year_energy and prediction_hours from the request JSON
    year_energy = float(data.get("year_energy"))
    prediction_hours = int(data.get("hours", 48))  # Default to 48 hours if not specified

    # Measured data in JSON format
    measured_data_json = data.get("measured_data")
    measured_data = pd.DataFrame(measured_data_json)
    measured_data["time"] = pd.to_datetime(measured_data["time"])

    # Ensure datetime has timezone info for accurate calculations
    if measured_data["time"].dt.tz is None:
        measured_data["time"] = measured_data["time"].dt.tz_localize("Europe/Berlin")
    else:
        measured_data["time"] = measured_data["time"].dt.tz_convert("Europe/Berlin")

    # Remove timezone info after conversion to simplify further processing
    measured_data["time"] = measured_data["time"].dt.tz_localize(None)

    # Instantiate LoadForecast and generate forecast data
    file_path = os.path.join("data", "load_profiles.npz")
    lf = LoadForecast(filepath=file_path, year_energy=year_energy)
    forecast_list = []

    # Generate daily forecasts for the date range based on measured data
    for single_date in pd.date_range(
        measured_data["time"].min().date(), measured_data["time"].max().date()
    ):
        date_str = single_date.strftime("%Y-%m-%d")
        daily_forecast = lf.get_daily_stats(date_str)
        mean_values = daily_forecast[0]
        hours = [single_date + pd.Timedelta(hours=i) for i in range(24)]
        daily_forecast_df = pd.DataFrame({"time": hours, "Last Pred": mean_values})
        forecast_list.append(daily_forecast_df)

    # Concatenate all daily forecasts into a single DataFrame
    predicted_data = pd.concat(forecast_list, ignore_index=True)

    # Create LoadPredictionAdjuster instance to adjust the predictions based on measured data
    adjuster = LoadPredictionAdjuster(measured_data, predicted_data, lf)
    adjuster.calculate_weighted_mean()  # Calculate weighted mean for adjustment
    adjuster.adjust_predictions()  # Adjust predictions based on measured data
    future_predictions = adjuster.predict_next_hours(prediction_hours)  # Predict future load

    # Extract household power predictions
    leistung_haushalt = future_predictions["Adjusted Pred"].values
    gesamtlast = LoadAggregator(prediction_hours=prediction_hours)
    gesamtlast.add_load(
        "Haushalt", leistung_haushalt
    )  # Add household load to total load calculation

    # Calculate the total load
    return jsonify(gesamtlast.calculate_total_load())


@app.route("/gesamtlast_simple", methods=["GET"])
def flask_gesamtlast_simple():
    if request.method == "GET":
        year_energy = float(
            request.args.get("year_energy")
        )  # Get annual energy value from query parameters
        date_now, date = get_start_enddate(
            prediction_hours, startdate=datetime.now().date()
        )  # Get the current date and prediction end date

        ###############
        # Load Forecast
        ###############
        server_dir = os.path.dirname(os.path.realpath(__file__))
        file_path = os.path.join(server_dir, "data", "load_profiles.npz")

        print(file_path)

        lf = LoadForecast(
            filepath=file_path, year_energy=year_energy
        )  # Instantiate LoadForecast with specified parameters
        leistung_haushalt = lf.get_stats_for_date_range(date_now, date)[
            0
        ]  # Get expected household load for the date range

        gesamtlast = LoadAggregator(prediction_hours=prediction_hours)  # Create Gesamtlast instance
        gesamtlast.add_load(
            "Haushalt", leistung_haushalt
        )  # Add household load to total load calculation

        # ###############
        # # WP (Heat Pump)
        # ##############
        # leistung_wp = wp.simulate_24h(temperature_forecast)  # Simulate heat pump load for 24 hours
        # gesamtlast.hinzufuegen("Heatpump", leistung_wp)  # Add heat pump load to total load calculation

        last = gesamtlast.calculate_total_load()  # Calculate total load
        print(last)
        return jsonify(last)  # Return total load as JSON


@app.route("/pvforecast", methods=["GET"])
def flask_pvprognose():
    if request.method == "GET":
        # Retrieve URL and AC power measurement from query parameters
        url = request.args.get("url")
        ac_power_measurement = request.args.get("ac_power_measurement")
        date_now, date = get_start_enddate(prediction_hours, startdate=datetime.now().date())

        ###############
        # PV Forecast
        ###############
        PVforecast = PVForecast(
            prediction_hours=prediction_hours, url=url
        )  # Instantiate PVForecast with given parameters
        if isfloat(ac_power_measurement):  # Check if the AC power measurement is a valid float
            PVforecast.update_ac_power_measurement(
                date_time=datetime.now(),
                ac_power_measurement=float(ac_power_measurement),
            )  # Update measurement

        # Get PV forecast and temperature forecast for the specified date range
        pv_forecast = PVforecast.get_pv_forecast_for_date_range(date_now, date)
        temperature_forecast = PVforecast.get_temperature_for_date_range(date_now, date)

        # Return both forecasts as a JSON response
        ret = {
            "temperature": temperature_forecast.tolist(),
            "pvpower": pv_forecast.tolist(),
        }
        return jsonify(ret)


@app.route("/optimize", methods=["POST"])
def flask_optimize():
    with open(
        "C:\\Users\\drbac\\OneDrive\\Dokumente\\PythonPojects\\EOS\\debug_output.txt",
        "a",
    ) as f:
        f.write("Test\n")

    if request.method == "POST":
        from datetime import datetime

        # Retrieve optimization parameters from the request JSON
        parameter = request.json

        # Check for required parameters
        required_parameters = [
            "preis_euro_pro_wh_akku",
            "strompreis_euro_pro_wh",
            "gesamtlast",
            "pv_akku_cap",
            "einspeiseverguetung_euro_pro_wh",
            "pv_forecast",
            "temperature_forecast",
            "eauto_min_soc",
            "eauto_cap",
            "eauto_charge_efficiency",
            "eauto_charge_power",
            "eauto_soc",
            "pv_soc",
            "start_solution",
            "haushaltsgeraet_dauer",
            "haushaltsgeraet_wh",
        ]
        # Identify any missing parameters
        missing_params = [p for p in required_parameters if p not in parameter]
        if missing_params:
            return jsonify(
                {"error": f"Missing parameter: {', '.join(missing_params)}"}
            ), 400  # Return error for missing parameters

        # Optional min SoC PV Battery
        if "min_soc_prozent" not in parameter:
            parameter["min_soc_prozent"] = None

        # Perform optimization simulation
        result = opt_class.optimierung_ems(parameter=parameter, start_hour=datetime.now().hour)
        print(result)
        # convert to JSON (None accepted by dumps)
        return jsonify(result)


@app.route("/visualization_results.pdf")
def get_pdf():
    # Endpoint to serve the generated PDF with visualization results
    return send_from_directory(
        os.path.abspath(output_dir), "visualization_results.pdf"
    )  # Adjust the directory if needed


@app.route("/site-map")
def site_map():
    # Function to generate a site map of valid routes in the application
    def print_links(links):
        content = "<h1>Valid routes</h1><ul>"
        for link in links:
            content += f"<li><a href='{link}'>{link}</a></li>"
        content += "</ul>"
        return content

    # Check if the route has no empty parameters
    def has_no_empty_params(rule):
        defaults = rule.defaults if rule.defaults is not None else ()
        arguments = rule.arguments if rule.arguments is not None else ()
        return len(defaults) >= len(arguments)

    # Collect all valid GET routes without empty parameters
    links = []
    for rule in app.url_map.iter_rules():
        if "GET" in rule.methods and has_no_empty_params(rule):
            url = url_for(rule.endpoint, **(rule.defaults or {}))
            links.append(url)
    return print_links(sorted(links))  # Return the sorted links as HTML


@app.route("/")
def root():
    # Redirect the root URL to the site map
    return redirect("/site-map", code=302)


if __name__ == "__main__":
    try:
        # Set host and port from environment variables or defaults
        host = os.getenv("FLASK_RUN_HOST", "0.0.0.0")
        port = os.getenv("FLASK_RUN_PORT", 8503)
        app.run(debug=True, host=host, port=port)  # Run the Flask application
    except Exception as e:
        print(
            f"Could not bind to host {host}:{port}. Error: {e}"
        )  # Error handling for binding issues
