#!/usr/bin/env python3

import os
import sys
from datetime import datetime

import matplotlib

# Sets the Matplotlib backend to 'Agg' for rendering plots in environments without a display
matplotlib.use("Agg")
from datetime import timedelta

import pandas as pd
from flask import Flask, jsonify, redirect, request, send_from_directory, url_for

from modules.class_load import LoadForecast
from modules.class_load_container import Gesamtlast
from modules.class_load_corrector import LoadPredictionAdjuster
from modules.class_optimize import isfloat, optimization_problem
from modules.class_pv_forecast import PVForecast
from modules.class_soc_calc import BatteryDataProcessor
from modules.class_strompreis import HourlyElectricityPriceForecast

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import db_config, get_start_enddate, optimization_hours, prediction_hours

app = Flask(__name__)

opt_class = optimization_problem(
    prediction_hours=prediction_hours, strafe=10, optimization_hours=optimization_hours
)

# @app.route('/last_correction', methods=['GET'])
# def flask_last_correction():
#     if request.method == 'GET':
#         year_energy = float(request.args.get("year_energy"))
#         date_now, date = get_start_enddate(prediction_hours, startdate=datetime.now().date())
#         ###############
#         # Load Forecast
#         ###############
#         lf = LoadForecast(filepath=r'load_profiles.npz', year_energy=year_energy)
#         leistung_haushalt = lf.get_stats_for_date_range(date_now, date)[0]  # Only the expected value!
#
#         gesamtlast = Gesamtlast(prediction_hours=prediction_hours)
#         gesamtlast.hinzufuegen("Haushalt", leistung_haushalt)
#         # ###############
#         # Heat Pump (WP)
#         # ##############
#         # leistung_wp = wp.simulate_24h(temperature_forecast)
#         # gesamtlast.hinzufuegen("Heatpump", leistung_wp)
#         last = gesamtlast.gesamtlast_berechnen()
#         print(last)
#         return jsonify(last.tolist())


@app.route("/soc", methods=["GET"])
def flask_soc():
    # MariaDB connection details
    config = db_config

    # Set parameters for SOC (State of Charge) calculation
    voltage_high_threshold = 55.4  # 100% SoC
    voltage_low_threshold = 46.5  # 0% SoC
    current_low_threshold = 2  # Low current threshold for both states
    gap = 30  # Time gap in minutes for grouping maxima/minima
    bat_capacity = 33 * 1000 / 48  # Battery capacity in watt-hours

    # Define the reference time point (3 weeks ago)
    zeitpunkt_x = (datetime.now() - timedelta(weeks=3)).strftime("%Y-%m-%d %H:%M:%S")

    # Instantiate BatteryDataProcessor and perform calculations
    processor = BatteryDataProcessor(
        config,
        voltage_high_threshold,
        voltage_low_threshold,
        current_low_threshold,
        gap,
        bat_capacity,
    )
    processor.connect_db()
    processor.fetch_data(zeitpunkt_x)
    processor.process_data()
    last_points_100_df, last_points_0_df = processor.find_soc_points()
    soc_df, integration_results = processor.calculate_resetting_soc(
        last_points_100_df, last_points_0_df
    )
    # soh_df = processor.calculate_soh(integration_results)  # Optional State of Health calculation
    processor.update_database_with_soc(soc_df)  # Update database with SOC data
    # processor.plot_data(last_points_100_df, last_points_0_df, soc_df)  # Optional data visualization
    processor.disconnect_db()  # Disconnect from the database

    return jsonify("Done")


@app.route("/strompreis", methods=["GET"])
def flask_strompreis():
    # Get the current date and the end date based on prediction hours
    date_now, date = get_start_enddate(
        prediction_hours, startdate=datetime.now().date()
    )
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
    prediction_hours = int(
        data.get("hours", 48)
    )  # Default to 48 hours if not specified

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
    lf = LoadForecast(filepath=r"load_profiles.npz", year_energy=year_energy)
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
    future_predictions = adjuster.predict_next_hours(
        prediction_hours
    )  # Predict future load

    # Extract household power predictions
    leistung_haushalt = future_predictions["Adjusted Pred"].values
    gesamtlast = Gesamtlast(prediction_hours=prediction_hours)
    gesamtlast.hinzufuegen(
        "Haushalt", leistung_haushalt
    )  # Add household load to total load calculation

    # Calculate the total load
    last = gesamtlast.gesamtlast_berechnen()  # Compute total load
    return jsonify(last.tolist())


# @app.route('/gesamtlast', methods=['GET'])
# def flask_gesamtlast():
#     if request.method == 'GET':
#         year_energy = float(request.args.get("year_energy"))  # Get annual energy value from query parameters
#         prediction_hours = int(request.args.get("hours", 48))  # Default to 48 hours if not specified
#         date_now = datetime.now()  # Get the current date and time
#         end_date = (date_now + timedelta(hours=prediction_hours)).strftime('%Y-%m-%d %H:%M:%S')  # Calculate end date based on prediction hours

