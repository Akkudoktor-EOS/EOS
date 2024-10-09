ARG PYTHON_VERSION=3.12.7
FROM python:${PYTHON_VERSION}-slim

LABEL source="https://github.com/Akkudoktor-EOS/EOS"

EXPOSE 5000

WORKDIR	/opt/eos

COPY src src
COPY pyproject.toml pyproject.toml
COPY requirements.txt requirements.txt

RUN DEBIAN_FRONTEND=noninteractive \
	apt-get update \
	&& rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir build \
    && pip install --no-cache-dir -e .

ENTRYPOINT []

CMD ["python", "-m", "akkudoktoreosserver.flask_server"]
