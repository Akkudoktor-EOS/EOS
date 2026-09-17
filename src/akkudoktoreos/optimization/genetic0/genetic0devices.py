"""Genetic0 optimization algorithm device interfaces/ parameters."""

from typing import Optional

from pydantic import Field

from akkudoktoreos.optimization.genetic0.genetic0abc import Genetic0ParametersBaseModel


class Genetic0DeviceParameters(Genetic0ParametersBaseModel):
    device_id: str = Field(json_schema_extra={"description": "ID of device", "examples": "device1"})
    hours: Optional[int] = Field(
        default=None,
        gt=0,
        json_schema_extra={
            "description": "Number of prediction hours. Defaults to global config prediction hours.",
            "examples": [None],
        },
    )
