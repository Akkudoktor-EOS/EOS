"""Server Module."""

import errno
import ipaddress
import os
import re
import socket
import sys
import time
from typing import Any, Optional

try:
    # Only available on Linux/Unix type systems
    import grp
    import pwd
except ModuleNotFoundError:
    grp = None  # type: ignore[assignment]
    pwd = None  # type: ignore[assignment]

import psutil
from loguru import logger
from pydantic import Field, field_validator

from akkudoktoreos.config.configabc import SettingsBaseModel, is_home_assistant_addon
from akkudoktoreos.core.coreabc import get_config


def get_default_host() -> str:
    """Default host for EOS."""
    return "127.0.0.1"


def get_default_port() -> int:
    """Default port for EOS."""
    return 8503


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


def _is_port_available(port: int) -> bool:
    """Check TCP binding on local IPv4 and IPv6 addresses without inspecting PIDs."""
    addresses = [(socket.AF_INET, "")]
    if socket.has_ipv6:
        addresses.append((socket.AF_INET6, "::"))

    # Reuse avoids waiting for TIME_WAIT connections. On macOS, a wildcard bind
    # with reuse can coexist with a listener on a specific interface, so probe
    # each local address too. Interface enumeration does not inspect processes.
    try:
        interface_addresses = [
            (address.family, address.address)
            for interface in psutil.net_if_addrs().values()
            for address in interface
            if address.family == socket.AF_INET
            or (socket.has_ipv6 and address.family == socket.AF_INET6)
        ]
    except (psutil.Error, OSError):
        interface_addresses = []
    addresses.extend(interface_addresses)

    for family, address in dict.fromkeys(addresses):
        try:
            with socket.socket(family, socket.SOCK_STREAM) as probe:
                # Fall back to exclusive probes if interface details are unavailable.
                if os.name != "nt" and interface_addresses:
                    probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                exclusive = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
                if os.name == "nt" and exclusive is not None:
                    probe.setsockopt(socket.SOL_SOCKET, exclusive, 1)
                if family == socket.AF_INET6:
                    probe.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
                probe.bind((address, port))
        except OSError as error:
            if error.errno in (errno.EADDRINUSE, errno.EACCES):
                return False
            if address not in ("", "::") and error.errno == errno.EADDRNOTAVAIL:
                # A local interface may disappear after enumeration.
                continue
            if family == socket.AF_INET6 and error.errno in (
                errno.EAFNOSUPPORT,
                errno.EPROTONOSUPPORT,
                errno.EADDRNOTAVAIL,
            ):
                # Python may support IPv6 even when it is disabled on this host.
                continue
            raise
    return True


