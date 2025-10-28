% SPDX-License-Identifier: Apache-2.0
(install-page)=

# Installation Guide

This guide provides four different methods to install AkkudoktorEOS. Choose the method that best
suits your needs.

## Installation Prerequisites

Before installing, ensure you have the following:

- **For Source/Release Installation:**
  - Python 3.10 or higher
  - pip (Python package manager)
  - Git (for source installation)
  - Tar/Zip (for release package installation)

- **For Docker Installation:**
  - Docker Engine 20.10 or higher
  - Docker Compose (optional, but recommended)

## Method 1: Installation from Source (GitHub)

This method is recommended for developers or users who want the latest features.

### M1-Step 1: Clone the Repository

```{eval-rst}
.. tabs::

  .. tab:: Windows

     .. code-block:: powershell

        git clone https://github.com/Akkudoktor-EOS/EOS.git
        cd EOS

  .. tab:: Linux

     .. code-block:: bash

        git clone https://github.com/Akkudoktor-EOS/EOS.git
        cd EOS
```

### M1-Step 2: Create a Virtual Environment and install dependencies

```{eval-rst}
.. tabs::

  .. tab:: Windows

     .. code-block:: powershell

        python -m venv .venv
        .venv\Scripts\pip install -r requirements.txt
        .venv\Scripts\pip install -e .

  .. tab:: Linux

     .. code-block:: bash

        python -m venv .venv
        .venv/bin/pip install -r requirements.txt
        .venv/bin/pip install -e .

```

### M1-Step 3: Run EOS

```{eval-rst}
.. tabs::

  .. tab:: Windows

     .. code-block:: powershell

        .venv\Scripts\python -m akkudoktoreos.server.eos

  .. tab:: Linux

     .. code-block:: bash

        .venv/bin/python -m akkudoktoreos.server.eos

```

EOS should now be accessible at [http://localhost:8503/docs](http://localhost:8503/docs) and EOSdash
should be available at [http://localhost:8504](http://localhost:8504).

If you want to make EOS and EOSdash accessible from outside of your machine or container at this
stage of the installation provide appropriate IP addresses on startup.

<!-- pyml disable line-length -->
```{eval-rst}
.. tabs::

  .. tab:: Windows

     .. code-block:: powershell

        .venv\Scripts\python -m akkudoktoreos.server.eos --host 0.0.0.0 --eosdash-host 0.0.0.0

  .. tab:: Linux

     .. code-block:: bash

        .venv/bin/python -m akkudoktoreos.server.eos --host 0.0.0.0 --eosdash-host 0.0.0.0

```
<!-- pyml enable line-length -->

### M1-Step 4: Configure EOS

Use [EOSdash](http://localhost:8504) to configure EOS.

### Updating from Source

To update to the latest version:

```{eval-rst}
.. tabs::

  .. tab:: Windows

     .. code-block:: powershell

        git pull origin main
        .venv\Scripts\pip install -r requirements.txt --upgrade

  .. tab:: Linux

     .. code-block:: bash

        git pull origin main
        .venv/bin/pip install -r requirements.txt --upgrade

```

## Method 2: Installation from Release Package (GitHub)

This method is recommended for users who want a stable, tested version.

### M2-Step 1: Download the Latest Release

Visit the [Releases page](https://github.com/Akkudoktor-EOS/EOS/releases) and download the latest
release package (e.g., `akkudoktoreos-v0.1.0.tar.gz` or `akkudoktoreos-v0.1.0.zip`).

### M2-Step 2: Extract the Package

```bash
tar -xzf akkudoktoreos-v0.1.0.tar.gz  # For .tar.gz
# or
unzip akkudoktoreos-v0.1.0.zip  # For .zip

cd akkudoktoreos-v0.1.0
```

### Follow Step 2, 3 and 4 of Method 1: Installation from source

Installation from release package now needs the exact same steps 2, 3, 4 of method 1.

## Method 3: Installation with Docker (DockerHub)

This method is recommended for easy deployment and containerized environments.

### M3-Step 1: Pull the Docker Image

```bash
docker pull akkudoktor/eos:latest
```

For a specific version:

```bash
docker pull akkudoktor/eos:v0.1.0
```

### M3-Step 2: Run the Container

**Basic run:**

```bash
docker run -d \
  --name akkudoktoreos \
  -p 8503:8503 \
  -p 8504:8504 \
  -e OPENBLAS_NUM_THREADS=1 \
  -e OMP_NUM_THREADS=1 \
  -e MKL_NUM_THREADS=1 \
  -e EOS_SERVER__HOST=0.0.0.0 \
  -e EOS_SERVER__PORT=8503 \
  -e EOS_SERVER__EOSDASH_HOST=0.0.0.0 \
  -e EOS_SERVER__EOSDASH_PORT=8504 \
  --ulimit nproc=65535:65535 \
  --ulimit nofile=65535:65535 \
  --security-opt seccomp=unconfined \
  akkudoktor/eos:latest
```

### M3-Step 3: Verify the Container is Running

```bash
docker ps
docker logs akkudoktoreos
```

EOS should now be accessible at [http://localhost:8503/docs](http://localhost:8503/docs) and EOSdash
should be available at [http://localhost:8504](http://localhost:8504).

### M3-Step 4: Configure EOS

Use [EOSdash](http://localhost:8504) to configure EOS.

### Docker Management Commands

**View logs:**

```bash
docker logs -f akkudoktoreos
```

**Stop the container:**

```bash
docker stop akkudoktoreos
```

**Start the container:**

```bash
docker start akkudoktoreos
```

**Remove the container:**

```bash
docker rm -f akkudoktoreos
```

**Update to latest version:**

```bash
docker pull Akkudoktor-EOS/EOS:latest
docker stop akkudoktoreos
docker rm akkudoktoreos
# Then run the container again with the run command
```

## Method 4: Installation with Docker (docker-compose)

### M4-Step 1: Get the akkudoktoreos source code

You may use either method 1 or method 2 to get the source code.

### M4-Step 2: Build and run the container

```{eval-rst}
.. tabs::

  .. tab:: Windows

     .. code-block:: powershell

        docker compose up --build

  .. tab:: Linux

     .. code-block:: bash

        docker compose up --build

```

### M4-Step 3: Verify the Container is Running

```bash
docker ps
docker logs akkudoktoreos
```

EOS should now be accessible at [http://localhost:8503/docs](http://localhost:8503/docs) and EOSdash
should be available at [http://localhost:8504](http://localhost:8504).

### M4-Step 4: Configure EOS

Use [EOSdash](http://localhost:8504) to configure EOS.
