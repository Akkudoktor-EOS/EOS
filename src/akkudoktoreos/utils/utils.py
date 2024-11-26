import datetime
import json
import zoneinfo
from typing import Any

import numpy as np


# currently unused
def ist_dst_wechsel(tag: datetime.datetime, timezone: str = "Europe/Berlin") -> bool:
    """Checks if Daylight Saving Time (DST) starts or ends on a given day."""
    tz = zoneinfo.ZoneInfo(timezone)
    # Get the current day and the next day
    current_day = datetime.datetime(tag.year, tag.month, tag.day)
    next_day = current_day + datetime.timedelta(days=1)

    # Check if the UTC offsets are different (indicating a DST change)
    dst_change = current_day.replace(tzinfo=tz).dst() != next_day.replace(tzinfo=tz).dst()

    return dst_change


class NumpyEncoder(json.JSONEncoder):
    @classmethod
    def convert_numpy(cls, obj: Any) -> tuple[Any, bool]:
        if isinstance(obj, np.ndarray):
            # Convert NumPy arrays to lists
            return [
                None if isinstance(x, (int, float)) and np.isnan(x) else x for x in obj.tolist()
            ], True
        if isinstance(obj, np.generic):
            return obj.item(), True  # Convert NumPy scalars to native Python types
        return obj, False

    def default(self, obj: Any) -> Any:
        obj, converted = NumpyEncoder.convert_numpy(obj)
        if converted:
            return obj
        return super(NumpyEncoder, self).default(obj)

    @staticmethod
    def dumps(data: Any) -> str:
        """Static method to serialize a Python object into a JSON string using NumpyEncoder.

        Args:
            data: The Python object to serialize.

        Returns:
            str: A JSON string representation of the object.
        """
        return json.dumps(data, cls=NumpyEncoder)


# # Example usage
# start_date = datetime.datetime(2024, 3, 31)  # Date of the DST change
# if ist_dst_wechsel(start_date):
#     prediction_hours = 23  # Adjust to 23 hours for DST change days
# else:
#     prediction_hours = 24  # Default value for days without DST change
