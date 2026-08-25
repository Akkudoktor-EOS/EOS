# syntax=docker/dockerfile:1.7
# Dockerfile

# Support both Home Assistant builds and standalone builds
# Only Debian based images are supported (no Alpine)
ARG BUILD_FROM
ARG PYTHON_VERSION=3.13.15

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

# EOS_SERVER__EOSDASH_SESSKEY is deliberately NOT set here.
# Baking a session signing key into a public image lets anyone forge EOSdash
# session cookies. Unset means EOSdash generates a random key per container.
# Set it via the environment only if sessions must survive a restart.

# Set environment variables to reduce threading needs
ENV OPENBLAS_NUM_THREADS=1
ENV OMP_NUM_THREADS=1
ENV MKL_NUM_THREADS=1

# pip behaviour (quiet output, no version nag, no in-image wheel cache)
ENV PIP_PROGRESS_BAR=off
ENV PIP_NO_COLOR=1
ENV PIP_DISABLE_PIP_VERSION_CHECK=1
ENV PIP_ROOT_USER_ACTION=ignore

# Generic environment
ENV LANG=C.UTF-8
ENV PYTHONUNBUFFERED=1
ENV VENV_PATH=/opt/venv
# - Use .venv for python commands
ENV PATH="$VENV_PATH/bin:$PATH"

WORKDIR ${EOS_DIR}

# Create eos user and data directories with eos user permissions.
# useradd/groupadd ship with the base image, so no apt install is needed here.
RUN groupadd --system eos \
    && useradd --system --gid eos --no-create-home --shell /usr/sbin/nologin eos \
    && mkdir -p "${EOS_CACHE_DIR}" "${EOS_OUTPUT_DIR}" "${EOS_CONFIG_DIR}" "${MPLCONFIGDIR}" \
    && chown -R eos:eos "${EOS_DATA_DIR}"

# - Copy project metadata first (better Docker layer caching)
COPY pyproject.toml .

# Install EOS/ EOSdash
# - Copy source (needed at runtime too: the version is derived from the source tree)
COPY src/ ./src

# Build and install in a single layer so the toolchain never reaches the final image.
# - Runtime BLAS/LAPACK/OpenMP libraries are installed as manual packages and survive
#   the --auto-remove of the compilers and the -dev headers.
# - scripts/get_version.py is bind mounted: it is a build-time helper and must not
#   linger in any layer.
# - The pip cache is a BuildKit cache mount, so rebuilds are fast without shipping it.
RUN --mount=type=bind,source=scripts/get_version.py,target=${EOS_DIR}/scripts/get_version.py \
    --mount=type=cache,target=/root/.cache/pip,sharing=locked \
    apt-get update \
    && apt-get install -y --no-install-recommends \
        libopenblas0 liblapack3 libgomp1 \
    && apt-get install -y --no-install-recommends \
        gcc g++ gfortran \
        libopenblas-dev liblapack-dev \
    # python-slim already provides venv; Home Assistant base images do not.
    && { python3 -m venv "${VENV_PATH}" \
        || { apt-get install -y --no-install-recommends python3-venv \
             && python3 -m venv "${VENV_PATH}"; }; } \
    && pip install --upgrade pip setuptools \
    # - Create version information, pyproject.toml reads the version from version.txt
    && python scripts/get_version.py > ./version.txt \
    # - Install akkudoktoreos package in editable form (-e)
    && pip install -e . \
    && apt-get purge -y --auto-remove \
        gcc g++ gfortran \
        libopenblas-dev liblapack-dev \
    && rm -rf /var/lib/apt/lists/* \
    # EOS never needs setuid/setgid binaries. Stripping them keeps the unprivileged
    # eos user from regaining root after the server drops privileges.
    && find / -xdev \( -perm -4000 -o -perm -2000 \) -type f -exec chmod a-s {} +

ENTRYPOINT []

EXPOSE 8504
EXPOSE 8503

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8503/v1/health').read()"

# Ensure EOS and EOSdash bind to 0.0.0.0
# EOS is started with root privileges so it can fix ownership of the mounted data
# directory. EOS drops root privileges and switches to user eos before serving.
CMD ["python", "-m", "akkudoktoreos.server.eos", "--host", "0.0.0.0", "--run_as_user", "eos"]

# Persistent data
# (Not recognized by home assistant add-on management, but there we have /data anyway)
VOLUME ["${EOS_DATA_DIR}"]
