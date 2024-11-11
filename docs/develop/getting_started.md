% SPDX-License-Identifier: Apache-2.0

# Getting Started

## Installation

Good installation guide:
https://meintechblog.de/2024/09/05/andreas-schmitz-joerg-installiert-mein-energieoptimierungssystem/

The project requires Python 3.10 or newer.

### Quick Start Guide

On Linux (Ubuntu/Debian):

```bash
sudo apt install make
```

On MacOS (requires [Homebrew](https://brew.sh)):

```zsh
brew install make
```

The server can then be started with `make run`. A full overview of the main shortcuts is given by `make help`.

### Detailed Instructions

All necessary dependencies can be installed via `pip`. Clone the repository and install the required packages with:

```bash
git clone https://github.com/Akkudoktor-EOS/EOS
cd EOS
```

Next, create a virtual environment. This serves to store the Python dependencies, which we will install later using `pip`:

```bash
virtualenv .venv
```

Finally, install the Python dependencies for EOS:

```bash
.venv/bin/pip install -r requirements.txt
```

To always use the Python version from the virtual environment, you should activate it before working in EOS:

```bash
source .venv/bin/activate
```

(for Bash users, the default under Linux) or

```zsh
. .venv/bin/activate
```

## Usage

### Configuration

---

This project uses a `config.json` file to manage configuration settings.

#### Default Configuration

A default configuration file `default.config.json` is provided. This file contains all the necessary configuration keys with their default values.

#### Custom Configuration

Users can specify a custom configuration directory by setting the environment variable `EOS_DIR`.

- If the directory specified by `EOS_DIR` contains an existing `config.json` file, the application will use this configuration file.
- If the `config.json` file does not exist in the specified directory, the `default.config.json` file will be copied to the directory as `config.json`.

#### Configuration Updates

If the configuration keys in the `config.json` file are missing or different from those in `default.config.json`, they will be automatically updated to match the default settings, ensuring that all required keys are present.

### Run server

To use the system, run `flask_server.py`, which starts the server:

```bash
./flask_server.py
```
