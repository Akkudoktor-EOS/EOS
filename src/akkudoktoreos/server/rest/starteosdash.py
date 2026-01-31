import asyncio
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, MutableMapping

from loguru import logger

from akkudoktoreos.config.config import get_config
from akkudoktoreos.server.server import (
    validate_ip_or_hostname,
    wait_for_port_free,
)

config_eos = get_config()

# Loguru to HA stdout
logger.add(sys.stdout, format="{time} | {level} | {message}", enqueue=True)


# Maximum bytes per line to log
EOSDASH_LOG_MAX_LINE_BYTES = 128 * 1024  # 128 kB safety cap

LOG_PATTERN = re.compile(
    r"""
    (?:(?P<timestamp>^\S+\s+\S+)\s*\|\s*)?                     # Optional timestamp
    (?P<level>TRACE|DEBUG|INFO|WARNING|ERROR|CRITICAL)\s*\|\s* # Log level
    (?:
        (?P<file_path>[A-Za-z0-9_\-./]+)                      # Full file path or filename
        :
        (?P<line>\d+)                                         # Line number
        \s*\|\s*
    )?
    (?:(?P<function>[A-Za-z0-9_<>-]+)\s*\|\s*)?               # Optional function name
    (?P<msg>.*)                                               # Message
    """,
    re.VERBOSE,
)

# Drop-on-overload settings
EOSDASH_LOG_QUEUE_SIZE = 50
EOSDASH_DROP_WARNING_INTERVAL = 5.0  # seconds

# The queue to handle dropping of EOSdash logs on overload
eosdash_log_queue: asyncio.Queue | None = None
eosdash_last_drop_warning: float = 0.0


async def _eosdash_log_worker() -> None:
    """Consumes queued log calls and emits them via Loguru."""
    if eosdash_log_queue is None:
        error_msg = "EOSdash log queue not initialized"
        logger.error(error_msg)
        raise RuntimeError(error_msg)

    while True:
        item = await eosdash_log_queue.get()
        if item is None:
            break  # shutdown signal

        log_fn, args = item
        try:
            log_fn(*args)
        except Exception:
            logger.exception("Error while emitting EOSdash log")


def _emit_drop_warning() -> None:
    global eosdash_last_drop_warning

    now = time.monotonic()
    if now - eosdash_last_drop_warning >= EOSDASH_DROP_WARNING_INTERVAL:
        eosdash_last_drop_warning = now
        logger.warning("EOSdash log queue full — dropping subprocess log lines")


# Loguru log message patching


def patch_loguru_record(
    record: MutableMapping[str, Any],
    *,
    file_name: str,
    file_path: str,
    line_no: int,
    function: str,
    logger_name: str = "EOSdash",
) -> None:
    """Patch a Loguru log record with subprocess-origin metadata.

    This helper mutates an existing Loguru record in-place to update selected
    metadata fields (file, line, function, and logger name) while preserving
    Loguru's internal record structure. It must be used with ``logger.patch()``
    and **must not** replace structured fields (such as ``record["file"]``)
    with plain dictionaries.

    The function is intended for forwarding log messages originating from
    subprocess stdout/stderr streams into Loguru while retaining meaningful
    source information (e.g., file path and line number).

    Args:
        record:
            The Loguru record dictionary provided to ``logger.patch()``.
        file_name:
            The source file name to assign (e.g. ``"main.py"``).
        file_path:
            The full source file path to assign
            (e.g. ``"/app/server/main.py"``).
        line_no:
            The source line number associated with the log entry.
        function:
            The function name associated with the log entry.
        logger_name:
            The logical logger name to assign to the record. Defaults to
            ``"EOSdash"``.

    Notes:
        - This function mutates the record in-place and returns ``None``.
        - Only attributes of existing structured objects are modified;
          no structured Loguru fields are replaced.
        - Replacing ``record["file"]`` or similar structured fields with a
          dictionary will cause Loguru sinks to fail.

    """
    record["file"].name = file_name
    record["file"].path = file_path
    record["line"] = line_no
    record["function"] = function
    record["name"] = logger_name


