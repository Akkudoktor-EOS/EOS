..
    SPDX-License-Identifier: Apache-2.0

.. _akkudoktoreos_getting_started:

Getting Started
###############

Installation
************

`Good installation guide <https://meintechblog.de/2024/09/05/andreas-schmitz-joerg-installiert-mein-energieoptimierungssystem/>`_

The project requires Python 3.10 or newer.

Quick Start Guide
-----------------

On Linux (Ubuntu/Debian):

```bash
sudo apt install make
```

On MacOS (requires `Homebrew <https://brew.sh>`_):

```zsh
brew install make
```

Next, adjust `config.py`.
The server can then be started with `make run`. A full overview of the main shortcuts is given by `make help`.

Detailed Instructions
---------------------

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
(if using zsh, primarily for MacOS users).

If `pip install` fails to install the mariadb dependency, the following commands may help:

* Debian/Ubuntu: `sudo apt-get install -y libmariadb-dev`
* MacOS/Homebrew: `brew install mariadb-connector-c`

Followed by a renewed `pip install -r requirements.txt`.

Usage
*****

Adjust `config.py`.

To start the server:

```bash
make run
```
