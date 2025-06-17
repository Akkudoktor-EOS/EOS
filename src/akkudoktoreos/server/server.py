"""Server Module."""

import ipaddress
import re
import socket
import time
from typing import Optional

import psutil
from loguru import logger
from pydantic import Field, field_validator

from akkudoktoreos.config.configabc import SettingsBaseModel


def get_default_host() -> str:
    """Default host for EOS."""
    return "127.0.0.1"


def get_host_ip() -> str:
    """IP address of the host machine.

    This function determines the IP address used to communicate with the outside world
    (e.g., for internet access), without sending any actual data. It does so by
    opening a UDP socket connection to a public IP address (Google DNS).

    Returns:
        str: The local IP address as a string. Returns '127.0.0.1' if unable to determine.

    Example:
        >>> get_host_ip()
        '192.168.1.42'
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"


def validate_ip_or_hostname(value: str) -> str:
    """Validate whether a string is a valid IP address (IPv4 or IPv6) or hostname.

    This function first attempts to interpret the input as an IP address using the
    standard library `ipaddress` module. If that fails, it checks whether the input
    is a valid hostname according to RFC 1123, which allows domain names consisting
    of alphanumeric characters and hyphens, with specific length and structure rules.

    Args:
        value (str): The input string to validate.

    Returns:
        IP address: Valid IP address or hostname.
    """
    try:
        ipaddress.ip_address(value)
        return value
    except ValueError:
        pass

    if len(value) > 253:
        raise ValueError(f"Not a valid hostname: {value}")

    hostname_regex = re.compile(
        r"^(?=.{1,253}$)(?!-)[A-Z\d-]{1,63}(?<!-)"
        r"(?:\.(?!-)[A-Z\d-]{1,63}(?<!-))*\.?$",
        re.IGNORECASE,
    )
    if not bool(hostname_regex.fullmatch(value)):
        raise ValueError(f"Not a valid hostname: {value}")

    ip = socket.gethostbyname(value)
    if ip is None:
        raise ValueError(f"Unknown host: {value}")

    return value


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

    host: Optional[str] = Field(
        default=get_default_host(),
        description="EOS server IP address. Defaults to 127.0.0.1.",
        examples=["127.0.0.1", "localhost"],
    )
    port: Optional[int] = Field(
        default=8503,
        description="EOS server IP port number. Defaults to 8503.",
        examples=[
            8503,
        ],
    )
    verbose: Optional[bool] = Field(default=False, description="Enable debug output")
    startup_eosdash: Optional[bool] = Field(
        default=True, description="EOS server to start EOSdash server. Defaults to True."
    )
    eosdash_host: Optional[str] = Field(
        default=None,
        description="EOSdash server IP address. Defaults to EOS server IP address.",
        examples=["127.0.0.1", "localhost"],
    )
    eosdash_port: Optional[int] = Field(
        default=None,
        description="EOSdash server IP port number. Defaults to EOS server IP port number + 1.",
        examples=[
            8504,
        ],
    )

    @field_validator("host", "eosdash_host", mode="before")
    def validate_server_host(cls, value: Optional[str]) -> Optional[str]:
        if isinstance(value, str):
            value = validate_ip_or_hostname(value)
        return value

    @field_validator("port", "eosdash_port")
    def validate_server_port(cls, value: Optional[int]) -> Optional[int]:
        if value is not None and not (1024 <= value <= 49151):
            raise ValueError("Server port number must be between 1024 and 49151.")
        return value
