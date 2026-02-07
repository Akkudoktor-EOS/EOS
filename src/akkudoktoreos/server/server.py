"""Server Module."""

import grp
import ipaddress
import os
import pwd
import re
import socket
import time
from typing import Optional

import psutil
from loguru import logger
from pydantic import Field, field_validator

from akkudoktoreos.config.configabc import SettingsBaseModel
from akkudoktoreos.core.coreabc import get_config


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


def drop_root_privileges(run_as_user: Optional[str] = None) -> bool:
    """Drop root privileges and switch execution to a less privileged user.

    This function transitions the running process from root (UID 0) to the
    specified unprivileged user. It sets UID, GID, supplementary groups, and
    updates environment variables to reflect the new user context.

    If the process is not running as root, no privilege changes are made.

    Args:
        run_as_user (str | None):
            The name of the target user to switch to.
            If ``None`` (default), the current effective user is used and
            no privilege change is attempted.

    Returns:
        bool:
            ``True`` if privileges were successfully dropped OR the process is
            already running as the target user.
            ``False`` if privilege dropping failed.

    Notes:
        - This must be called very early during startup, before opening files,
          creating sockets, or starting threads.
        - Dropping privileges is irreversible within the same process.
        - The target user must exist inside the container (valid entry in
          ``/etc/passwd`` and ``/etc/group``).
    """
    # Determine current user
    current_user = pwd.getpwuid(os.geteuid()).pw_name

    # No action needed if already running as the desired user
    if run_as_user is None or run_as_user == current_user:
        return True

    # Cannot switch users unless running as root
    if os.geteuid() != 0:
        logger.error(
            f"Privilege switch requested to '{run_as_user}' "
            f"but process is not root (running as '{current_user}')."
        )
        return False

    # Resolve target user info
    try:
        pw_record = pwd.getpwnam(run_as_user)
    except KeyError:
        logger.error(f"Privilege switch failed: user '{run_as_user}' does not exist.")
        return False

    user_uid: int = pw_record.pw_uid
    user_gid: int = pw_record.pw_gid

    try:
        # Get all groups where the user is listed as a member
        supplementary_groups: list[int] = [
            g.gr_gid for g in grp.getgrall() if run_as_user in g.gr_mem
        ]

        # Ensure the primary group is included (it usually is NOT in gr_mem)
        if user_gid not in supplementary_groups:
            supplementary_groups.append(user_gid)

        # Apply groups, gid, uid (in that order)
        os.setgroups(supplementary_groups)
        os.setgid(user_gid)
        os.setuid(user_uid)
    except Exception as e:
        logger.error(f"Privilege switch failed: {e}")
        return False

    # Update environment variables to reflect the new user identity
    os.environ["HOME"] = pw_record.pw_dir
    os.environ["LOGNAME"] = run_as_user
    os.environ["USER"] = run_as_user

    # Restrictive umask
    os.umask(0o077)

    # Verify that privilege drop was successful
    if os.geteuid() != user_uid or os.getegid() != user_gid:
        logger.error(
            f"Privilege drop sanity check failed: now uid={os.geteuid()}, gid={os.getegid()}, "
            f"expected uid={user_uid}, gid={user_gid}"
        )
        return False

    logger.info(
        f"Switched privileges to user '{run_as_user}' "
        f"(uid={user_uid}, gid={user_gid}, groups={supplementary_groups})"
    )
    return True


def fix_data_directories_permissions(run_as_user: Optional[str] = None) -> None:
    """Ensure correct ownership for data directories.

    This function recursively updates the owner and group of the data directories and all of its
    subdirectories and files so that they belong to the given user.

    The function may require root privileges to change file ownership. It logs an error message
    if a path ownership can not be updated.

    Args:
        run_as_user (Optional[str]): The user who should own the data directories and files.
            Defaults to current one.
    """
    config_eos = get_config()

    base_dirs = [
        config_eos.general.data_folder_path,
        config_eos.general.data_output_path,
        config_eos.general.config_folder_path,
        config_eos.cache.path(),
    ]

    error_msg: Optional[str] = None

    if run_as_user is None:
        # Get current user - try to ensure current user can access the data directories
        run_as_user = pwd.getpwuid(os.geteuid()).pw_name

    try:
        pw_record = pwd.getpwnam(run_as_user)
    except KeyError as e:
        error_msg = f"Data directories '{base_dirs}' permission fix failed: user '{run_as_user}' does not exist."
        logger.error(error_msg)
        return

    uid = pw_record.pw_uid
    gid = pw_record.pw_gid

    # Walk directory tree and fix permissions
    for base_dir in base_dirs:
        if base_dir is None:
            continue
        # ensure base dir exists
        try:
            base_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.error(f"Could not setup data dir '{base_dir}': {e}")
            continue
        for root, dirs, files in os.walk(base_dir):
            for name in dirs + files:
                path = os.path.join(root, name)
                try:
                    os.chown(path, uid, gid)
                except PermissionError as e:
                    error_msg = f"Permission denied while updating ownership of '{path}' to user '{run_as_user}'"
                    logger.error(error_msg)
                except Exception as e:
                    error_msg = (
                        f"Updating ownership failed of '{path}' to user '{run_as_user}': {e}"
                    )
                    logger.error(error_msg)
        # Also fix the base directory itself
        try:
            os.chown(base_dir, uid, gid)
        except PermissionError as e:
            error_msg = (
                f"Permission denied while updating ownership of '{path}' to user '{run_as_user}'"
            )
            logger.error(error_msg)
        except Exception as e:
            error_msg = f"Updating ownership failed of '{path}' to user '{run_as_user}': {e}"
            logger.error(error_msg)

    if error_msg is None:
        logger.info(f"Updated ownership of '{base_dirs}' recursively to user '{run_as_user}'.")


class ServerCommonSettings(SettingsBaseModel):
    """Server Configuration."""

    host: Optional[str] = Field(
        default=get_default_host(),
        json_schema_extra={
            "description": "EOS server IP address. Defaults to 127.0.0.1.",
            "examples": ["127.0.0.1", "localhost"],
        },
    )
    port: Optional[int] = Field(
        default=8503,
        json_schema_extra={
            "description": "EOS server IP port number. Defaults to 8503.",
            "examples": [
                8503,
            ],
        },
    )
    verbose: Optional[bool] = Field(
        default=False, json_schema_extra={"description": "Enable debug output"}
    )
    startup_eosdash: Optional[bool] = Field(
        default=True,
        json_schema_extra={"description": "EOS server to start EOSdash server. Defaults to True."},
    )
    eosdash_host: Optional[str] = Field(
        default=None,
        json_schema_extra={
            "description": "EOSdash server IP address. Defaults to EOS server IP address.",
            "examples": ["127.0.0.1", "localhost"],
        },
    )
    eosdash_port: Optional[int] = Field(
        default=None,
        json_schema_extra={
            "description": "EOSdash server IP port number. Defaults to EOS server IP port number + 1.",
            "examples": [
                8504,
            ],
        },
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
