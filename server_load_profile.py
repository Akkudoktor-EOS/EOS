from datetime import datetime
from pprint import pprint

from flask import Flask, jsonify, request

import modules.class_load as cl

app = Flask(__name__)

# Constants
DATE_FORMAT = "%Y-%m-%d"
EXPECTED_ARRAY_SHAPE = (2, 24)
FILEPATH = r".\load_profiles.npz"


def get_load_forecast(year_energy):
    """Initialize LoadForecast with the given year_energy."""
    return cl.LoadForecast(filepath=FILEPATH, year_energy=float(year_energy))


def validate_date(date_str):
    """Validate the date string and return a datetime object."""
    try:
        return datetime.strptime(date_str, DATE_FORMAT)
    except ValueError:
        raise ValueError(
            "Date is not in the correct format. Expected format: YYYY-MM-DD."
        )


@app.route("/getdata", methods=["GET"])
def get_data():
    # Retrieve the date and year_energy from query parameters
    date_str = request.args.get("date")
    year_energy = request.args.get("year_energy")

    if not date_str or not year_energy:
        return jsonify(
            {"error": "Missing 'date' or 'year_energy' query parameter."}
        ), 400

    try:
        # Validate and convert the date
        date_obj = validate_date(date_str)
        lf = get_load_forecast(year_energy)

        # Get daily statistics for the requested date
        array_list = lf.get_daily_stats(date_str)
        pprint(array_list)
        pprint(array_list.shape)

        # Check if the shape of the array is valid
        if array_list.shape == EXPECTED_ARRAY_SHAPE:
            return jsonify({date_str: array_list.tolist()})
        else:
            return jsonify({"error": "Data not found for the given date."}), 404

    except ValueError as e:
        # Return a descriptive error message for date validation issues
        return jsonify({"error": str(e)}), 400
    except Exception:
        # Return a generic error message for unexpected errors
        return jsonify({"error": "An unexpected error occurred."}), 500


if __name__ == "__main__":
    app.run(debug=True)
