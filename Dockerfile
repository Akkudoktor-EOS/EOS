# syntax=docker/dockerfile:1.7
# Dockerfile

# Support both Home Assistant builds and standalone builds
# Only Debian based images are supported (no Alpine)
ARG BUILD_FROM
ARG PYTHON_VERSION=3.13.9

# If BUILD_FROM is set (Home Assistant), use it; otherwise use python-slim
FROM ${BUILD_FROM:-python:${PYTHON_VERSION}-slim}

LABEL \
    io.hass.version="VERSION" \
    io.hass.type="addon" \
    io.hass.arch="aarch64|amd64" \
    source="https://github.com/Akkudoktor-EOS/EOS"

ENV EOS_DIR="/opt/eos"
# Create persistent data directory similar to home assistant add-on
# - EOS_DATA_DIR: Persistent data directory
# - MPLCONFIGDIR: user customizations to Mathplotlib
ENV EOS_DATA_DIR="/data"
ENV EOS_CACHE_DIR="${EOS_DATA_DIR}/cache"
ENV EOS_OUTPUT_DIR="${EOS_DATA_DIR}/output"
ENV EOS_CONFIG_DIR="${EOS_DATA_DIR}/config"
ENV MPLCONFIGDIR="${EOS_DATA_DIR}/mplconfigdir"

# Overwrite when starting the container in a production environment
ENV EOS_SERVER__EOSDASH_SESSKEY=s3cr3t

# Set environment variables to reduce threading needs
ENV OPENBLAS_NUM_THREADS=1
ENV OMP_NUM_THREADS=1
ENV MKL_NUM_THREADS=1
ENV PIP_PROGRESS_BAR=off
ENV PIP_NO_COLOR=1

# Generic environment
ENV LANG=C.UTF-8
ENV VENV_PATH=/opt/venv
# - Use .venv for python commands
ENV PATH="$VENV_PATH/bin:$PATH"

WORKDIR ${EOS_DIR}

# Create eos user and data directories with eos user permissions
RUN apt-get update && apt-get install -y --no-install-recommends adduser \
    && adduser --system --group --no-create-home eos \
    && mkdir -p "${EOS_DATA_DIR}" \
    && chown -R eos:eos "${EOS_DATA_DIR}" \
    && mkdir -p "${EOS_CACHE_DIR}" "${EOS_OUTPUT_DIR}" "${EOS_CONFIG_DIR}" "${MPLCONFIGDIR}" \
    && chown -R eos:eos "${EOS_CACHE_DIR}" "${EOS_OUTPUT_DIR}" "${EOS_CONFIG_DIR}" "${MPLCONFIGDIR}"

# Install build dependencies (Debian)
# - System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3-venv \
    gcc g++ gfortran \
    libopenblas-dev liblapack-dev \
    && rm -rf /var/lib/apt/lists/*

# - Copy project metadata first (better Docker layer caching)
COPY pyproject.toml .

# - Create venv
RUN python3 -m venv ${VENV_PATH}

# - Upgrade pip inside venv
RUN pip install --upgrade pip setuptools wheel

# Install EOS/ EOSdash
# - Copy source
COPY src/ ./src

# - Create version information
COPY scripts/get_version.py ./scripts/get_version.py
RUN python scripts/get_version.py > ./version.txt
RUN rm ./scripts/get_version.py

RUN echo "Building Akkudoktor-EOS with Python $PYTHON_VERSION"

# - Install akkudoktoreos package in editable form (-e)
# - pyproject-toml will read the version from version.txt
RUN pip install --no-cache-dir -e .

ENTRYPOINT []

EXPOSE 8504
EXPOSE 8503

# Ensure EOS and EOSdash bind to 0.0.0.0
# EOS is started with root provileges. EOS will drop root proviledges and switch to user eos.
CMD ["python", "-m", "akkudoktoreos.server.eos", "--host", "0.0.0.0", "--run_as_user", "eos"]

# Persistent data
# (Not recognized by home assistant add-on management, but there we have /data anyway)
VOLUME ["${EOS_DATA_DIR}"]
