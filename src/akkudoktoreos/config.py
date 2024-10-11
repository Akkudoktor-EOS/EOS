import os
from datetime import datetime, timedelta


def parse_charging_rates(env_var):
    try:
        return [float(rate) for rate in env_var.split(",")]
    except ValueError:
        raise ValueError(
            "Invalid format for EOS_AVAILABLE_CHARGING_RATES_PERC. Expected a comma-separated list of floats."
        )

default_output_dir = "output"
# Default values
default_prediction_hours = 48
default_optimization_hours = 24
default_penalty = 10
default_charging_rates = [
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
]

# Get environment variables
output_dir = os.getenv("EOS_OUTPUT_DIR", default_output_dir)
prediction_hours = int(os.getenv("EOS_PREDICTION_HOURS", default_prediction_hours))
optimization_hours = int(
    os.getenv("EOS_OPTIMIZATION_HOURS", default_optimization_hours)
)
penalty = int(os.getenv("EOS_PENALTY", default_penalty))
env_charging_rates = os.getenv("EOS_AVAILABLE_CHARGING_RATES_PERC")

# Parse the environment variable or use the default
if env_charging_rates:
    available_charging_rates_in_percentage = parse_charging_rates(env_charging_rates)
else:
    available_charging_rates_in_percentage = default_charging_rates


def get_start_enddate(prediction_hours=48, startdate=None):
    ############
    # Parameter
    ############
    if startdate is None:
        date = (datetime.now().date() + timedelta(hours=prediction_hours)).strftime("%Y-%m-%d")
        date_now = datetime.now().strftime("%Y-%m-%d")
    else:
        date = (startdate + timedelta(hours=prediction_hours)).strftime("%Y-%m-%d")
        date_now = startdate.strftime("%Y-%m-%d")
    return date_now, date
