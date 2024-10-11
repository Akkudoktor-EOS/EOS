from datetime import datetime, timedelta

output_dir = "output"

prediction_hours = 48
optimization_hours = 24
strafe = 10
moegliche_ladestroeme_in_prozent = [
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
