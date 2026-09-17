"""Genetic optimization algorithm device interfaces/ parameters."""

from typing import Optional

from pydantic import Field

from akkudoktoreos.optimization.genetic.geneticabc import GeneticParametersBaseModel


class DeviceParameters(GeneticParametersBaseModel):
    device_id: str = Field(json_schema_extra={"description": "ID of device", "examples": "device1"})
    hours: Optional[int] = Field(
        default=None,
        gt=0,
        json_schema_extra={
            "description": "Number of prediction hours. Defaults to global config prediction hours.",
            "examples": [None],
        },
    )