async def forward_stream(stream: asyncio.StreamReader, prefix: str = "") -> None:
    """Continuously read log lines from a subprocess and re-log them via Loguru.

    The function reads lines from an ``asyncio.StreamReader`` originating from a
    subprocess (typically the subprocess's stdout or stderr), parses the log
    metadata if present (log level, file path, line number, function), and
    forwards the log entry to Loguru. If the line cannot be parsed, it is logged
    as an ``INFO`` message with generic metadata.

    Args:
        stream (asyncio.StreamReader):
            An asynchronous stream to read from, usually ``proc.stdout`` or
            ``proc.stderr`` from ``asyncio.create_subprocess_exec``.
        prefix (str, optional):
            A string prefix added to each forwarded log line. Useful for
            distinguishing between multiple subprocess sources.
            Defaults to an empty string.

    Notes:
        - If the subprocess log line includes a file path (e.g.,
          ``/app/server/main.py:42``), both ``file.name`` and ``file.path`` will
          be set accordingly in the forwarded Loguru log entry.
        - If metadata cannot be extracted, fallback values
          (``subprocess.py`` and ``/subprocess/subprocess.py``) are used.
        - The function runs until ``stream`` reaches EOF.

    """
    buffer = bytearray()

    while True:
        try:
            chunk = await stream.readuntil(b"\n")
            buffer.extend(chunk)
            complete = True

        except asyncio.LimitOverrunError as e:
            # Read buffered data without delimiter
            chunk = await stream.readexactly(e.consumed)
            buffer.extend(chunk)
            complete = False

        except asyncio.IncompleteReadError as e:
            buffer.extend(e.partial)
            complete = False

        if not buffer:
            break  # true EOF

        # Enforce memory bound
        truncated = False
        if len(buffer) > EOSDASH_LOG_MAX_LINE_BYTES:
            buffer = buffer[:EOSDASH_LOG_MAX_LINE_BYTES]
            truncated = True

            # Drain until newline or EOF
            try:
                while True:
                    await stream.readuntil(b"\n")
            except (asyncio.LimitOverrunError, asyncio.IncompleteReadError):
                pass

        # If we don't yet have a full line, continue accumulating
        if not complete and not truncated:
            continue

        raw = buffer.decode(errors="replace").rstrip()
        if truncated:
            raw += " [TRUNCATED]"

        buffer.clear()

        match = LOG_PATTERN.search(raw)

        if match:
            data = match.groupdict()

            level = data["level"] or "INFO"
            message = data["msg"]

            # ---- Extract file path and name ----
            file_path = data["file_path"]
            if file_path:
                if "/" in file_path:
                    file_name = file_path.rsplit("/", 1)[1]
                else:
                    file_name = file_path
            else:
                file_name = "subprocess.py"
                file_path = f"/subprocess/{file_name}"

            # ---- Extract function and line ----
            func_name = data["function"] or "<subprocess>"
            line_no = int(data["line"]) if data["line"] else 1

            # ---- Patch logger with realistic metadata ----
            patched = logger.patch(
                lambda r: patch_loguru_record(
                    r,
                    file_name=file_name,
                    file_path=file_path,
                    line_no=line_no,
                    function=func_name,
                )
            )
            if eosdash_log_queue is None:
                patched.log(level, f"{prefix}{message}")
            else:
                try:
                    eosdash_log_queue.put_nowait(
                        (
                            patched.log,
                            (level, f"{prefix}{message}"),
                        )
                    )
                except asyncio.QueueFull:
                    _emit_drop_warning()

        else:
            # Fallback: unstructured log line
            file_name = "subprocess.py"
            file_path = f"/subprocess/{file_name}"

            patched = logger.patch(
                lambda r: patch_loguru_record(
                    r,
                    file_name=file_name,
                    file_path=file_path,
                    line_no=1,
                    function="<subprocess>",
                    logger_name="EOSdash",
                )
            )
            if eosdash_log_queue is None:
                patched.info(f"{prefix}{raw}")
            else:
                try:
                    eosdash_log_queue.put_nowait(
                        (
                            patched.info,
                            (f"{prefix}{raw}",),
                        )
                    )
                except asyncio.QueueFull:
                    _emit_drop_warning()


