% SPDX-License-Identifier: Apache-2.0
(revert-page)=

# Revert Guide

This guide explains how to **revert AkkudoktorEOS to a previous version**.
The exact methods and steps differ depending on how EOS was installed:

- M1/M2: Reverting when Installed from Source or Release Package
- M3/M4: Reverting when Installed via Docker

:::{admonition} Important
:class: warning
Before reverting, ensure you have a backup of your `EOS.config.json`.
EOS also maintains internal configuration backups that can be restored after a downgrade.
:::

:::{admonition} Tip
:class: Note
If you need to update instead, see the [Update Guideline](update-page).
:::

## Revert to a Previous Version of EOS

You can revert to a previous version using the same installation method you originally selected.
See: [Installation Guideline](install-page)

## Reverting when Installed from Source or Release Package (M1/M2)

### 1) Locate the target version (M2)

Go to the GitHub Releases page:

> <https://github.com/Akkudoktor-EOS/EOS/tags>

### 2) Download or check out that version (M1/M2)

#### Git (source) (M1)

```bash
git fetch
git checkout v<version>
````

Example:

```bash
git checkout v0.1.0
```

Then reinstall dependencies:

```bash
uv sync
```

#### Release package (M2)

Download and extract the desired ZIP or TAR release.
Refer to **Method 2** in the [Installation Guideline](install-page).

### 3) Restart EOS (M1/M2)

```bash
uv run python -m akkudoktoreos.server.eos
```

### 4) Restore configuration (optional) (M1/M2)

If your configuration changed since the downgrade, you may restore a previous backup:

- via **EOSdash**

    Admin → configuration → Revert to backup

    or

    Admin → configuration → Import from file

- via **REST**

    ```bash
    curl -X PUT "http://<host>:8503/v1/config/revert?backup_id=<backup>"
    ```

## Reverting when Installed via Docker (M3/M4)

### 1) Pull the desired image version (M3/M4)

```bash
docker pull akkudoktor/eos:v<version>
```

Example:

```bash
docker pull akkudoktor/eos:v0.1.0
```

### 2) Stop and remove the current container (M3/M4)

```bash
docker stop akkudoktoreos
docker rm akkudoktoreos
```

### 3) Start a container with the selected version (M3/M4)

Start EOS as usual, using your existing `docker run` or `docker compose` setup
(see Method 3 or Method 4 in the [Installation Guideline](install-page)).

### 4) Restore configuration (optional) (M3/M4)

In many cases configuration will migrate automatically.
If needed, you may restore a configuration backup:

- via **EOSdash**

    Admin → configuration → Revert to backup

    or

    Admin → configuration → Import from file

- via **REST**

    ```bash
    curl -X PUT "http://<host>:8503/v1/config/revert?backup_id=<backup>"
    ```

## About Configuration Backups

EOS keeps configuration backup files next to your active `EOS.config.json`.

You can list and restore backups:

- via **EOSdash UI**
- via **REST API**

### List available backups

```bash
GET /v1/config/backups
```

### Restore backup

```bash
PUT /v1/config/revert?backup_id=<id>
```

:::{admonition} Important
:class: warning
If no backup file is available, create or copy a previously saved `EOS.config.json` before reverting.
:::
