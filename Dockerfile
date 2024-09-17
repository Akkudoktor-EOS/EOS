ARG PYTHON_VERSION=3.12.6
FROM python:${PYTHON_VERSION}-slim

LABEL source="https://github.com/Akkudoktor-EOS/EOS"

EXPOSE 5000

ARG APT_OPTS="--yes --auto-remove --no-install-recommends --no-install-suggests"
RUN DEBIAN_FRONTEND=noninteractive \
	apt-get update \
	&& apt-get install ${APT_OPTS} gcc libhdf5-dev libmariadb-dev pkg-config \
	&& rm -rf /var/lib/apt/lists/*

WORKDIR /var/lib/eos
WORKDIR	/opt/eos

COPY requirements.txt requirements.txt

RUN pip install --no-cache-dir -r requirements.txt

COPY . .
COPY config.example.py config.py

ENTRYPOINT []

CMD ["python", "flask_server.py"]
