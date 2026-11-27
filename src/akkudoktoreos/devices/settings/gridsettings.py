"""Settings of the grid connection of the installation."""

from typing import Optional

from pydantic import Field

from akkudoktoreos.config.configabc import ConfigScope, SettingsBaseModel


class GridConnectionSettings(SettingsBaseModel):
    """Limits of the grid connection point, shared by all devices of the installation.

    Both limits are electrical power at the connection point. They stand next to the
    inverter and battery settings because they are a physical property of the installation
    (fuse, network operator limit), not of a tariff or of an algorithm.
    """

    import_limit_w: Optional[float] = Field(
        default=None,
        gt=0,
        allow_inf_nan=False,
        json_schema_extra={
            "x-scope": [str(ConfigScope.OHMRAKEL42)],
            "description": (
                "Maximum power drawn from the grid [W], for example a fuse or a peak limit. "
                "The optimizer prices every kWh above it with 'import_limit_penalty_eur_per_kwh', "
                "so the limit is practically hard. None means no limit."
            ),
            "examples": [None, 11000],
        },
    )
    export_limit_w: Optional[float] = Field(
        default=None,
        gt=0,
        allow_inf_nan=False,
        json_schema_extra={
            "x-scope": [str(ConfigScope.OHMRAKEL42)],
            "description": (
                "Maximum power fed into the grid [W], for example a network operator limit. "
                "Surplus above it is curtailed and counts as a loss. None means no limit."
            ),
            "examples": [None, 7000],
        },
    )
    import_limit_penalty_eur_per_kwh: float = Field(
        default=10.0,
        ge=0,
        allow_inf_nan=False,
        json_schema_extra={
            "x-scope": [str(ConfigScope.OHMRAKEL42)],
            "description": (
                "Price of exceeding the import limit [EUR/kWh]. The default of 10 EUR/kWh lies "
                "above the highest price of the European intraday markets (9.99 EUR/kWh), so no "
                "price difference can pay for an excess."
            ),
            "examples": [10.0],
        },
    )
