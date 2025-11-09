% SPDX-License-Identifier: Apache-2.0
(install-page)=

# Installation Guide

This guide provides different methods to install AkkudoktorEOS:

- Installation from Source (GitHub)
- Installation from Release Package (GitHub)
- Installation with Docker (DockerHub)
- Installation with Docker (docker-compose)

Choose the method that best suits your needs.

:::{admonition} Tip
:class: Note
If you need to update instead, see the [Update Guideline](update-page). For reverting to a previous
release see the [Revert Guideline](revert-page).
:::

## Installation Prerequisites

Before installing, ensure you have the following:

### For Source / Release Installation

- Python 3.10 or higher
- pip
- Git (only for source)
- Tar/Zip (for release package)

### For Docker Installation

- Docker Engine 20.10 or higher
- Docker Compose (optional, recommended)

## Installation from Source (GitHub) (M1)

Recommended for developers or users wanting the latest updates.

### 1) Clone the Repository (M1)

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

### 2) Create a Virtual Environment and install dependencies (M1)

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

### 3) Run EOS (M1)

```{eval-rst}
.. tabs::

  .. tab:: Windows

     .. code-block:: powershell

        .venv\Scripts\python -m akkudoktoreos.server.eos

  .. tab:: Linux

     .. code-block:: bash

        .venv/bin/python -m akkudoktoreos.server.eos

```

EOS is now available at:

- API: [http://localhost:8503/docs](http://localhost:8503/docs)
- EOSdash: [http://localhost:8504](http://localhost:8504)

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

### 4) Configure EOS (M1)

Use EOSdash at [http://localhost:8504](http://localhost:8504) to configure EOS.

## Installation from Release Package (GitHub) (M2)

This method is recommended for users who want a stable, tested version.

### 1) Download the Latest Release (M2)

Visit the [Releases page](https://github.com/Akkudoktor-EOS/EOS/tags) and download the latest
release package (e.g., `akkudoktoreos-v0.2.0.tar.gz` or `akkudoktoreos-v0.2.0.zip`).

### 2) Extract the Package (M2)

```bash
tar -xzf akkudoktoreos-v0.2.0.tar.gz  # For .tar.gz
# or
unzip akkudoktoreos-v0.2.0.zip  # For .zip

cd akkudoktoreos-v0.2.0
```

### 3) Create a virtual environment and run and configure EOS (M2)

Follow Step 2), 3) and 4) of method M1. Start at
`2) Create a Virtual Environment and install dependencies`

### 4) Update the source code (M2)

To extract a new release to a new directory just proceed with method M2 step 1) for the new release.

You may remove the old release directory afterwards.

## Installation with Docker (DockerHub) (M3)

This method is recommended for easy deployment and containerized environments.

### 1) Pull the Docker Image (M3)

```bash
docker pull akkudoktor/eos:latest
```

For a specific version:

```bash
docker pull akkudoktor/eos:v<version>
```

### 2) Run the Container (M3)

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

### 3) Verify the Container is Running (M3)

```bash
docker ps
docker logs akkudoktoreos
```

EOS should now be accessible at [http://localhost:8503/docs](http://localhost:8503/docs) and EOSdash
should be available at [http://localhost:8504](http://localhost:8504).

### 4) Configure EOS (M3)

Use EOSdash at [http://localhost:8504](http://localhost:8504) to configure EOS.

## Installation with Docker (docker-compose) (M4)

### 1) Get the akkudoktoreos source code (M4)

You may use either method M1 or method M2 to get the source code.

### 2) Build and run the container (M4)

```{eval-rst}
.. tabs::

  .. tab:: Windows

     .. code-block:: powershell

        docker compose up --build

  .. tab:: Linux

     .. code-block:: bash

        docker compose up --build

```

### 3) Verify the Container is Running (M4)

```bash
docker ps
docker logs akkudoktoreos
```

EOS should now be accessible at [http://localhost:8503/docs](http://localhost:8503/docs) and EOSdash
should be available at [http://localhost:8504](http://localhost:8504).

### 4) Configure EOS

Use EOSdash at [http://localhost:8504](http://localhost:8504) to configure EOS.

## Helpful Docker Commands

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