#         ###############
#         # Load Forecast
#         ###############
#         # Instantiate LastEstimator to retrieve measured data
#         estimator = LastEstimator()
#         start_date = (date_now - timedelta(days=60)).strftime('%Y-%m-%d')  # Start date: last 60 days
#         end_date = date_now.strftime('%Y-%m-%d')  # Current date

#         last_df = estimator.get_last(start_date, end_date)  # Get last load data

#         selected_columns = last_df[['timestamp', 'Last']]  # Select relevant columns
#         selected_columns['time'] = pd.to_datetime(selected_columns['timestamp']).dt.floor('H')  # Floor timestamps to the nearest hour
#         selected_columns['Last'] = pd.to_numeric(selected_columns['Last'], errors='coerce')  # Convert 'Last' to numeric, coerce errors
#         cleaned_data = selected_columns.dropna()  # Clean data by dropping NaN values

#         # Instantiate LoadForecast
#         lf = LoadForecast(filepath=r'load_profiles.npz', year_energy=year_energy)

#         # Generate forecast data
#         forecast_list = []  # List to hold daily forecasts
#         for single_date in pd.date_range(cleaned_data['time'].min().date(), cleaned_data['time'].max().date()):  # Iterate over date range
#             date_str = single_date.strftime('%Y-%m-%d')  # Format date
#             daily_forecast = lf.get_daily_stats(date_str)  # Get daily stats from LoadForecast
#             mean_values = daily_forecast[0]  # Extract mean values
#             hours = [single_date + pd.Timedelta(hours=i) for i in range(24)]  # Generate hours for the day
#             daily_forecast_df = pd.DataFrame({'time': hours, 'Last Pred': mean_values})  # Create DataFrame for daily forecast
#             forecast_list.append(daily_forecast_df)  # Append to the list

#         forecast_df = pd.concat(forecast_list, ignore_index=True)  # Concatenate all daily forecasts

#         # Create LoadPredictionAdjuster instance
#         adjuster = LoadPredictionAdjuster(cleaned_data, forecast_df, lf)
#         adjuster.calculate_weighted_mean()  # Calculate weighted mean for adjustments
#         adjuster.adjust_predictions()  # Adjust predictions based on measured data

#         # Predict the next hours
#         future_predictions = adjuster.predict_next_hours(prediction_hours)  # Predict future load

#         leistung_haushalt = future_predictions['Adjusted Pred'].values  # Extract household power predictions

#         gesamtlast = Gesamtlast(prediction_hours=prediction_hours)  # Create Gesamtlast instance
#         gesamtlast.hinzufuegen("Haushalt", leistung_haushalt)  # Add household load to total load calculation

#         # ###############
#         # # WP (Heat Pump)
#         # ##############
#         # leistung_wp = wp.simulate_24h(temperature_forecast)  # Simulate heat pump load for 24 hours
#         # gesamtlast.hinzufuegen("Heatpump", leistung_wp)  # Add heat pump load to total load calculation

#         last = gesamtlast.gesamtlast_berechnen()  # Calculate total load
#         print(last)  # Output total load
#         return jsonify(last.tolist())  # Return total load as JSON


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
        lf = LoadForecast(
            filepath=r"load_profiles.npz", year_energy=year_energy
        )  # Instantiate LoadForecast with specified parameters
        leistung_haushalt = lf.get_stats_for_date_range(date_now, date)[
            0
        ]  # Get expected household load for the date range

        gesamtlast = Gesamtlast(
            prediction_hours=prediction_hours
        )  # Create Gesamtlast instance
        gesamtlast.hinzufuegen(
            "Haushalt", leistung_haushalt
        )  # Add household load to total load calculation

        # ###############
        # # WP (Heat Pump)
        # ##############
        # leistung_wp = wp.simulate_24h(temperature_forecast)  # Simulate heat pump load for 24 hours
        # gesamtlast.hinzufuegen("Heatpump", leistung_wp)  # Add heat pump load to total load calculation

        last = gesamtlast.gesamtlast_berechnen()  # Calculate total load
        print(last)  # Output total load
        return jsonify(last.tolist())  # Return total load as JSON


@app.route("/pvforecast", methods=["GET"])
def flask_pvprognose():
    if request.method == "GET":
        # Retrieve URL and AC power measurement from query parameters
        url = request.args.get("url")
        ac_power_measurement = request.args.get("ac_power_measurement")
        date_now, date = get_start_enddate(
            prediction_hours, startdate=datetime.now().date()
        )

        ###############
        # PV Forecast
        ###############
        PVforecast = PVForecast(
            prediction_hours=prediction_hours, url=url
        )  # Instantiate PVForecast with given parameters
        if isfloat(
            ac_power_measurement
        ):  # Check if the AC power measurement is a valid float
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

        # Perform optimization simulation
        result = opt_class.optimierung_ems(
            parameter=parameter, start_hour=datetime.now().hour
        )

        return jsonify(result)  # Return optimization results as JSON


@app.route("/visualisierungsergebnisse.pdf")
def get_pdf():
    # Endpoint to serve the generated PDF with visualization results
    return send_from_directory(
        "", "visualisierungsergebnisse.pdf"
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

# PV Forecast:
#   object {
#    pvpower: array[48]
#    temperature: array[48]
#   }
