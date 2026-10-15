# Adaptive Akkudoktor PV forecast

`PVForecastAdaptiveAkkudoktor` is an opt-in provider that combines local
Open-Meteo/pvlib forecasts with measured production. It publishes the normal
`pvforecast_ac_power` and `pvforecast_dc_power` keys, so the optimizer can use
it without an external PV forecast override.

## What learns

- Each measured plane has an energy gain and a smooth daily correction.
  Training uses completed previous days and genuinely issued forecasts.
- Independent, uncurtailed reference planes correct the next two hours. The
  correction fades with forecast distance and does not carry over into tomorrow.
- A separate residual/trend regression learns each quarter-hour lead from
  archived features and subsequently available measurements. Until five suitable
  days and 100 pairs per lead exist, it retains the local correction.

ICON D2 is preferred near term, with a transition to the other configured
weather models between four and six hours. Missing daylight radiation cannot
become zero production. A failed weather update retains a usable old forecast.

The provider does not identify physical module angles from live measurements.
Configure each actual plane separately. Optional initial response profiles in
the learning archive are fixed starting estimates, distinct from live training.
More training data do not guarantee a lower error on every day.

## Configuration

Merge the following sections into the existing EOS configuration, keeping the
site location, devices, tariffs and other settings. Replace the example planes
with your actual plant. Azimuth is clockwise from north; peak power is in kWp.
Each plane needs its own distinct power channel and at least one plane must
be an independent weather reference.

```json
{
  "pvforecast": {
    "provider": "PVForecastAdaptiveAkkudoktor",
    "adaptive": {
      "weather_models": ["icon_seamless", "icon_d2"],
      "regional_weather_model": "icon_d2",
      "learning_days": 21,
      "min_training_days": 5,
      "archive_retention_days": 60,
      "nowcast_enabled": true,
      "trend_enabled": true
    },
    "planes": [
      {
        "name": "Hybrid inverter",
        "peakpower": 5.0,
        "surface_tilt": 20,
        "surface_azimuth": 180,
        "measurement_key": "pv_hybrid_w",
        "curtailment_sensitive": true
      },
      {
        "name": "Independent microinverter",
        "peakpower": 1.0,
        "surface_tilt": 30,
        "surface_azimuth": 180,
        "measurement_key": "pv_micro_w",
        "nowcast_reference": true
      }
    ]
  },
  "measurement": {
    "channels": {
      "pv_hybrid_w": {
        "quantity": "power",
        "unit": "W",
        "integration_method": "hold",
        "max_gap_seconds": 600
      },
      "pv_micro_w": {
        "quantity": "power",
        "unit": "W",
        "integration_method": "hold",
        "max_gap_seconds": 600
      }
    }
  }
}
```

Mark battery-full-curtailed hybrid strings as `curtailment_sensitive`. They do
not train unrestricted weather gain and cannot be reference planes. The
independent reference transfers the local weather residual to the whole plant.
Whole-plant validation still includes any actual curtailment.

Send original measured power, not energy counters, to the ordinary measurement
API or directly to `PUT /v1/prediction/pv-adaptive/samples`. The direct endpoint
accepts at most 10,000 samples per request, assigned plane keys, nonnegative
values and explicit time zones. It rejects future samples. A sample has this
shape; substitute the real acquisition timestamp and value:

```json
[
  {
    "date_time": "2026-01-01T12:00:00+01:00",
    "key": "pv_micro_w",
    "value": 350.0
  }
]
```

Supply all configured planes regularly, including legitimate night-time zeros.
EOS integrates complete 15-minute intervals with actual temporal coverage.
Missing readings stay missing. Use the `linear` integration method instead of
`hold` only if that matches the sensor's semantics.

## Install this branch on a Linux NUC

### Update an existing source installation

Stop EOS and back up its configuration and data directory. In your existing
EOS checkout, update to the PR branch and synchronize dependencies:

```bash
git fetch origin
git switch feat/adaptive-pv-learning
git pull --ff-only
uv sync --frozen
git rev-parse HEAD
```

Record the printed commit for a reproducible installation. Keep any local
changes safe before switching branches. For a fresh checkout, clone the branch
with the command shown in the Docker section below, then run `uv sync --frozen`.

Merge the PV configuration into the existing `EOS.config.json`, retaining your
devices, tariffs and native data paths. In particular, preserve
`general.data_folder_path`, `general.data_output_subpath` and the environment
variables used by your existing launcher, including `EOS_CONFIG_DIR` and
`EOS_DATA_DIR` where set. Select `PVForecastAdaptiveAkkudoktor` as the provider
and configure the per-plane measurement channels above.

