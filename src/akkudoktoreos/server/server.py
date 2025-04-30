"""Server Module."""

import ipaddress
import re
import time
from typing import Optional, Union

import psutil
from pydantic import Field, IPvAnyAddress, field_validator

from akkudoktoreos.config.configabc import SettingsBaseModel
from akkudoktoreos.core.logging import get_logger

logger = get_logger(__name__)


def get_default_host() -> str:
    """Default host for EOS."""
    return "127.0.0.1"


def is_valid_ip_or_hostname(value: str) -> bool:
    """Validate whether a string is a valid IP address (IPv4 or IPv6) or hostname.

    This function first attempts to interpret the input as an IP address using the
    standard library `ipaddress` module. If that fails, it checks whether the input
    is a valid hostname according to RFC 1123, which allows domain names consisting
    of alphanumeric characters and hyphens, with specific length and structure rules.

    Args:
        value (str): The input string to validate.

    Returns:
        bool: True if the input is a valid IP address or hostname, False otherwise.
    """
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        pass

    if len(value) > 253:
        return False

    hostname_regex = re.compile(
        r"^(?=.{1,253}$)(?!-)[A-Z\d-]{1,63}(?<!-)"
        r"(?:\.(?!-)[A-Z\d-]{1,63}(?<!-))*\.?$",
        re.IGNORECASE,
    )
    return bool(hostname_regex.fullmatch(value))


def wait_for_port_free(port: int, timeout: int = 0, waiting_app_name: str = "App") -> bool:
    """Wait for a network port to become free, with timeout.

    Checks if the port is currently in use and logs warnings with process details.
    Retries every 3 seconds until timeout is reached.

    Args:
        port: The network port number to check
        timeout: Maximum seconds to wait (0 means check once without waiting)
        waiting_app_name: Name of the application waiting for the port

    Returns:
        bool: True if port is free, False if port is still in use after timeout

    Raises:
        ValueError: If port number or timeout is invalid
        psutil.Error: If there are problems accessing process information
    """
    if not 0 <= port <= 65535:
        raise ValueError(f"Invalid port number: {port}")
    if timeout < 0:
        raise ValueError(f"Invalid timeout: {timeout}")

    def get_processes_using_port() -> list[dict]:
        """Get info about processes using the specified port."""
        processes: list[dict] = []
        seen_pids: set[int] = set()

        try:
            for conn in psutil.net_connections(kind="inet"):
                if conn.laddr.port == port and conn.pid not in seen_pids:
                    try:
                        process = psutil.Process(conn.pid)
                        seen_pids.add(conn.pid)
                        processes.append(process.as_dict(attrs=["pid", "cmdline"]))
                    except psutil.NoSuchProcess:
                        continue
        except psutil.Error as e:
            logger.error(f"Error checking port {port}: {e}")
            raise

        return processes

    retries = max(int(timeout / 3), 1) if timeout > 0 else 1

    for _ in range(retries):
        process_info = get_processes_using_port()

        if not process_info:
            return True

        if timeout <= 0:
            break

        logger.info(f"{waiting_app_name} waiting for port {port} to become free...")
        time.sleep(3)

    if process_info:
        logger.warning(
            f"{waiting_app_name} port {port} still in use after waiting {timeout} seconds."
        )
        for info in process_info:
            logger.warning(
                f"Process using port - PID: {info['pid']}, Command: {' '.join(info['cmdline'])}"
            )
        return False

    return True


class ServerCommonSettings(SettingsBaseModel):
    """Server Configuration."""

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

    @field_validator("host", "eosdash_host", mode="before")
    def validate_server_host(
        cls, value: Optional[Union[str, IPvAnyAddress]]
    ) -> Optional[Union[str, IPvAnyAddress]]:
        if isinstance(value, str):
            if not is_valid_ip_or_hostname(value):
                raise ValueError(f"Invalid host: {value}")
            if value.lower() in ("localhost", "loopback"):
                value = "127.0.0.1"
        return value

    @field_validator("port", "eosdash_port")
    def validate_server_port(cls, value: Optional[int]) -> Optional[int]:
        if value is not None and not (1024 <= value <= 49151):
            raise ValueError("Server port number must be between 1024 and 49151.")
        return value
