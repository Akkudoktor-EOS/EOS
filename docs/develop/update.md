% SPDX-License-Identifier: Apache-2.0
(update-page)=

# Update Guide

This guide explains how to update AkkudoktorEOS to a newer version.

- Updating from Source (M1)
- Updating from Release Package (M2)
- Updating Docker Installation (M3)
- Updating Docker Compose Installation (M4)
- Updating Home Assistant Add-on Installation (M5)

Choose the section based on how you originally [installed EOS](install-page).

:::{admonition} Tip
:class: Note
If you need to revert instead, see the see the [Revert Guideline](revert-page).
:::

## Updating from Source (M1)

```{eval-rst}
.. tabs::

  .. tab:: Windows

     .. code-block:: powershell

        git pull origin main
        .venv\Scripts\pip install -r requirements.txt --upgrade

  .. tab:: Linux

     .. code-block:: bash

        git pull origin main
        .venv/bin/pip install -r requirements.txt --upgrade
```

Restart EOS normally.

## Updating from Release Package (M2)

1. Download new release
2. Extract to a new directory
3. Recreate virtual environment & reinstall dependencies
4. Optionally remove previous directory

Follow steps from [Installation from Release Package (GitHub) (M2)](install-page).

## Updating Docker Installation (M3)

```bash
docker pull akkudoktor/eos:latest
docker stop akkudoktoreos
docker rm akkudoktoreos
```

Then start the container again using your normal `docker run` command.

## Updating Docker Compose Installation (M4)

1. Stop & remove existing container

   ```bash
   docker stop akkudoktoreos
   docker rm akkudoktoreos
   ```

2. Update source (if using source checkout) — see M1 or M2
3. Rebuild & start

   ```bash
   docker compose up --build
   ```

## Verify Docker Update (M3/M4)

Check logs:

```bash
docker logs akkudoktoreos
```

Then visit:

- API: [http://localhost:8503/docs](http://localhost:8503/docs)
- UI: [http://localhost:8504](http://localhost:8504)

## Updating Home Assistant Add-on Installation (M5)

1. Open 'Home Assistant' and navigate to 'Settings → Add-ons'.
2. Select the 'Akkudoktor-EOS' add-on from your installed add-ons.
3. If an update is available, click 'Update'.
4. Wait for the update process to finish, then restart the add-on if prompted.

If you installed Akkudoktor-EOS from a custom repository and no update appears, open the Add-on
Store, click the '⋮' menu in the top right, and choose 'Reload' to refresh the repository.

## Backup Recommendation

Before updating, back up your config:

```bash
EOS.config.json
```

EOS also maintains internal configuration backups.