To retain an existing adaptive instance's learning, transfer its configuration
and SQLite archive together. Stop the source instance while copying, or use
SQLite's backup API for a consistent live copy. With the target EOS stopped,
back up any existing target archive and copy the transferred `learning.sqlite3`
to `<data_folder_path>/<data_output_subpath>/adaptive-pv/learning.sqlite3`.
The default output subpath is `output`; the destination must be writable by
the user running EOS. A configuration patch alone does not transfer learning.

Restart EOS with your existing service or launcher. For a manual LAN start
from the checkout, using the same configuration environment as before:

```bash
uv run python -m akkudoktoreos.server.eos --host 0.0.0.0 --eosdash-host 0.0.0.0
```

Retain your existing port options if customized. Check the adaptive `status`
and `latest` endpoints below, including tomorrow's forecast, then redirect
Node-RED's EOS measurement delivery, optimizer requests and diagnostic export
to the NUC's EOS host and port. Remove any external `pv_forecast_wh` override
from optimizer requests so the native provider supplies the PV forecast.
Switch control and export delivery to one EOS instance when activating it.
Check that a valid optimization plan is produced using the adaptive provider.
This provider does not require changing battery or optimizer settings.

### Optional Docker build

The PR branch is source code, not an automatically published DockerHub image.
For a container installation with Docker already installed, build it locally:

```bash
git clone --branch feat/adaptive-pv-learning --single-branch \
  https://github.com/Akkudoktor-EOS/EOS.git eos-adaptive-pv
cd eos-adaptive-pv
git rev-parse HEAD
docker build --build-arg BUILD_VERSION=adaptive-pv -t eos:adaptive-pv .
docker volume create eos-adaptive-data
docker run -d --name eos-adaptive-pv --restart unless-stopped \
  -p 8505:8503 -p 8506:8504 \
  -v eos-adaptive-data:/data eos:adaptive-pv
```

These example host ports allow a separate instance; select available ports on
your NUC. Configure
it through EOS before switching measurement delivery and the optimizer client
to the new instance. A fresh volume begins without your plant configuration.

The container stores configuration at `/data/config/EOS.config.json` and the
learning archive at `/data/output/adaptive-pv/learning.sqlite3`. The volume
must survive container replacement. The forecast needs internet access to
Open-Meteo; local weather correction needs your sensor delivery as well.

To move an existing adaptive instance, back up its configuration and learning
archive together while the source instance is stopped, or use SQLite's backup
API for a consistent live archive copy. Transfer them into the corresponding
paths of the new data volume while the target instance is stopped. Set
`general.data_folder_path` to `/data` and `general.data_output_subpath` to
`output` in the container configuration. Check other machine-specific paths
in the existing configuration before starting the target.

Keep the original instance's configuration and data available until the new
installation has produced a valid forecast and an optimization using it.

## Learning progress and Grafana

The private archive retains samples, issued forecasts and trained models across
restarts. Its default retention is 60 days; the learning window is 21 days.
Changing the plant, weather configuration or response profiles changes the
training signature so incompatible histories are not mixed.

The following HTTP endpoints are available:

| Endpoint | Purpose |
| --- | --- |
| `GET /v1/prediction/pv-adaptive/status` | State, training days/counts, per-plane gain and matched errors |
| `GET /v1/prediction/pv-adaptive/latest` | Latest forecast with raw, calibrated, local and adaptive power |
| `GET /v1/prediction/pv-adaptive/comparisons` | Issued predictions paired with later observations |
| `PUT /v1/prediction/pv-adaptive/samples` | Bounded ingestion of original plane readings |

For a compact learning panel, plot matched `mae_w` from status `metrics` over
time for one scope and horizon, with `method` distinguishing the stages. Do
not compare different observation sets or display missing errors as zero.
The API does not require Grafana or MariaDB. Existing Node-RED/Grafana bridges
must point to the new EOS address after migration.

`state` reports `initializing`, `reference_missing`, `learning`, `active` or
`night`. A fresh installation still provides a physical forecast while it
collects genuinely issued training pairs. Historical sensor readings alone
cannot recreate missing issued forecasts.

Check `/v1/prediction/pv-adaptive/latest` for tomorrow as well as today. The
native output is mean power in W per interval; daily kWh is the sum of W times
0.25 divided by 1000 for a complete local day. Keep time stamps when aligning
with the optimizer, including on daylight-saving transitions.

To revert, select the previous PV provider and restore its configuration.
Retain the data directory or container volume with the learned archive.
