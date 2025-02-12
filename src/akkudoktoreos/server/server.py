"""Server Module."""

import os
from typing import Optional

from pydantic import Field, IPvAnyAddress, field_validator

from akkudoktoreos.config.configabc import SettingsBaseModel
from akkudoktoreos.core.logging import get_logger

logger = get_logger(__name__)


def get_default_host() -> str:
    if os.name == "nt":
        return "127.0.0.1"
    return "0.0.0.0"


class ServerCommonSettings(SettingsBaseModel):
    """Server Configuration.

    Attributes:
        To be added
    """

    host: Optional[IPvAnyAddress] = Field(
        default=get_default_host(), description="EOS server IP address."
    )
    port: Optional[int] = Field(default=8503, description="EOS server IP port number.")
    verbose: Optional[bool] = Field(default=False, description="Enable debug output")
    startup_eosdash: Optional[bool] = Field(
        default=True, description="EOS server to start EOSdash server."
    )
    eosdash_host: Optional[IPvAnyAddress] = Field(
        default=get_default_host(), description="EOSdash server IP address."
    )
    eosdash_port: Optional[int] = Field(default=8504, description="EOSdash server IP port number.")

    @field_validator("port", "eosdash_port")
    def validate_server_port(cls, value: Optional[int]) -> Optional[int]:
        if value is not None and not (1024 <= value <= 49151):
            raise ValueError("Server port number must be between 1024 and 49151.")
        return value
