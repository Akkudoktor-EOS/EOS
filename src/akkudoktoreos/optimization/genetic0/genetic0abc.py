"""Genetic0 optimization algorithm abstract and base classes."""

from pydantic import ConfigDict

from akkudoktoreos.core.pydantic import PydanticBaseModel


class Genetic0ParametersBaseModel(PydanticBaseModel):
    """Pydantic base model for parameters for the GENETIC algorithm."""

    model_config = ConfigDict(extra="forbid")
