# syntax=docker/dockerfile:1.7
# Dockerfile

# Support both Home Assistant builds and standalone builds.
# Only Debian based images are supported (no Alpine).
ARG BUILD_FROM
ARG PYTHON_VERSION=3.13.15

# Builder and runtime share the same base so the copied virtualenv is ABI-safe.
# If BUILD_FROM is set (Home Assistant), use it; otherwise use python-slim.
FROM ${BUILD_FROM:-python:${PYTHON_VERSION}-slim} AS builder

# uv: pinned, copied as a static binary (no extra Python packages installed).
COPY --from=ghcr.io/astral-sh/uv:0.12.7 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /opt/eos

# Build toolchain for the numpy/scipy/pandas/matplotlib stack. python3 is
# explicit because the Home Assistant base image ships without it.
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    gcc g++ gfortran \
    libopenblas-dev liblapack-dev \
    && rm -rf /var/lib/apt/lists/*

# Resolve and install dependencies from the lock file first. This layer stays
# cached as long as pyproject.toml / uv.lock are unchanged.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# Project sources and generated version (pyproject reads version from version.txt).
COPY src/ ./src
COPY scripts/get_version.py ./scripts/get_version.py
RUN python scripts/get_version.py > version.txt

# Install the project itself. Editable, because akkudoktoreos.core.version
# requires the src/akkudoktoreos layout at runtime; the runtime stage copies
# src/ back in.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev


FROM ${BUILD_FROM:-python:${PYTHON_VERSION}-slim} AS runtime

ARG BUILD_VERSION=VERSION

LABEL \
    io.hass.version="${BUILD_VERSION}" \
    io.hass.type="addon" \
    io.hass.arch="aarch64|amd64" \
    source="https://github.com/Akkudoktor-EOS/EOS" \
    org.opencontainers.image.source="https://github.com/Akkudoktor-EOS/EOS" \
    org.opencontainers.image.version="${BUILD_VERSION}" \
    org.opencontainers.image.licenses="Apache-2.0"

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

# Runtime shared libraries only (no -dev packages, no compilers). Create the eos
# user and the persistent data directories with eos ownership.
RUN apt-get update && apt-get install -y --no-install-recommends \
    adduser python3 libopenblas0 liblapack3 \
    && adduser --system --group --no-create-home eos \
    && mkdir -p "${EOS_DATA_DIR}" "${EOS_CACHE_DIR}" "${EOS_OUTPUT_DIR}" "${EOS_CONFIG_DIR}" "${MPLCONFIGDIR}" \
    && chown -R eos:eos "${EOS_DATA_DIR}" \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/venv /opt/venv
# Editable install: the venv only points at the source tree, so it must be present.
COPY src/ ./src

ENTRYPOINT []

EXPOSE 8503
EXPOSE 8504

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8503/v1/health')" || exit 1

# Ensure EOS and EOSdash bind to 0.0.0.0
# EOS is started with root privileges. EOS will drop root privileges and switch to user eos.
CMD ["python", "-m", "akkudoktoreos.server.eos", "--host", "0.0.0.0", "--run_as_user", "eos"]

# Persistent data
# (Not recognized by home assistant add-on management, but there we have /data anyway)
VOLUME ["${EOS_DATA_DIR}"]
