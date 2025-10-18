# syntax=docker/dockerfile:1.7
ARG PYTHON_VERSION=3.12.7
FROM python:${PYTHON_VERSION}-slim

LABEL source="https://github.com/Akkudoktor-EOS/EOS"

ENV MPLCONFIGDIR="/tmp/mplconfigdir"
ENV EOS_DIR="/opt/eos"
ENV EOS_CACHE_DIR="${EOS_DIR}/cache"
ENV EOS_OUTPUT_DIR="${EOS_DIR}/output"
ENV EOS_CONFIG_DIR="${EOS_DIR}/config"

# Overwrite when starting the container in a production environment
ENV EOS_SERVER__EOSDASH_SESSKEY=s3cr3t

# Set environment variables to reduce threading needs
ENV OPENBLAS_NUM_THREADS=1
ENV OMP_NUM_THREADS=1
ENV MKL_NUM_THREADS=1
ENV PIP_PROGRESS_BAR=off
ENV PIP_NO_COLOR=1

WORKDIR ${EOS_DIR}

RUN adduser --system --group --no-create-home eos \
    && mkdir -p "${MPLCONFIGDIR}" \
    && chown eos "${MPLCONFIGDIR}" \
    && mkdir -p "${EOS_CACHE_DIR}" \
    && chown eos "${EOS_CACHE_DIR}" \
    && mkdir -p "${EOS_OUTPUT_DIR}" \
    && chown eos "${EOS_OUTPUT_DIR}" \
    && mkdir -p "${EOS_CONFIG_DIR}" \
    && chown eos "${EOS_CONFIG_DIR}"

COPY requirements.txt .

RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --no-cache-dir -r requirements.txt

COPY pyproject.toml .
RUN mkdir -p src && pip install --no-cache-dir -e .

COPY src src

# Create minimal default configuration for Docker to fix EOSDash accessibility (#629)
# This ensures EOSDash binds to 0.0.0.0 instead of 127.0.0.1 in containers
RUN echo '{\n\
  "server": {\n\
    "host": "0.0.0.0",\n\
    "port": 8503,\n\
    "startup_eosdash": true,\n\
    "eosdash_host": "0.0.0.0",\n\
    "eosdash_port": 8504\n\
  }\n\
}' > "${EOS_CONFIG_DIR}/EOS.config.json" \
    && chown eos:eos "${EOS_CONFIG_DIR}/EOS.config.json"

USER eos
ENTRYPOINT []

EXPOSE 8503
EXPOSE 8504

CMD ["python", "src/akkudoktoreos/server/eos.py", "--host", "0.0.0.0"]

VOLUME ["${MPLCONFIGDIR}", "${EOS_CACHE_DIR}", "${EOS_OUTPUT_DIR}", "${EOS_CONFIG_DIR}"]
