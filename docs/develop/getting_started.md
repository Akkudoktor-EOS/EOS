% SPDX-License-Identifier: Apache-2.0
(getting-started-page)=

# Getting Started

## Installation and Running

AkkudoktorEOS can be installed and run using several different methods:

- **Release package** (for stable versions)
- **Docker image** (for easy deployment)
- **From source** (for developers)

See the [installation guideline](#install-page) for detailed instructions on each method.

### Where to Find AkkudoktorEOS

- **Release Packages**: [GitHub Releases](https://github.com/Akkudoktor-EOS/EOS/releases)
- **Docker Images**: [Docker Hub](https://hub.docker.com/r/akkudoktor/eos)
- **Source Code**: [GitHub Repository](https://github.com/Akkudoktor-EOS/EOS)

## Configuration

AkkudoktorEOS uses the `EOS.config.json` file to manage all configuration settings.

### Default Configuration

If essential configuration settings are missing, the application automatically uses a default
configuration to get you started quickly.

### Custom Configuration Directory

You can specify a custom location for your configuration by setting the `EOS_DIR` environment
variable:

```bash
export EOS_DIR=/path/to/your/config
```

**How it works:**

- **If `EOS.config.json` exists** in the `EOS_DIR` directory → the application uses this
  configuration
- **If `EOS.config.json` doesn't exist** → the application copies `default.config.json` to `EOS_DIR`
  as `EOS.config.json`

### Creating Your Configuration

There are three ways to configure AkkudoktorEOS:

1. **EOSdash (Recommended)** - The easiest method is to use the web-based dashboard at
   [http://localhost:8504](http://localhost:8504)

2. **Manual editing** - Create or edit the `EOS.config.json` file directly in your preferred text
   editor

3. **Server API** - Programmatically change configuration through the [server API](#server-api-page)

For a complete reference of all available configuration options, see the [configuration guideline](#configuration-page).

## Quick Start Example

```bash
# Pull the latest docker image
docker pull akkudoktor/eos:latest

# Run the application
docker run -d \
  --name akkudoktoreos \
  -p 8503:8503 \
  -p 8504:8504 \
  -e OPENBLAS_NUM_THREADS=1 \
  -e OMP_NUM_THREADS=1 \
  -e MKL_NUM_THREADS=1 \
  -e EOS_SERVER__HOST=0.0.0.0 \
  -e EOS_SERVER__PORT=8503 \
  -e EOS_SERVER__EOSDASH_HOST=0.0.0.0 \
  -e EOS_SERVER__EOSDASH_PORT=8504 \
  --ulimit nproc=65535:65535 \
  --ulimit nofile=65535:65535 \
  --security-opt seccomp=unconfined \
  akkudoktor/eos:latest

# Access the dashboard
open http://localhost:8504
```
