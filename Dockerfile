ARG PYTHON_VERSION=3.12.7
FROM python:${PYTHON_VERSION}-slim

LABEL source="https://github.com/Akkudoktor-EOS/EOS"

EXPOSE 5000

WORKDIR	/opt/eos

COPY . .

ARG APT_OPTS="--yes --auto-remove --no-install-recommends --no-install-suggests"

RUN DEBIAN_FRONTEND=noninteractive \
	apt-get update \
	&& apt-get install ${APT_OPTS} gcc libhdf5-dev libmariadb-dev pkg-config mariadb-common libmariadb3 \
	&& rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir build \
    && pip install --no-cache-dir -e . \
    && apt remove ${APT_OPTS} gcc libhdf5-dev libmariadb-dev pkg-config

ENTRYPOINT []

CMD ["python", "-m", "akkudoktoreos.flask_server"]
