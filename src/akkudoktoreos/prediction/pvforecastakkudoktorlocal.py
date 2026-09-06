"""Native PV power forecast computed inside EOS from Open-Meteo weather with pvlib.

Unlike the other PV forecast providers this one does not ask a third-party service
for PV power. It fetches raw irradiance and weather from Open-Meteo and runs the
whole modelling chain locally with pvlib:

    solar position -> horizon shading -> transposition to the module plane ->
    incidence-angle modifier -> cell temperature -> PVWatts DC -> inverter AC

Why this exists:

* **Horizon.** Open-Meteo serves up to 16 forecast days at 15-minute resolution in a
  single request. Services that wrap it (including api.akkudoktor.net) cut the
  horizon much shorter, which starves ``optimization.tail_horizon_hours``.
* **Call budget.** One request per hour against a ~10k/day non-commercial budget,
  instead of competing for someone else's upstream quota.
* **Honest parameters.** ``albedo``, inverter efficiency and the module temperature
  coefficient become real configuration instead of constants baked into a URL.

Two conventions matter and are handled explicitly here:

* Open-Meteo radiation values are the mean over the **preceding** interval, so the
  representative sun position for a value stamped ``t`` is ``t - interval/2``.
* EOS records label an interval by its **start** (``key_to_array`` resamples with
  left-labelled buckets), so a value stamped ``t`` by Open-Meteo is stored at
  ``t - interval``. Set ``shift_to_interval_start`` to False to keep the raw stamps.

Note also that ``direct_radiation`` in the Open-Meteo API is beam irradiance on the
*horizontal* plane; the DNI this chain needs is ``direct_normal_irradiance``.
"""

import math
from typing import Any, Optional

import numpy as np
import pandas as pd
import pendulum
import pvlib
import requests
from loguru import logger
from pydantic import Field, field_validator

from akkudoktoreos.config.configabc import SettingsBaseModel
from akkudoktoreos.core.cache import cache_in_file
from akkudoktoreos.prediction.pvforecastabc import PVForecastProvider
from akkudoktoreos.utils.datetimeutil import compare_datetimes, to_datetime, to_duration

OPENMETEO_URL = "https://api.open-meteo.com/v1/forecast"

# Open-Meteo variables the pvlib chain needs. `direct_normal_irradiance` is the DNI;
# `direct_radiation` (beam on the horizontal) would be wrong here.
OPENMETEO_VARIABLES = (
    "temperature_2m",
    "relative_humidity_2m",
    "wind_speed_10m",
    "shortwave_radiation",
    "diffuse_radiation",
    "direct_normal_irradiance",
)

# Open-Meteo hard limits for a single forecast request.
MAX_FORECAST_DAYS = 16
MAX_PAST_DAYS = 92

TRANSPOSITION_MODELS = ("isotropic", "klucher", "haydavies", "reindl", "king", "perez")

# pvlib SAPM cell temperature parameter set per EOS `mountingplace`.
MOUNTING_TEMPERATURE_MODEL = {
    "free": "open_rack_glass_glass",
    "building": "close_mount_glass_glass",
}


