"""Utility for configuring Loguru loggers."""

import json
import logging as pylogging
import os
import re
import sys
from pathlib import Path
from types import FrameType
from typing import Any, BinaryIO, Iterator, List, Optional

from loguru import logger

from akkudoktoreos.core.logabc import LOGGING_LEVELS
from akkudoktoreos.utils.datetimeutil import to_datetime


class InterceptHandler(pylogging.Handler):
    """A logging handler that redirects standard Python logging messages to Loguru.

    This handler ensures consistency between the `logging` module and Loguru by intercepting
    logs sent to the standard logging system and re-emitting them through Loguru with proper
    formatting and context (including exception info and call depth).

    Attributes:
        loglevel_mapping (dict): Mapping from standard logging levels to Loguru level names.
    """

    loglevel_mapping: dict[int, str] = {
        50: "CRITICAL",
        40: "ERROR",
        30: "WARNING",
        20: "INFO",
        10: "DEBUG",
        5: "TRACE",
        0: "NOTSET",
    }

    def emit(self, record: pylogging.LogRecord) -> None:
        """Emits a logging record by forwarding it to Loguru with preserved metadata.

        Args:
            record (logging.LogRecord): A record object containing log message and metadata.
        """
        # Skip DEBUG logs from matplotlib - very noisy
        if record.name.startswith("matplotlib") and record.levelno <= pylogging.DEBUG:
            return

        try:
            level = logger.level(record.levelname).name
        except AttributeError:
            level = self.loglevel_mapping.get(record.levelno, "INFO")

        frame: Optional[FrameType] = pylogging.currentframe()
        depth: int = 2
        while frame and frame.f_code.co_filename == pylogging.__file__:
            frame = frame.f_back
            depth += 1

        log = logger.bind(request_id="app")
        log.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())


console_handler_id = None
file_handler_id = None


def logging_track_config(config_eos: Any, path: str, old_value: Any, value: Any) -> None:
    """Track logging config changes."""
    global console_handler_id, file_handler_id

    if not path.startswith("logging"):
        raise ValueError(f"Logging shall not track '{path}'")

    if not config_eos.logging.console_level:
        # No value given - check environment value - may also be None
        config_eos.logging.console_level = os.getenv("EOS_LOGGING__LEVEL")
    if not config_eos.logging.file_level:
        # No value given - check environment value - may also be None
        config_eos.logging.file_level = os.getenv("EOS_LOGGING__LEVEL")

    # Remove handlers
    if console_handler_id:
        try:
            logger.remove(console_handler_id)
        except Exception as e:
            logger.debug("Exception on logger.remove: {}", e, exc_info=True)
        console_handler_id = None
    if file_handler_id:
        try:
            logger.remove(file_handler_id)
        except Exception as e:
            logger.debug("Exception on logger.remove: {}", e, exc_info=True)
        file_handler_id = None

    # Create handlers with new configuration
    # Always add console handler
    if config_eos.logging.console_level not in LOGGING_LEVELS:
        logger.error(
            f"Invalid console log level '{config_eos.logging.console_level} - forced to INFO'."
        )
        config_eos.logging.console_level = "INFO"

    console_handler_id = logger.add(
        sys.stderr,
        enqueue=True,
        backtrace=True,
        level=config_eos.logging.console_level,
        # format=_console_format
    )

    # Add file handler
    if config_eos.logging.file_level and config_eos.logging.file_path:
        if config_eos.logging.file_level not in LOGGING_LEVELS:
            logger.error(
                f"Invalid file log level '{config_eos.logging.console_level}' - forced to INFO."
            )
            config_eos.logging.file_level = "INFO"

        file_handler_id = logger.add(
            sink=config_eos.logging.file_path,
            rotation="100 MB",
            retention="3 days",
            enqueue=True,
            backtrace=True,
            level=config_eos.logging.file_level,
            serialize=True,  # JSON dict formatting
            # format=_file_format
        )

    # Redirect standard logging to Loguru
    pylogging.basicConfig(handlers=[InterceptHandler()], level=0)
    # Redirect uvicorn and fastapi logging to Loguru
    pylogging.getLogger("uvicorn.access").handlers = [InterceptHandler()]
    for pylogger_name in ["uvicorn", "uvicorn.error", "fastapi"]:
        pylogger = pylogging.getLogger(pylogger_name)
        pylogger.handlers = [InterceptHandler()]
        pylogger.propagate = False

    logger.info(
        f"Logger reconfigured - console: {config_eos.logging.console_level}, file: {config_eos.logging.file_level}."
    )


