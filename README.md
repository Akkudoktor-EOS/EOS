![AkkudoktorEOS](docs/_static/logo.png)

**Build optimized energy management plans for your home automation**

AkkudoktorEOS is a comprehensive solution for simulating and optimizing energy systems based on
renewable sources. Optimize your photovoltaic systems, battery storage, load management, and
electric vehicles while considering real-time electricity pricing.

## Why use AkkudoktorEOS?

AkkudoktorEOS can be used to build energy management plans that are optimized for your specific
setup of PV system, battery, electric vehicle, household load and electricity pricing. It can
be integrated into home automation systems such as NodeRED, Home Assistant, EVCC.

## 🏘️ Community

We are an open-source community-driven project and we love to hear from you. Here are some ways to
get involved:

- [GitHub Issue Tracker](https://github.com/Akkudoktor-EOS/EOS/issues): discuss ideas and features,
and report bugs.

- [Akkudoktor Forum](https://www.akkudoktor.net/c/der-akkudoktor/eos): get direct suppport from the
cummunity.

## What do people build with AkkudoktorEOS

The community uses AkkudoktorEOS to minimize grid energy consumption and to maximize the revenue
from grid energy feed in with their home automation system.

- Andreas Schmitz, [the Akkudoktor](https://www.youtube.com/@Akkudoktor), uses
  EOS integrated in his NodeRED home automation system for
  [OpenSource Energieoptimierung](https://www.youtube.com/watch?v=sHtv0JCxAYk).
- Jörg, [meintechblog](https://www.youtube.com/@meintechblog), uses EOS for
  day-ahead optimization for time-variable energy prices. See:
  [So installiere ich EOS von Andreas Schmitz](https://www.youtube.com/watch?v=9XCPNU9UqSs)

## Why not use AkkudoktorEOS?

AkkudoktorEOS does not control your home automation assets. It must be integrated into a home
automation system. If you do not use a home automation system or you feel uncomfortable with
the configuration effort needed for the integration you should better use other solutions.

## Quick Start

Run EOS with Docker (access dashboard at `http://localhost:8504`):

```bash
docker run -d \
  --name akkudoktoreos \
  -p 8503:8503 \
  -p 8504:8504 \
  -e OPENBLAS_NUM_THREADS=1 \
  -e OMP_NUM_THREADS=1 \
  -e MKL_NUM_THREADS=1 \
  -e EOS_SERVER__HOST=0.0.0.0 \
  -e EOS_SERVER__EOSDASH_HOST=0.0.0.0 \
  -e EOS_SERVER__EOSDASH_PORT=8504 \
  --ulimit nproc=65535:65535 \
  --ulimit nofile=65535:65535 \
  --security-opt seccomp=unconfined \
  akkudoktor/eos:latest
```

## System Requirements

- **Python**: 3.11 or higher
- **Architecture**: amd64, aarch64 (armv8)
- **OS**: Linux, Windows, macOS

> **Note**: Other architectures (armv6, armv7) require manual compilation of dependencies with Rust and GCC.

## Installation

### Docker (Recommended)

```bash
docker pull akkudoktor/eos:latest
docker compose up -d
```

Access the API at `http://localhost:8503` (docs at `http://localhost:8503/docs`)

### From Source

```bash
git clone https://github.com/Akkudoktor-EOS/EOS.git
cd EOS
```

**Linux:**

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/pip install -e .
.venv/bin/python -m akkudoktoreos.server.eos
```

**Windows:**

```cmd
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\pip install -e .
.venv\Scripts\python -m akkudoktoreos.server.eos
```

## Configuration

EOS uses `EOS.config.json` for configuration. If the file doesn't exist, a default configuration is
created automatically.

### Custom Configuration Directory

```bash
export EOS_DIR=/path/to/your/config
```

### Configuration Methods

1. **EOSdash** (Recommended) - Web interface at `http://localhost:8504`
2. **Manual** - Edit `EOS.config.json` directly
3. **API** - Use the [Server API](https://petstore3.swagger.io/?url=https://raw.githubusercontent.com/Akkudoktor-EOS/EOS/refs/heads/main/openapi.json)

See the [documentation](https://akkudoktor-eos.readthedocs.io/) for all configuration options.

## Port Configuration

**Default ports**: 8503 (API), 8504 (Dashboard)

If running on shared systems (e.g., Synology NAS), these ports may conflict with system services. Reconfigure port mappings as needed:

```bash
docker run -p 8505:8503 -p 8506:8504 ...
```

## API Documentation

Interactive API docs available at:
- Swagger UI: `http://localhost:8503/docs`
- OpenAPI Spec: [View Online](https://petstore3.swagger.io/?url=https://raw.githubusercontent.com/Akkudoktor-EOS/EOS/refs/heads/main/openapi.json)

## Resources

- [Full Documentation](https://akkudoktor-eos.readthedocs.io/)
- [Installation Guide (German)](https://www.youtube.com/watch?v=9XCPNU9UqSs)

## Contributing

We welcome contributions! See [CONTRIBUTING](CONTRIBUTING.md) for guidelines.

[![Contributors](https://contrib.rocks/image?repo=Akkudoktor-EOS/EOS)](https://github.com/Akkudoktor-EOS/EOS/graphs/contributors)

## License

This project is licensed under the Apache License 2.0 - see the LICENSE file for details.
