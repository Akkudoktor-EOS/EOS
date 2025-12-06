# syntax=docker/dockerfile:1.7
# Dockerfile

# Set base image first
ARG PYTHON_VERSION=3.13.9
FROM python:${PYTHON_VERSION}-slim

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

WORKDIR ${EOS_DIR}

# Create eos user and data directories with eos user permissions
RUN adduser --system --group --no-create-home eos \
    && mkdir -p "${EOS_DATA_DIR}" \
    && chown eos "${EOS_DATA_DIR}" \
    && mkdir -p "${EOS_CACHE_DIR}" \
    && chown eos "${EOS_CACHE_DIR}" \
    && mkdir -p "${EOS_OUTPUT_DIR}" \
    && chown eos "${EOS_OUTPUT_DIR}" \
    && mkdir -p "${EOS_CONFIG_DIR}" \
    && chown eos "${EOS_CONFIG_DIR}" \
    && mkdir -p "${MPLCONFIGDIR}" \
    && chown eos "${MPLCONFIGDIR}"

# Install requirements
COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --no-cache-dir -r requirements.txt

# Copy source
COPY src/ ./src
COPY pyproject.toml .

# Create version information
COPY scripts/get_version.py ./scripts/get_version.py
RUN python scripts/get_version.py > ./version.txt
RUN rm ./scripts/get_version.py

RUN echo "Building Akkudoktor-EOS with Python $PYTHON_VERSION"

# Install akkudoktoreos package in editable form (-e)
# pyproject-toml will read the version from version.txt
RUN pip install --no-cache-dir -e .

ENTRYPOINT []

EXPOSE 8503
EXPOSE 8504

# Ensure EOS and EOSdash bind to 0.0.0.0
# EOS is started with root provileges. EOS will drop root proviledges and switch to user eos.
CMD ["python", "-m", "akkudoktoreos.server.eos", "--host", "0.0.0.0", "--run_as_user", "eos"]

# Persistent data
# (Not recognized by home assistant add-on management, but there we have /data anyway)
VOLUME ["${EOS_DATA_DIR}"]
