import json

import numpy as np


class NumpyEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, np.ndarray):
            return obj.tolist()  # Convert NumPy arrays to lists
        if isinstance(obj, np.generic):
            return obj.item()  # Convert NumPy scalars to native Python types
        return super(NumpyEncoder, self).default(obj)

    @staticmethod
    def dumps(data):
        """Static method to serialize a Python object into a JSON string using NumpyEncoder.

        Args:
            data: The Python object to serialize.

        Returns:
            str: A JSON string representation of the object.
        """
        return json.dumps(data, cls=NumpyEncoder)
