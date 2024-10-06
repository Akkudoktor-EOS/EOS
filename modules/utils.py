from datetime import datetime, timedelta
from typing import Optional


def get_start_enddate(
    prediction_hours: int = 48, startdate: Optional[datetime] = None
) -> tuple[str, str]:
    ############
    # Parameter
    ############
    if startdate is None:
        date = (datetime.now().date() + timedelta(hours=prediction_hours)).strftime(
            "%Y-%m-%d"
        )
        date_now = datetime.now().strftime("%Y-%m-%d")
    else:
        date = (startdate + timedelta(hours=prediction_hours)).strftime("%Y-%m-%d")
        date_now = startdate.strftime("%Y-%m-%d")
    return date_now, date