def wait_for_port_free(port: int, timeout: int = 0, waiting_app_name: str = "App") -> bool:
    """Wait for a TCP port to become available for binding, with a bounded timeout.

    Probe IPv4 and supported IPv6 sockets, retrying at most every three seconds.
    Process details are optional diagnostics when the port remains unavailable.

    Args:
        port: The network port number to check.
        timeout: Maximum seconds to wait (0 means check once without waiting).
        waiting_app_name: Name of the application waiting for the port.

    Returns:
        True if the port can be bound, False if it remains unavailable.

    Raises:
        ValueError: If the port number or timeout is invalid.
        OSError: If socket probing fails for reasons other than an unavailable port
            or unsupported IPv6.
    """
    if not 0 <= port <= 65535:
        raise ValueError(f"Invalid port number: {port}")
    if timeout < 0:
        raise ValueError(f"Invalid timeout: {timeout}")

    deadline = time.monotonic() + timeout
    while True:
        if _is_port_available(port):
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        logger.info(f"{waiting_app_name} waiting for port {port} to become free...")
        time.sleep(min(3.0, remaining))

    logger.warning(f"{waiting_app_name} port {port} still in use after waiting {timeout} seconds.")
    try:
        connections = psutil.net_connections(kind="inet")
    except (psutil.Error, OSError) as error:
        logger.debug(f"Process details unavailable for port {port}: {error}")
        return False

    seen_pids: set[int] = set()
    for conn in connections:
        if not conn.laddr or conn.laddr.port != port or conn.pid is None or conn.pid in seen_pids:
            continue
        seen_pids.add(conn.pid)
        try:
            process = psutil.Process(conn.pid)
            cmdline = process.cmdline()
        except (psutil.Error, OSError):
            # Protected or disappearing processes do not change the port result.
            continue
        logger.warning(f"Process using port - PID: {conn.pid}, Command: {' '.join(cmdline)}")
    return False


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
    if pwd is None or grp is None or not hasattr(os, "geteuid"):
        if run_as_user is not None:
            logger.error(f"Privilege switching is not supported on `{sys.platform}`.")
            return False
        return True

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
    if pwd is None or not hasattr(os, "geteuid") or not hasattr(os, "chown"):
        logger.debug(f"Skipping data directory ownership fix on `{sys.platform}`.")
        return

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

    host: str = Field(
        default=get_default_host(),
        json_schema_extra={
            "description": "EOS server IP address. Defaults to 127.0.0.1.",
            "examples": ["127.0.0.1", "localhost"],
        },
    )
    port: int = Field(
        default=get_default_port(),
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
    eosdash_host: str = Field(
        default=get_default_host(),
        json_schema_extra={
            "description": "EOSdash server IP address. Defaults to EOS server IP address.",
            "examples": ["127.0.0.1", "localhost"],
        },
    )
    eosdash_port: int = Field(
        default=get_default_port() + 1,
        json_schema_extra={
            "description": "EOSdash server IP port number. Defaults to 8504.",
            "examples": [
                8504,
            ],
        },
    )
    eosdash_supervise_interval_sec: int = Field(
        default=10,
        json_schema_extra={
            "description": "Supervision interval for EOS server to supervise EOSdash [seconds].",
            "examples": [
                10,
            ],
        },
    )
    run_as_user: Optional[str] = Field(
        default=None,
        json_schema_extra={
            "description": (
                "The name of the target user to switch to. If ``None`` (default), the current "
                "effective user is used and no privilege change is attempted."
            ),
            "examples": [
                None,
                "user",
            ],
        },
    )
    reload: Optional[bool] = Field(
        default=False,
        json_schema_extra={
            "description": (
                "Enable server auto-reload for debugging or development. Default is False. "
                "Monitors the package directory for changes and reloads the server."
            ),
            "examples": [
                True,
            ],
        },
    )

    @field_validator("host", "eosdash_host", mode="before")
    def validate_server_host(cls, value: Optional[str]) -> Optional[str]:
        if isinstance(value, str):
            value = validate_ip_or_hostname(value)
        return value

    @field_validator("port", mode="before")
    def validate_server_port(cls, value: Any) -> int:
        default_port = get_default_port()
        if value is None:
            return default_port
        value = int(value)
        if is_home_assistant_addon() and value != default_port:
            raise ValueError(
                f"Server port number `{default_port}` for Home Assistant add-on can not be changed."
            )
        if not (1024 <= value <= 49151):
            raise ValueError("Server port number must be between 1024 and 49151.")
        return value

    @field_validator("eosdash_port", mode="before")
    def validate_eosdash_port(cls, value: Any) -> int:
        default_port = get_default_port() + 1
        if value is None:
            return default_port
        value = int(value)
        if is_home_assistant_addon() and value != default_port:
            raise ValueError(
                f"EOSdash port number `{default_port}` for Home Assistant add-on can not be changed."
            )
        if not (1024 <= value <= 49151):
            raise ValueError("EOSdash port number must be between 1024 and 49151.")
        return value

    @field_validator("run_as_user")
    def validate_user(cls, value: Optional[str]) -> Optional[str]:
        if value is not None:
            if pwd is None:
                raise ValueError(f"User privilege switching is not supported on `{sys.platform}`.")
            # Resolve target user info
            try:
                pw_record = pwd.getpwnam(value)
            except KeyError:
                raise ValueError(f"User '{value}' does not exist.")
        return value