class PVForecastAkkudoktorLocalCommonSettings(SettingsBaseModel):
    """Common settings for the local (pvlib) PV forecast provider."""

    resolution_minutes: int = Field(
        default=15,
        json_schema_extra={
            "description": (
                "Forecast resolution in minutes. 15 requests Open-Meteo's `minutely_15` "
                "block (natively resolved over Central Europe and North America, "
                "interpolated from hourly elsewhere); 60 requests the `hourly` block."
            ),
            "examples": [15, 60],
        },
    )
    forecast_days: Optional[int] = Field(
        default=None,
        ge=1,
        le=MAX_FORECAST_DAYS,
        json_schema_extra={
            "description": (
                "Forecast horizon in days (1-16). Leave empty to derive it from "
                "`prediction.hours`, which is what keeps the optimizer's tail horizon fed."
            ),
            "examples": [None, 7],
        },
    )
    past_days: Optional[int] = Field(
        default=None,
        ge=0,
        le=MAX_PAST_DAYS,
        json_schema_extra={
            "description": (
                "Days of past data to request (0-92). Leave empty to derive it from "
                "`prediction.historic_hours`."
            ),
            "examples": [None, 3],
        },
    )
    weather_models: list[str] = Field(
        default=["best_match"],
        min_length=1,
        json_schema_extra={
            "description": (
                "Open-Meteo weather models to request. Listing more than one turns the "
                "input into a poor-man's ensemble: the members are averaged per variable, "
                "which is the cheapest reliable way to cut irradiance forecast error. "
                "Costs no extra API calls."
            ),
            "examples": [
                ["best_match"],
                ["icon_seamless", "ecmwf_ifs025", "gfs_seamless"],
            ],
        },
    )
    transposition_model: str = Field(
        default="perez",
        json_schema_extra={
            "description": (
                "pvlib sky-diffuse transposition model: isotropic, klucher, haydavies, "
                "reindl, king or perez."
            ),
            "examples": ["perez", "haydavies"],
        },
    )
    albedo: float = Field(
        default=0.25,
        ge=0.0,
        le=1.0,
        json_schema_extra={
            "description": "Ground albedo used for planes that do not set their own.",
            "examples": [0.25, 0.2],
        },
    )
    inverter_efficiency: float = Field(
        default=0.96,
        gt=0.0,
        le=1.0,
        json_schema_extra={
            "description": "Nominal inverter efficiency (PVWatts eta_inv_nom).",
            "examples": [0.96, 0.94],
        },
    )
    temperature_coefficient: float = Field(
        default=-0.36,
        json_schema_extra={
            "description": (
                "Module power temperature coefficient in %/degC (negative). Matches the "
                "`cellCoEff` the akkudoktor.net forecast uses."
            ),
            "examples": [-0.36, -0.29],
        },
    )
    apply_iam: bool = Field(
        default=True,
        json_schema_extra={
            "description": "Apply the ASHRAE incidence-angle modifier to the beam component.",
            "examples": [True],
        },
    )
    shift_to_interval_start: bool = Field(
        default=True,
        json_schema_extra={
            "description": (
                "Open-Meteo stamps an interval mean with the interval END. EOS labels an "
                "interval by its START, so records are shifted back by one interval. "
                "Disable only to compare like-for-like against a provider that does not."
            ),
            "examples": [True],
        },
    )

    calibration_enabled: bool = Field(
        default=False,
        json_schema_extra={
            "description": (
                "Correct systematic model error against measured PV production. Requires "
                "`measurement.pv_production_emr_keys` to be configured and fed. Fits a "
                "global scale factor plus per-solar-azimuth factors, which is what catches "
                "near-field shading the horizon profile misses."
            ),
            "examples": [True],
        },
    )
    calibration_days: int = Field(
        default=30,
        ge=1,
        le=MAX_PAST_DAYS,
        json_schema_extra={
            "description": "Length of the measurement window used to fit the correction.",
            "examples": [30, 14],
        },
    )
    calibration_azimuth_bin_degrees: int = Field(
        default=15,
        ge=0,
        le=180,
        json_schema_extra={
            "description": (
                "Width of the solar-azimuth bins for the correction. 0 fits a single "
                "global factor only."
            ),
            "examples": [15, 30, 0],
        },
    )
    calibration_prior_kwh: float = Field(
        default=5.0,
        ge=0.0,
        json_schema_extra={
            "description": (
                "Shrinkage strength: a bin needs this much modelled energy before its own "
                "factor outweighs the global one. Higher is more conservative."
            ),
            "examples": [5.0, 20.0],
        },
    )
    calibration_min_factor: float = Field(
        default=0.5,
        gt=0.0,
        json_schema_extra={
            "description": "Lower clamp on any fitted correction factor.",
            "examples": [0.5],
        },
    )
    calibration_max_factor: float = Field(
        default=1.5,
        gt=0.0,
        json_schema_extra={
            "description": "Upper clamp on any fitted correction factor.",
            "examples": [1.5],
        },
    )

    @field_validator("resolution_minutes")
    @classmethod
    def validate_resolution(cls, value: int) -> int:
        if value not in (15, 60):
            raise ValueError(f"resolution_minutes must be 15 or 60, got {value}")
        return value

    @field_validator("transposition_model")
    @classmethod
    def validate_transposition_model(cls, value: str) -> str:
        if value not in TRANSPOSITION_MODELS:
            raise ValueError(
                f"Invalid transposition_model '{value}', expected one of {TRANSPOSITION_MODELS}"
            )
        return value