async def run_eosdash_supervisor() -> None:
    """Starts EOSdash, pipes its logs, restarts it if it crashes.

    Runs forever.
    """
    global eosdash_log_queue

    eosdash_path = Path(__file__).parent.resolve().joinpath("eosdash.py")

    while True:
        await asyncio.sleep(5)

        if not config_eos.server.startup_eosdash:
            continue

        if (
            config_eos.server.eosdash_host is None
            or config_eos.server.eosdash_port is None
            or config_eos.server.host is None
            or config_eos.server.port is None
        ):
            error_msg = (
                f"Invalid configuration for EOSdash server startup.\n"
                f"- server/eosdash_host: {config_eos.server.eosdash_host}\n"
                f"- server/eosdash_port: {config_eos.server.eosdash_port}\n"
                f"- server/host: {config_eos.server.host}\n"
                f"- server/port: {config_eos.server.port}"
            )
            logger.error(error_msg)
            continue

        # Get all the parameters
        host = str(config_eos.server.eosdash_host)
        port = config_eos.server.eosdash_port
        eos_host = str(config_eos.server.host)
        eos_port = config_eos.server.port
        access_log = True
        reload = False
        log_level = config_eos.logging.console_level if config_eos.logging.console_level else "info"

        try:
            validate_ip_or_hostname(host)
            validate_ip_or_hostname(eos_host)
        except Exception as ex:
            error_msg = f"Could not start EOSdash: {ex}"
            logger.error(error_msg)
            continue

        if eos_host != host:
            # EOSdash runs on a different server - we can not start.
            error_msg = (
                f"EOSdash server startup not possible on different hosts.\n"
                f"- server/eosdash_host: {config_eos.server.eosdash_host}\n"
                f"- server/host: {config_eos.server.host}"
            )
            logger.error(error_msg)
            continue

        # Do a one time check for port free to generate warnings if not so
        wait_for_port_free(port, timeout=0, waiting_app_name="EOSdash")

        cmd = [
            sys.executable,
            "-m",
            "akkudoktoreos.server.eosdash",
            "--host",
            str(host),
            "--port",
            str(port),
            "--eos-host",
            str(eos_host),
            "--eos-port",
            str(eos_port),
            "--log_level",
            log_level,
            "--access_log",
            str(access_log),
            "--reload",
            str(reload),
        ]
        # Set environment before any subprocess run, to keep custom config dir
        eos_dir = str(config_eos.package_root_path)
        eos_data_dir = str(config_eos.general.data_folder_path)
        eos_config_dir = str(config_eos.general.config_folder_path)
        env = os.environ.copy()
        env["EOS_DIR"] = eos_dir
        env["EOS_DATA_DIR"] = eos_data_dir
        env["EOS_CONFIG_DIR"] = eos_config_dir

        logger.info("Starting EOSdash subprocess...")

        # Start EOSdash server
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
        except FileNotFoundError:
            logger.error(
                "Failed to start EOSdash: 'python' executable '{sys.executable}' not found."
            )
            continue
        except PermissionError:
            logger.error("Failed to start EOSdash: permission denied on 'eosdash.py'.")
            continue
        except asyncio.CancelledError:
            logger.warning("EOSdash startup cancelled (shutdown?).")
            return
        except Exception as e:
            logger.exception(f"Unexpected error launching EOSdash: {e}")
            continue

        if eosdash_log_queue is None:
            # Initialize EOSdash log queue + worker once
            eosdash_log_queue = asyncio.Queue(maxsize=EOSDASH_LOG_QUEUE_SIZE)
            asyncio.create_task(_eosdash_log_worker())

        if proc.stdout is None:
            logger.error("Failed to forward EOSdash output to EOS pipe.")
        else:
            # Forward log
            asyncio.create_task(forward_stream(proc.stdout, prefix="[EOSdash] "))

        if proc.stderr is None:
            logger.error("Failed to forward EOSdash error output to EOS pipe.")
        else:
            # Forward log
            asyncio.create_task(forward_stream(proc.stderr, prefix="[EOSdash] "))

        # If we reach here, the subprocess started successfully
        logger.info("EOSdash subprocess started successfully.")

        # Wait for exit
        try:
            exit_code = await proc.wait()
            logger.error(f"EOSdash exited with code {exit_code}")

        except asyncio.CancelledError:
            logger.warning("EOSdash wait cancelled (shutdown?).")
            return

        except Exception as e:
            logger.exception(f"Error while waiting for EOSdash to terminate: {e}")

        # Restart after a delay
        logger.info("Restarting EOSdash...")
