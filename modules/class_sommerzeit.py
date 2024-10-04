import datetime

import pytz


def ist_dst_wechsel(tag, timezone="Europe/Berlin"):
    """Checks if Daylight Saving Time (DST) starts or ends on a given day."""
    tz = pytz.timezone(timezone)
    # Get the current day and the next day
    current_day = datetime.datetime(tag.year, tag.month, tag.day)
    next_day = current_day + datetime.timedelta(days=1)

    # Localize the days in the given timezone
    current_day_localized = tz.localize(current_day, is_dst=None)
    next_day_localized = tz.localize(next_day, is_dst=None)

    # Check if the UTC offsets are different (indicating a DST change)
    dst_change = current_day_localized.dst() != next_day_localized.dst()

    return dst_change


# # Example usage
# start_date = datetime.datetime(2024, 3, 31)  # Date of the DST change
# if ist_dst_wechsel(start_date):
#     prediction_hours = 23  # Adjust to 23 hours for DST change days
# else:
#     prediction_hours = 24  # Default value for days without DST change
