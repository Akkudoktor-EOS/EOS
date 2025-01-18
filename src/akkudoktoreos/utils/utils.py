import json
from typing import Any, Optional

import numpy as np

from akkudoktoreos.config.configabc import SettingsBaseModel
from akkudoktoreos.core.logging import get_logger

logger = get_logger(__name__)


class classproperty(property):
    def __get__(self, _: Any, owner_cls: Optional[type[Any]] = None) -> Any:
        if owner_cls is None:
            return self
        assert self.fget is not None
        return self.fget(owner_cls)


class UtilsCommonSettings(SettingsBaseModel):
    """Utils Configuration."""

    pass


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
#     hours = 23  # Adjust to 23 hours for DST change days
# else:
#     hours = 24  # Default value for days without DST change
