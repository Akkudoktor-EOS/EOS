% SPDX-License-Identifier: Apache-2.0
(logging-page)=

# Logging

EOS automatically records important events and messages to help you understand what’s happening and
to troubleshoot problems.

## How Logging Works

- By default, logs are shown in your terminal (console).
- You can also save logs to a file for later review.
- Log files are rotated automatically to avoid becoming too large.

## Controlling Log Details

### 1. Command-Line Option

Set the amount of log detail shown on the console by using `--log-level` when starting EOS.

Example:

```{eval-rst}
.. tabs::

  .. tab:: Windows

     .. code-block:: powershell

        uv run python src/akkudoktoreos/server/eos.py --log-level DEBUG

  .. tab:: Linux

     .. code-block:: bash

        uv run python src/akkudoktoreos/server/eos.py --log-level DEBUG

```

Common levels:

- DEBUG (most detail)
- INFO (default)
- WARNING
- ERROR
- CRITICAL (least detail)

### 2. Configuration File

You can also set logging options in your EOS configuration file (EOS.config.json).

```Json
{
  "logging": {
    "console_level": "INFO",
    "file_level": "DEBUG"
  }
}
```

### 3. Environment Variable

You can also control the log level by setting the `EOS_LOGGING__CONSOLE_LEVEL` and the
`EOS_LOGGING__FILE_LEVEL` environment variables.

```bash
  EOS_LOGGING__CONSOLE_LEVEL="INFO"
  EOS_LOGGING__FILE_LEVEL="DEBUG"
```

## File Logging

If the `file_level` configuration is set, log records are written to a rotating log file. The log
file is in the data output directory and named `eos.log`. You may directly read the file or use
the `/v1/logging/log` endpoint to access the file log.

The endpoint returns the matching entries as written to the file, one JSON object per entry with
the formatted `text` and the loguru `record` (level, message, time and more):

- `level` returns only the entries of exactly this level, `contains` and `regex` match the
  message.
- `from_time` and `to_time` take ISO 8601 times. A time without a UTC offset is in the
  location's time zone (`general.timezone`), or in UTC when no location is set.
- `limit` caps the number of entries. Without `tail` the oldest matching entries are returned,
  with `tail=true` the newest, both in file order.
- Only the current `eos.log` is read, not the files left by log rotation.

```bash
# the newest warning
curl "http://localhost:8503/v1/logging/log?tail=true&limit=1&level=WARNING"
```

:::{admonition} Note
:class: note
The `/v1/logging/log` endpoint needs file logging to be enabled. Otherwise old or no logging
information is provided.
:::