def read_file_log(
    log_path: Path,
    limit: int = 100,
    level: Optional[str] = None,
    contains: Optional[str] = None,
    regex: Optional[str] = None,
    from_time: Optional[str] = None,
    to_time: Optional[str] = None,
    tail: bool = False,
    timezone: Optional[str] = None,
) -> List[dict]:
    """Read and filter structured log entries from a JSON-formatted log file.

    The file is the EOS file sink: one loguru record per line, serialized as
    ``{"text": ..., "record": {...}}``. The filters read level, message and time from ``record``.

    Args:
        log_path (Path): Path to the JSON-formatted log file.
        limit (int, optional): Maximum number of log entries to return. Defaults to 100.
        level (Optional[str], optional): Filter logs by log level (e.g., "INFO", "ERROR"). Defaults to None.
        contains (Optional[str], optional): Filter logs that contain this substring in their message. Case-insensitive. Defaults to None.
        regex (Optional[str], optional): Filter logs whose message matches this regular expression. Defaults to None.
        from_time (Optional[str], optional): ISO 8601 datetime string to filter logs not earlier than this time. Defaults to None.
        to_time (Optional[str], optional): ISO 8601 datetime string to filter logs not later than this time. Defaults to None.
        tail (bool, optional): If True, return the newest `limit` matching entries (like `tail -n`), in
            file order. Defaults to False.
        timezone (Optional[str], optional): Time zone for `from_time` and `to_time` strings without a UTC
            offset. Defaults to UTC.

    Returns:
        List[dict]: A list of filtered log entries as dictionaries.

    Raises:
        FileNotFoundError: If the log file does not exist.
        ValueError: If the datetime strings or the regular expression are invalid.
        Exception: For other unforeseen I/O or parsing errors.
    """
    if not log_path.exists():
        raise FileNotFoundError("Log file not found")

    zone = timezone or "UTC"
    try:
        # Compare POSIX timestamps: cheap per record, and independent of time zones.
        from_ts = to_datetime(from_time, in_timezone=zone).timestamp() if from_time else None
        to_ts = to_datetime(to_time, in_timezone=zone).timestamp() if to_time else None
    except Exception as e:
        raise ValueError(f"Invalid date/time format: {e}") from e

    try:
        regex_pattern = re.compile(regex) if regex else None
    except re.error as e:
        raise ValueError(f"Invalid regular expression: {e}") from e

    def matches_filters(log: dict) -> bool:
        # The file sink uses ``serialize=True``: every line is ``{"text": ..., "record": {...}}``.
        rec = log.get("record", log)
        message = rec.get("message", "")
        if level and rec.get("level", {}).get("name") != level.upper():
            return False
        if contains and contains.lower() not in message.lower():
            return False
        if regex_pattern and not regex_pattern.search(message):
            return False
        if from_ts is not None or to_ts is not None:
            record_time = rec.get("time")
            try:
                # loguru serializes the time as {"repr": ..., "timestamp": ...}
                if isinstance(record_time, dict):
                    log_ts = float(record_time["timestamp"])
                else:
                    log_ts = to_datetime(record_time, in_timezone=zone).timestamp()
            except Exception:
                return False
            if from_ts is not None and log_ts < from_ts:
                return False
            if to_ts is not None and log_ts > to_ts:
                return False
        return True

    def parse(line: bytes | str) -> Optional[dict]:
        if not line.strip():
            return None
        try:
            return json.loads(line)
        except json.JSONDecodeError:  # e.g. a line still being written
            return None

    matched_logs: list[dict] = []
    if tail:
        # Newest first, stop as soon as `limit` matches are found, return in file order.
        with log_path.open("rb") as f:
            for raw in _iter_lines_reverse(f):
                log = parse(raw)
                if log is not None and matches_filters(log):
                    matched_logs.append(log)
                    if len(matched_logs) >= limit:
                        break
        matched_logs.reverse()
    else:
        with log_path.open("r", encoding="utf-8", errors="replace") as f_txt:
            for line in f_txt:  # stream, don't load a 100 MB file into memory
                log = parse(line)
                if log is not None and matches_filters(log):
                    matched_logs.append(log)
                    if len(matched_logs) >= limit:
                        break

    return matched_logs


def _iter_lines_reverse(f: BinaryIO, block_size: int = 64 * 1024) -> Iterator[bytes]:
    r"""Yield the lines of a binary file from the last to the first, reading block-wise.

    Splitting on ``b"\n"`` is safe for UTF-8: the byte never occurs inside a multi-byte sequence.
    """
    f.seek(0, os.SEEK_END)
    pos = f.tell()
    rest = b""
    while pos > 0:
        size = min(block_size, pos)
        pos -= size
        f.seek(pos)
        parts = (f.read(size) + rest).split(b"\n")
        rest = parts.pop(0)  # may be incomplete, keep for the next block
        for part in reversed(parts):
            if part.strip():
                yield part
    if rest.strip():
        yield rest