class PVForecastAkkudoktorLocal(PVForecastProvider):
    """Compute the PV forecast locally from Open-Meteo irradiance using pvlib."""

    @classmethod
    def provider_id(cls) -> str:
        """Return the unique identifier for the PV-Forecast-Provider."""
        return "PVForecastAkkudoktorLocal"

    @property
    def _settings(self) -> PVForecastAkkudoktorLocalCommonSettings:
        settings = self.config.pvforecast.provider_settings.PVForecastAkkudoktorLocal
        if settings is None:
            settings = PVForecastAkkudoktorLocalCommonSettings()
        return settings

    # ------------------------------------------------------------------ request

    def _horizon_days(self) -> tuple[int, int]:
        """Resolve (forecast_days, past_days), deriving them from prediction config."""
        settings = self._settings

        forecast_days = settings.forecast_days
        if forecast_days is None:
            # +1 day so the last requested hour is still covered when the run starts
            # late in the day.
            hours = self.config.prediction.hours or 48
            forecast_days = min(MAX_FORECAST_DAYS, max(1, math.ceil(hours / 24) + 1))

        past_days = settings.past_days
        if past_days is None:
            historic_hours = self.config.prediction.historic_hours or 0
            past_days = math.ceil(historic_hours / 24)
        if settings.calibration_enabled:
            # The fit compares modelled against measured power over the same past
            # intervals, so the weather for that window has to come back with the request.
            past_days = max(past_days, settings.calibration_days)

        return forecast_days, min(MAX_PAST_DAYS, past_days)

    @cache_in_file(with_ttl="1 hour")
    def _request_forecast(self) -> Any:
        """Fetch raw irradiance and weather from Open-Meteo."""
        latitude = self.config.general.latitude
        longitude = self.config.general.longitude
        if latitude is None or longitude is None:
            raise ValueError("PVForecastAkkudoktorLocal needs general.latitude and general.longitude")

        settings = self._settings
        block = "minutely_15" if settings.resolution_minutes == 15 else "hourly"
        forecast_days, past_days = self._horizon_days()

        params = {
            "latitude": latitude,
            "longitude": longitude,
            block: ",".join(OPENMETEO_VARIABLES),
            # Ask for UTC so the returned stamps are unambiguous across DST changes.
            "timezone": "UTC",
            # pvlib's SAPM cell temperature model wants m/s; Open-Meteo defaults to km/h.
            "wind_speed_unit": "ms",
            "forecast_days": forecast_days,
            "past_days": past_days,
            "models": ",".join(settings.weather_models),
        }

        try:
            response = requests.get(OPENMETEO_URL, params=params, timeout=30)
            logger.debug(f"Requesting Open-Meteo forecast: {response.url}")
            response.raise_for_status()
        except requests.RequestException as e:
            logger.error(f"Failed to fetch weather for local pvforecast: {e}")
            raise RuntimeError("Failed to fetch weather from Open-Meteo API") from e

        data = response.json()
        if block not in data:
            raise ValueError(
                f"Open-Meteo response is missing the '{block}' block: {list(data.keys())}"
            )

        self.update_datetime = to_datetime(in_timezone=self.config.general.timezone)
        return data

    # ------------------------------------------------------------------ weather

    def _weather_frame(self, data: Any) -> pd.DataFrame:
        """Build a UTC-indexed weather frame from the Open-Meteo response."""
        block = "minutely_15" if self._settings.resolution_minutes == 15 else "hourly"
        raw = data[block]

        index = pd.DatetimeIndex(
            [pendulum.parse(str(t), tz="UTC") for t in raw["time"]], name="time"
        ).tz_convert("UTC")

        frame = pd.DataFrame(index=index)
        for variable in OPENMETEO_VARIABLES:
            # With a single model Open-Meteo returns the bare variable name; with several
            # it suffixes each member (`shortwave_radiation_icon_seamless`). Average the
            # members that actually came back - not every model carries every variable.
            members = [
                pd.to_numeric(pd.Series(raw[key], index=index), errors="coerce")
                for key in raw
                if key == variable or key.startswith(f"{variable}_")
            ]
            if not members:
                raise ValueError(f"Open-Meteo response is missing '{variable}'")
            frame[variable] = pd.concat(members, axis=1).mean(axis=1, skipna=True)

        # Night rows and occasional gaps arrive as null. Irradiance is genuinely zero
        # then; temperature and wind are interpolated so the temperature model stays
        # defined instead of poisoning the whole row with NaN.
        for variable in ("shortwave_radiation", "diffuse_radiation", "direct_normal_irradiance"):
            frame[variable] = frame[variable].fillna(0.0).clip(lower=0.0)
        for variable in ("temperature_2m", "relative_humidity_2m", "wind_speed_10m"):
            frame[variable] = frame[variable].interpolate(method="time").ffill().bfill()
        frame["wind_speed_10m"] = frame["wind_speed_10m"].fillna(1.0).clip(lower=0.0)
        frame["temperature_2m"] = frame["temperature_2m"].fillna(15.0)

        return frame

    # ------------------------------------------------------------------ geometry

    @staticmethod
    def _horizon_elevation(userhorizon: list[float], solar_azimuth: np.ndarray) -> np.ndarray:
        """Interpolate the horizon elevation at each solar azimuth.

        ``userhorizon`` follows the PVGIS convention: elevations in degrees at equally
        spaced azimuths clockwise from north, the first entry being due north. The
        profile wraps around, so the first entry is repeated at 360 degrees.
        """
        horizon = np.asarray(userhorizon, dtype=float)
        count = len(horizon)
        azimuths = np.arange(count, dtype=float) * (360.0 / count)
        return np.interp(
            np.asarray(solar_azimuth, dtype=float) % 360.0,
            np.append(azimuths, 360.0),
            np.append(horizon, horizon[0]),
        )

    @staticmethod
    def _tracked_orientation(plane: Any, solpos: pd.DataFrame) -> tuple[Any, Any]:
        """Return (surface_tilt, surface_azimuth) honouring the plane's tracking type."""
        tilt = float(plane.surface_tilt if plane.surface_tilt is not None else 30.0)
        azimuth = float(plane.surface_azimuth if plane.surface_azimuth is not None else 180.0)
        tracking = plane.trackingtype

        if tracking in (None, 0):
            return tilt, azimuth

        apparent_zenith = solpos["apparent_zenith"]
        solar_azimuth = solpos["azimuth"]

        if tracking == 2:
            # Two-axis: the plane always faces the sun. Below the horizon the angles are
            # meaningless, so park the plane flat and let the zero irradiance do the rest.
            return apparent_zenith.clip(lower=0.0, upper=90.0), solar_azimuth
        if tracking == 3:
            # Vertical axis: fixed tilt, azimuth follows the sun.
            return tilt, solar_azimuth
        if tracking in (1, 4, 5):
            # Horizontal N-S (1), horizontal E-W (4), inclined N-S (5).
            axis_tilt = tilt if tracking == 5 else 0.0
            axis_azimuth = 90.0 if tracking == 4 else 0.0
            tracker = pvlib.tracking.singleaxis(
                apparent_zenith=apparent_zenith,
                solar_azimuth=solar_azimuth,
                axis_tilt=axis_tilt,
                axis_azimuth=axis_azimuth,
                max_angle=90,
                backtrack=False,
            )
            return (
                tracker["surface_tilt"].fillna(axis_tilt),
                tracker["surface_azimuth"].fillna(axis_azimuth),
            )

        logger.warning(
            f"Unsupported trackingtype {tracking} for local pvforecast, treating plane as fixed."
        )
        return tilt, azimuth

    # ------------------------------------------------------------------ pv model

    def _plane_power(
        self,
        plane: Any,
        weather: pd.DataFrame,
        solpos: pd.DataFrame,
        dni_extra: pd.Series,
        airmass: pd.Series,
    ) -> tuple[pd.Series, pd.Series]:
        """Run the pvlib chain for one plane, returning (dc_power_w, ac_power_w)."""
        settings = self._settings

        peakpower_kw = plane.peakpower
        if peakpower_kw is None:
            logger.warning("Plane without peakpower skipped by local pvforecast.")
            zero = pd.Series(0.0, index=weather.index)
            return zero, zero
        pdc0 = float(peakpower_kw) * 1000.0

        ghi = weather["shortwave_radiation"]
        dhi = weather["diffuse_radiation"]
        dni = weather["direct_normal_irradiance"]

        # Horizon shading kills the beam component; what is left of the global is the
        # diffuse. Do this before transposition so the sky model sees consistent inputs.
        if plane.userhorizon:
            horizon = self._horizon_elevation(plane.userhorizon, solpos["azimuth"].to_numpy())
            shaded = solpos["apparent_elevation"].to_numpy() < horizon
            dni = dni.where(~shaded, 0.0)
            ghi = ghi.where(~shaded, dhi)

        surface_tilt, surface_azimuth = self._tracked_orientation(plane, solpos)
        albedo = plane.albedo if plane.albedo is not None else settings.albedo

        poa = pvlib.irradiance.get_total_irradiance(
            surface_tilt=surface_tilt,
            surface_azimuth=surface_azimuth,
            solar_zenith=solpos["apparent_zenith"],
            solar_azimuth=solpos["azimuth"],
            dni=dni,
            ghi=ghi,
            dhi=dhi,
            dni_extra=dni_extra,
            airmass=airmass,
            albedo=float(albedo),
            model=settings.transposition_model,
        )
        poa_global = poa["poa_global"].fillna(0.0).clip(lower=0.0)
        poa_direct = poa["poa_direct"].fillna(0.0).clip(lower=0.0)
        poa_diffuse = poa["poa_diffuse"].fillna(0.0).clip(lower=0.0)

        if settings.apply_iam:
            aoi = pvlib.irradiance.aoi(
                surface_tilt, surface_azimuth, solpos["apparent_zenith"], solpos["azimuth"]
            )
            iam = pvlib.iam.ashrae(aoi).fillna(0.0)
            effective_irradiance = poa_direct * iam + poa_diffuse
        else:
            effective_irradiance = poa_global

        mounting = plane.mountingplace or "free"
        temperature_params = pvlib.temperature.TEMPERATURE_MODEL_PARAMETERS["sapm"][
            MOUNTING_TEMPERATURE_MODEL.get(mounting, "open_rack_glass_glass")
        ]
        temp_cell = pvlib.temperature.sapm_cell(
            poa_global=poa_global,
            temp_air=weather["temperature_2m"],
            wind_speed=weather["wind_speed_10m"],
            **temperature_params,
        )

        dc_power = pvlib.pvsystem.pvwatts_dc(
            effective_irradiance=effective_irradiance,
            temp_cell=temp_cell,
            pdc0=pdc0,
            gamma_pdc=settings.temperature_coefficient / 100.0,
        )
        # `loss` is the PVGIS-style lump of soiling, mismatch, wiring and ageing.
        loss = plane.loss if plane.loss is not None else 0.0
        dc_power = (dc_power * (1.0 - float(loss) / 100.0)).fillna(0.0).clip(lower=0.0)

        paco = plane.inverter_paco
        eta = settings.inverter_efficiency
        if paco is None:
            # No inverter rating configured: apply efficiency but do not clip.
            ac_power = dc_power * eta
        else:
            ac_power = pd.Series(
                pvlib.inverter.pvwatts(
                    pdc=dc_power.to_numpy(),
                    pdc0=float(paco) / eta,
                    eta_inv_nom=eta,
                ),
                index=dc_power.index,
            )
        ac_power = ac_power.fillna(0.0).clip(lower=0.0)

        return dc_power, ac_power

    def _forecast_frame(self, data: Any, calibrate: bool = True) -> pd.DataFrame:
        """Run the full chain and return a frame with dc/ac power indexed as EOS records.

        Args:
            data: The Open-Meteo response.
            calibrate: Apply the measurement-fitted correction when it is enabled and
                fittable. Pass False to obtain the raw model output, which is what the
                fit itself compares against.
        """
        settings = self._settings
        weather = self._weather_frame(data)
        if weather.empty:
            return pd.DataFrame(columns=["dc_power", "ac_power"])

        location = pvlib.location.Location(
            latitude=float(self.config.general.latitude),
            longitude=float(self.config.general.longitude),
            tz="UTC",
            altitude=data.get("elevation"),
        )

        # Open-Meteo stamps an interval mean with the interval end, so the sun position
        # that produced it sits half an interval earlier.
        interval = pd.Timedelta(minutes=settings.resolution_minutes)
        solar_times = weather.index - interval / 2

        solpos_solar = location.get_solarposition(solar_times)
        solpos = solpos_solar.set_axis(weather.index)
        dni_extra = pd.Series(
            np.asarray(pvlib.irradiance.get_extra_radiation(solar_times), dtype=float),
            index=weather.index,
        )
        airmass = pd.Series(
            location.get_airmass(solar_times, solar_position=solpos_solar)[
                "airmass_relative"
            ].to_numpy(),
            index=weather.index,
        )

        total_dc = pd.Series(0.0, index=weather.index)
        total_ac = pd.Series(0.0, index=weather.index)
        for plane in self.config.pvforecast.planes or []:
            dc_power, ac_power = self._plane_power(plane, weather, solpos, dni_extra, airmass)
            total_dc = total_dc.add(dc_power, fill_value=0.0)
            total_ac = total_ac.add(ac_power, fill_value=0.0)

        frame = pd.DataFrame(
            {
                "dc_power": total_dc,
                "ac_power": total_ac,
                "solar_elevation": solpos["apparent_elevation"].to_numpy(),
                "solar_azimuth": solpos["azimuth"].to_numpy(),
            }
        )
        # Relabel from Open-Meteo's interval-end stamps to the interval-start stamps
        # that EOS records use.
        if settings.shift_to_interval_start:
            frame.index = frame.index - interval

        if calibrate:
            # The fit needs the raw model to compare against, which is exactly `frame`.
            calibration = self._fit_calibration(frame)
            if calibration is not None:
                _, factors = calibration
                frame = self._apply_calibration(frame, factors, self._installed_ac_capacity_w())
        return frame

    # ------------------------------------------------------------ calibration

    def _installed_ac_capacity_w(self) -> float:
        """Rough installed AC capacity, used only to threshold near-zero intervals."""
        total = 0.0
        for plane in self.config.pvforecast.planes or []:
            if plane.inverter_paco is not None:
                total += float(plane.inverter_paco)
            elif plane.peakpower is not None:
                total += float(plane.peakpower) * 1000.0
        return total

    def _fit_calibration(self, frame: pd.DataFrame) -> Optional[tuple[float, np.ndarray]]:
        """Fit correction factors from measured PV production against the model.

        The comparison runs on past intervals, where the Open-Meteo rows are analysed
        rather than forecast weather. That is deliberate: it isolates the error of the
        *PV model* (wrong kWp, soiling, degradation, shading the horizon profile misses)
        from the error of the *weather forecast*, and only the former is systematic
        enough to correct.

        Returns:
            (global_factor, per_azimuth_bin_factors) or None when there is not enough
            data to fit anything.
        """
        settings = self._settings
        if not settings.calibration_enabled:
            return None
        if not self.config.measurement.pv_production_emr_keys:
            logger.info(
                "PVForecastAkkudoktorLocal calibration is enabled but "
                "measurement.pv_production_emr_keys is not configured - skipping."
            )
            return None

        measurement = self.measurement
        if measurement.max_datetime is None or measurement.min_datetime is None:
            logger.info("PVForecastAkkudoktorLocal calibration: no PV measurements yet - skipping.")
            return None

        interval = to_duration("1 hour")
        end = measurement.max_datetime.start_of("hour")
        start = end.subtract(days=settings.calibration_days)
        if compare_datetimes(start, measurement.min_datetime).lt:
            start = measurement.min_datetime.start_of("hour").add(hours=1)
        # The model side only exists for the weather window that was requested.
        model_start = to_datetime(frame.index[0].to_pydatetime())
        if compare_datetimes(start, model_start).lt:
            start = model_start.start_of("hour").add(hours=1)
        if compare_datetimes(start, end).ge:
            logger.info("PVForecastAkkudoktorLocal calibration: measurement window too short - skipping.")
            return None

        measured_kwh = np.asarray(
            measurement.pv_production_total_kwh(
                start_datetime=start, end_datetime=end, interval=interval
            ),
            dtype=float,
        )
        if measured_kwh.size == 0 or not np.isfinite(measured_kwh).any():
            logger.info("PVForecastAkkudoktorLocal calibration: no usable PV measurements - skipping.")
            return None

        # Model side on the same hourly grid. `ac_power` is a mean power per interval,
        # so the hourly mean in W is directly the hourly energy in Wh.
        hourly = frame[["ac_power", "solar_azimuth"]].resample("1h").mean()
        grid = pd.date_range(
            start=pd.Timestamp(start.in_timezone("UTC").isoformat()),
            periods=len(measured_kwh),
            freq="1h",
        )
        hourly = hourly.reindex(grid)
        modelled_kwh = hourly["ac_power"].to_numpy(dtype=float) / 1000.0
        azimuth = hourly["solar_azimuth"].to_numpy(dtype=float)

        # Only fit where the model says something meaningful is being produced. Dawn and
        # dusk intervals otherwise dominate the ratio with noise.
        floor_kwh = max(0.02 * self._installed_ac_capacity_w() / 1000.0, 0.05)
        usable = (
            np.isfinite(modelled_kwh)
            & np.isfinite(measured_kwh)
            & np.isfinite(azimuth)
            & (modelled_kwh > floor_kwh)
            & (measured_kwh >= 0.0)
        )
        if usable.sum() < 12:
            logger.info(
                f"PVForecastAkkudoktorLocal calibration: only {int(usable.sum())} usable hours - skipping."
            )
            return None

        modelled_kwh = modelled_kwh[usable]
        measured_kwh = measured_kwh[usable]
        azimuth = azimuth[usable]

        model_total = float(modelled_kwh.sum())
        if model_total <= 0.0:
            return None
        global_factor = float(
            np.clip(
                measured_kwh.sum() / model_total,
                settings.calibration_min_factor,
                settings.calibration_max_factor,
            )
        )

        bin_degrees = settings.calibration_azimuth_bin_degrees
        if bin_degrees <= 0:
            logger.info(
                f"PVForecastAkkudoktorLocal calibration: global factor {global_factor:.3f} "
                f"from {usable.sum()} hours."
            )
            return global_factor, np.array([global_factor])

        bin_count = max(1, int(round(360 / bin_degrees)))
        bin_index = np.clip((azimuth % 360.0) / (360.0 / bin_count), 0, bin_count - 1).astype(int)

        # Weight each bin by its modelled energy and shrink toward the global factor, so
        # a thinly sampled bin cannot swing the forecast on its own.
        prior = settings.calibration_prior_kwh
        factors = np.full(bin_count, global_factor, dtype=float)
        for b in range(bin_count):
            in_bin = bin_index == b
            weight = float(modelled_kwh[in_bin].sum())
            if weight <= 0.0:
                continue
            raw = float(measured_kwh[in_bin].sum()) / weight
            factors[b] = np.clip(
                (weight * raw + prior * global_factor) / (weight + prior),
                settings.calibration_min_factor,
                settings.calibration_max_factor,
            )

        # Report how much of the bias the fit actually removes on its own training window.
        before = float(np.abs(modelled_kwh - measured_kwh).mean())
        after = float(np.abs(modelled_kwh * factors[bin_index] - measured_kwh).mean())
        logger.info(
            f"PVForecastAkkudoktorLocal calibration over {usable.sum()} h: global factor "
            f"{global_factor:.3f}, {bin_count} azimuth bins, "
            f"MAE {before:.3f} -> {after:.3f} kWh/h"
        )
        return global_factor, factors

    @staticmethod
    def _apply_calibration(
        frame: pd.DataFrame, factors: np.ndarray, ac_cap_w: float
    ) -> pd.DataFrame:
        """Scale the modelled power by the per-azimuth factor of each interval."""
        bin_count = len(factors)
        azimuth = frame["solar_azimuth"].to_numpy(dtype=float)
        bin_index = np.clip(
            np.nan_to_num(azimuth % 360.0) / (360.0 / bin_count), 0, bin_count - 1
        ).astype(int)
        scale = factors[bin_index]
        frame = frame.copy()
        frame["dc_power"] = frame["dc_power"] * scale
        frame["ac_power"] = frame["ac_power"] * scale
        if ac_cap_w > 0.0:
            # A factor above 1 must not push the plant past its inverters.
            frame["ac_power"] = frame["ac_power"].clip(upper=ac_cap_w)
        return frame

    # ------------------------------------------------------------------ update

    def _update_data(self, force_update: Optional[bool] = False) -> None:
        """Compute the PV forecast and store it as PVForecastDataRecord entries."""
        if not self.enabled():
            logger.info("PVForecastAkkudoktorLocal is disabled, skipping update.")
            return

        if not self.config.pvforecast.planes:
            error_msg = "Requested PV forecast, but no planes configured."
            logger.error(f"Configuration error: {error_msg}")
            raise ValueError(error_msg)

        data = self._request_forecast(force_update=force_update)  # type: ignore[call-arg]
        frame = self._forecast_frame(data)
        if frame.empty:
            logger.warning("Open-Meteo returned no weather rows for local pvforecast.")
            return

        for timestamp, row in frame.iterrows():
            self.update_value(
                to_datetime(timestamp.to_pydatetime()),
                {
                    "pvforecast_dc_power": round(float(row["dc_power"]), 1),
                    "pvforecast_ac_power": round(float(row["ac_power"]), 1),
                },
            )

        logger.debug(
            f"Updated local pvforecast: {len(frame)} records at "
            f"{self._settings.resolution_minutes} min over "
            f"{len(self.config.pvforecast.planes)} plane(s)."
        )
        self.update_datetime = to_datetime(in_timezone=self.config.general.timezone)


# Example usage
if __name__ == "__main__":
    pv = PVForecastAkkudoktorLocal()
    pv._update_data()
