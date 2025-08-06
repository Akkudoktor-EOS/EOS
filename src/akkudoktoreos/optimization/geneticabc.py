"""Genetic optimization algorithm abstract and base classes."""

from pydantic import ConfigDict

from akkudoktoreos.core.pydantic import PydanticBaseModel


class GeneticParametersBaseModel(PydanticBaseModel):
    """Pydantic base model for parameters for the GENETIC algorithm."""

    model_config = ConfigDict(extra="forbid")
