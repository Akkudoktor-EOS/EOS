ARG PYTHON_VERSION=3.12.7
FROM python:${PYTHON_VERSION}-slim

LABEL source="https://github.com/Akkudoktor-EOS/EOS"

ENV VIRTUAL_ENV="/opt/venv"
ENV PATH="${VIRTUAL_ENV}/bin:${PATH}"
ENV MPLCONFIGDIR="/tmp/mplconfigdir"
ENV EOS_DIR="/opt/eos"
ENV EOS_CACHE_DIR="${EOS_DIR}/cache"
ENV EOS_OUTPUT_DIR="${EOS_DIR}/output"
ENV EOS_CONFIG_DIR="${EOS_DIR}/config"

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
    pip install -r requirements.txt

COPY pyproject.toml .
RUN mkdir -p src && pip install -e .

COPY src src

USER eos
ENTRYPOINT []

EXPOSE 8503
EXPOSE 8504

CMD ["python", "src/akkudoktoreos/server/eos.py", "--host", "0.0.0.0"]

VOLUME ["${MPLCONFIGDIR}", "${EOS_CACHE_DIR}", "${EOS_OUTPUT_DIR}", "${EOS_CONFIG_DIR}"]
