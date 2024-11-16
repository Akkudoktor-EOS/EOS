import datetime
import zoneinfo


# currently unused
def ist_dst_wechsel(tag: datetime.datetime, timezone="Europe/Berlin") -> bool:
    """Checks if Daylight Saving Time (DST) starts or ends on a given day."""
    tz = zoneinfo.ZoneInfo(timezone)
    # Get the current day and the next day
    current_day = datetime.datetime(tag.year, tag.month, tag.day)
    next_day = current_day + datetime.timedelta(days=1)

    # Check if the UTC offsets are different (indicating a DST change)
    dst_change = current_day.replace(tzinfo=tz).dst() != next_day.replace(tzinfo=tz).dst()

    return dst_change


# # Example usage
# start_date = datetime.datetime(2024, 3, 31)  # Date of the DST change
# if ist_dst_wechsel(start_date):
#     prediction_hours = 23  # Adjust to 23 hours for DST change days
# else:
#     prediction_hours = 24  # Default value for days without DST change
