ARG PYTHON_VERSION=3.12.7
FROM python:${PYTHON_VERSION}-slim

LABEL source="https://github.com/Akkudoktor-EOS/EOS"

ENV VIRTUAL_ENV="/opt/venv"
ENV PATH="${VIRTUAL_ENV}/bin:${PATH}"
ENV MPLCONFIGDIR="/tmp/mplconfigdir"
ENV EOS_DIR="/opt/eos"
ENV EOS_CACHE_DIR="${EOS_DIR}/cache"
ENV EOS_OUTPUT_DIR="${EOS_DIR}/output"

WORKDIR ${EOS_DIR}

RUN adduser --system --group --no-create-home eos \
    && mkdir -p "${MPLCONFIGDIR}" \
    && chown eos "${MPLCONFIGDIR}" \
    && mkdir -p "${EOS_CACHE_DIR}" \
    && chown eos "${EOS_CACHE_DIR}" \
    && mkdir -p "${EOS_OUTPUT_DIR}" \
    && chown eos "${EOS_OUTPUT_DIR}"

COPY requirements.txt .

RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements.txt

COPY src .

USER eos
ENTRYPOINT []

CMD ["python", "-m", "akkudoktoreosserver.fastapi_server"]

VOLUME ["${MPLCONFIGDIR}", "${EOS_CACHE_DIR}", "${EOS_OUTPUT_DIR}"]
