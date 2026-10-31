"""Keep secrets out of REST API responses.

The configuration holds credentials such as access tokens and API keys of forecast and
price providers or a Home Assistant long-lived token. The REST API has no
authentication, so it must never hand them out: every configuration response is
redacted, and a masked value that comes back (EOSdash export/import, a client that
writes back what it read) leaves the stored secret unchanged.

Secrets are set through environment variables, the configuration file or a PUT with
the real value.
"""

from __future__ import annotations

import re
from typing import Any

MASK = "***"

_SECRET_NAMES = {
    "token",
    "access_token",
    "api_key",
    "apikey",
    "password",
    "passwd",
    "secret",
    "dsn",
    "sesskey",
    "eosdash_sesskey",
}
_SECRET_SUFFIXES = ("_token", "_password", "_secret", "_api_key", "_apikey", "_sesskey")
_URL_PASSWORD = re.compile(
    r"^(?P<head>[a-zA-Z][a-zA-Z0-9+.-]*://[^:/@]+:)(?P<pw>[^@]*)(?P<tail>@.*)$"
)
_KV_PASSWORD = re.compile(r"(password\s*=\s*)(\S+)", re.IGNORECASE)


def is_secret_key(name: str) -> bool:
    """True when a configuration key names a secret."""
    lowered = str(name).lower()
    return lowered in _SECRET_NAMES or lowered.endswith(_SECRET_SUFFIXES)


def path_is_secret(path: str) -> bool:
    """True when any segment of a ``a/b/c`` configuration path names a secret."""
    return any(is_secret_key(part) for part in str(path).strip("/").split("/") if part)


def mask_value(key: str, value: Any) -> Any:
    """Masked form of a secret value (empty values stay empty: "not set" is no secret)."""
    if value is None or value == "":
        return value
    if str(key).lower() == "dsn" and isinstance(value, str):
        url = _URL_PASSWORD.match(value)
        if url:
            return f"{url.group('head')}{MASK}{url.group('tail')}"
        if _KV_PASSWORD.search(value):
            return _KV_PASSWORD.sub(lambda m: m.group(1) + MASK, value)
        return value  # a DSN without password
    return MASK


def redact(obj: Any) -> Any:
    """Copy of a JSON-like structure with every secret value masked."""
    if isinstance(obj, dict):
        return {
            key: (
                mask_value(key, value)
                if is_secret_key(key) and not isinstance(value, (dict, list))
                else redact(value)
            )
            for key, value in obj.items()
        }
    if isinstance(obj, list):
        return [redact(item) for item in obj]
    return obj


def is_masked(value: Any) -> bool:
    """True when a value contains the mask."""
    return isinstance(value, str) and MASK in value


def strip_masked(obj: Any) -> Any:
    """Copy without secret entries whose value is (still) masked; for config imports."""
    if isinstance(obj, dict):
        result = {}
        for key, value in obj.items():
            if is_secret_key(key) and is_masked(value):
                continue
            result[key] = strip_masked(value)
        return result
    if isinstance(obj, list):
        return [strip_masked(item) for item in obj]
    return obj
