from datetime import datetime, timedelta

# Configuration
prediction_hours = 48
optimization_hours = 24
strafe = 10

# Lade Ströme in Prozent (possible charge currents in percentage)
moegliche_ladestroeme_in_prozent = [i / 16.0 for i in range(6, 17)] # [0.0 ,6.0/16.0, 7.0/16.0, 8.0/16.0, 9.0/16.0, 10.0/16.0, 11.0/16.0, 12.0/16.0, 13.0/16.0, 14.0/16.0, 15.0/16.0, 1.0 ]

# Optional Database Configuration
db_config = {
    'user': 'eos',
    'password': 'eos',
    'host': '127.0.0.1',
    'database': 'eos'
}

def get_start_enddate(prediction_hours=48, startdate=None):
    if startdate is None:
        startdate = datetime.now()

    date_now = startdate.strftime("%Y-%m-%d")
    end_date = (startdate + timedelta(hours=prediction_hours)).strftime("%Y-%m-%d")

    return date_now, end_date
