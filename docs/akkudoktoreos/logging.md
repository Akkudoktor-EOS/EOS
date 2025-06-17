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

        .venv\Scripts\python src/akkudoktoreos/server/eos.py --log-level DEBUG

  .. tab:: Linux

     .. code-block:: bash

        .venv/bin/python src/akkudoktoreos/server/eos.py --log-level DEBUG

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

:::{admonition} Note
:class: note
The `/v1/logging/log` endpoint needs file logging to be enabled. Otherwise old or no logging
information is provided.
:::
