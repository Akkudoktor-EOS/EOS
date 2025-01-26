ARG PYTHON_VERSION=3.12.8
ARG BASE_IMAGE=python
ARG IMAGE_SUFFIX=-slim
FROM ${BASE_IMAGE}:${PYTHON_VERSION}${IMAGE_SUFFIX} AS base

LABEL source="https://github.com/Akkudoktor-EOS/EOS"

ENV MPLCONFIGDIR="/tmp/mplconfigdir"
ENV EOS_DIR="/opt/eos"
ENV EOS_CACHE_DIR="${EOS_DIR}/cache"
ENV EOS_OUTPUT_DIR="${EOS_DIR}/output"
ENV EOS_CONFIG_DIR="${EOS_DIR}/config"

WORKDIR ${EOS_DIR}

# Use useradd over adduser to support both debian:x-slim and python:x-slim base images
RUN useradd --system --no-create-home --shell /usr/sbin/nologin eos \
    && mkdir -p "${MPLCONFIGDIR}" \
    && chown eos "${MPLCONFIGDIR}" \
    && mkdir -p "${EOS_CACHE_DIR}" \
    && chown eos "${EOS_CACHE_DIR}" \
    && mkdir -p "${EOS_OUTPUT_DIR}" \
    && chown eos "${EOS_OUTPUT_DIR}" \
    && mkdir -p "${EOS_CONFIG_DIR}" \
    && chown eos "${EOS_CONFIG_DIR}"

ARG APT_PACKAGES
ENV APT_PACKAGES="${APT_PACKAGES}"
RUN --mount=type=cache,sharing=locked,target=/var/lib/apt/lists \
    --mount=type=cache,sharing=locked,target=/var/cache/apt \
    rm /etc/apt/apt.conf.d/docker-clean; \
    if [ -n "${APT_PACKAGES}" ]; then \
        apt-get update \
        && apt-get install -y --no-install-recommends ${APT_PACKAGES}; \
    fi

FROM base AS build
ARG APT_BUILD_PACKAGES
ENV APT_BUILD_PACKAGES="${APT_BUILD_PACKAGES}"
RUN --mount=type=cache,sharing=locked,target=/var/lib/apt/lists \
    --mount=type=cache,sharing=locked,target=/var/cache/apt \
    rm /etc/apt/apt.conf.d/docker-clean; \
    if [ -n "${APT_BUILD_PACKAGES}" ]; then \
        apt-get update \
        && apt-get install -y --no-install-recommends ${APT_BUILD_PACKAGES}; \
    fi

ARG RUSTUP_INSTALL
ENV RUSTUP_INSTALL="${RUSTUP_INSTALL}"
ENV RUSTUP_HOME=/opt/rust
ENV CARGO_HOME=/opt/rust
ENV PATH="$RUSTUP_HOME/bin:$PATH"
ARG PIP_EXTRA_INDEX_URL
ENV PIP_EXTRA_INDEX_URL="${PIP_EXTRA_INDEX_URL}"
RUN --mount=type=cache,target=/root/.cache/pip \
    --mount=type=tmpfs,target=/root/.cargo \
    dpkgArch=$(dpkg --print-architecture) \
    && if [ -n "${RUSTUP_INSTALL}" ]; then \
        case "$dpkgArch" in \
            # armv6
            armel) \
                curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal --target arm-unknown-linux-gnueabi --no-modify-path \
                ;; \
            *) \
                curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal --no-modify-path \
                ;; \
        esac \
        && rustc --version \
        && cargo --version; \
    fi \
    # Install 32bit fix for pendulum, can be removed after next pendulum release (> 3.0.0)
    && case "$dpkgArch" in \
        # armv7/armv6
        armhf|armel) \
            git clone https://github.com/python-pendulum/pendulum.git \
            && git -C pendulum checkout -b 3.0.0 3.0.0 \
            # Apply 32bit patch
            && git -C pendulum -c user.name=ci -c user.email=ci@github.com cherry-pick b84b97625cdea00f8ab150b8b35aa5ccaaf36948 \
            && cd pendulum \
            # Use pip3 over pip to support both debian:x and python:x base images
            && pip3 install maturin \
            && maturin build --release --out dist \
            && pip3 install dist/*.whl --break-system-packages \
            && cd - \
            ;; \
    esac


COPY requirements.txt .

# Use tmpfs for cargo due to qemu (multiarch) limitations
RUN --mount=type=cache,target=/root/.cache/pip \
    --mount=type=tmpfs,target=/root/.cargo \
    # Use pip3 over pip to support both debian:x and python:x base images
    pip3 install -r requirements.txt --break-system-packages

FROM base AS final
# Copy all python dependencies previously installed or built to the final stage.
COPY --from=build /usr/local/ /usr/local/
COPY --from=build /opt/eos/requirements.txt .

COPY pyproject.toml .
RUN --mount=type=cache,target=/root/.cache/pip \
    # Use pip3 over pip to support both debian:x and python:x base images
    mkdir -p src && pip3 install -e . --break-system-packages

COPY src src

USER eos
ENTRYPOINT []

EXPOSE 8503
EXPOSE 8504

# Use python3 over python to support both debian:x and python:x base images
CMD ["python3", "src/akkudoktoreos/server/eos.py", "--host", "0.0.0.0"]

VOLUME ["${MPLCONFIGDIR}", "${EOS_CACHE_DIR}", "${EOS_OUTPUT_DIR}", "${EOS_CONFIG_DIR}"]
