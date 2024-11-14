"""Server Module."""

from typing import Optional

from pydantic import Field, IPvAnyAddress, field_validator

from akkudoktoreos.config.configabc import SettingsBaseModel
from akkudoktoreos.utils.logutil import get_logger

logger = get_logger(__name__, logging_level="DEBUG")


class ServerCommonSettings(SettingsBaseModel):
    """Common server settings.

    Attributes:
        To be added
    """

    server_fastapi_host: Optional[IPvAnyAddress] = Field(
        default="0.0.0.0", description="FastAPI server IP address."
    )
    server_fastapi_port: Optional[int] = Field(
        default=8503, description="FastAPI server IP port number."
    )
    server_fasthtml_host: Optional[IPvAnyAddress] = Field(
        default="0.0.0.0", description="FastHTML server IP address."
    )
    server_fasthtml_port: Optional[int] = Field(
        default=8504, description="FastHTML server IP port number."
    )

    @field_validator("server_fastapi_port", "server_fasthtml_port")
    def validate_server_port(cls, value: Optional[int]) -> Optional[int]:
        if value is not None and not (1024 <= value <= 49151):
            raise ValueError("Server port number must be between 1024 and 49151.")
        return value
