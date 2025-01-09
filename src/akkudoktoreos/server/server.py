"""Server Module."""

from typing import Optional

from pydantic import Field, IPvAnyAddress, field_validator

from akkudoktoreos.config.configabc import SettingsBaseModel
from akkudoktoreos.core.logging import get_logger

logger = get_logger(__name__)


class ServerCommonSettings(SettingsBaseModel):
    """Common server settings.

    Attributes:
        To be added
    """

    server_eos_host: Optional[IPvAnyAddress] = Field(
        default="0.0.0.0", description="EOS server IP address."
    )
    server_eos_port: Optional[int] = Field(default=8503, description="EOS server IP port number.")
    server_eos_verbose: Optional[bool] = Field(default=False, description="Enable debug output")
    server_eos_startup_eosdash: Optional[bool] = Field(
        default=True, description="EOS server to start EOSdash server."
    )
    server_eosdash_host: Optional[IPvAnyAddress] = Field(
        default="0.0.0.0", description="EOSdash server IP address."
    )
    server_eosdash_port: Optional[int] = Field(
        default=8504, description="EOSdash server IP port number."
    )

    @field_validator("server_eos_port", "server_eosdash_port")
    def validate_server_port(cls, value: Optional[int]) -> Optional[int]:
        if value is not None and not (1024 <= value <= 49151):
            raise ValueError("Server port number must be between 1024 and 49151.")
        return value
