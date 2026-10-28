"""Adaptive local Akkudoktor PV forecast, causal learning and public diagnostics."""

import asyncio
import bisect
import hashlib
from pathlib import Path
from typing import Any, Callable, Literal, Optional, Self, cast

import numpy as np
import pandas as pd
from loguru import logger
from pydantic import Field, model_validator

from akkudoktoreos.core.databaseabc import DatabaseTimestamp
from akkudoktoreos.measurement.energy import energy_intervals
from akkudoktoreos.measurement.quality import MeasurementSample, SampleQuality
from akkudoktoreos.prediction.pvadaptive import AdaptivePVModel, apply_local_nowcast
from akkudoktoreos.prediction.pvforecastabc import PVForecastDataRecord
from akkudoktoreos.prediction.pvforecastakkudoktorlocal import (
    OPENMETEO_VARIABLES,
    PVForecastAkkudoktorLocal,
    PVForecastAkkudoktorLocalCommonSettings,
)
from akkudoktoreos.prediction.pvlearnstore import PVLearningStore
from akkudoktoreos.prediction.pvquality import choose_correction, select_corrections
from akkudoktoreos.prediction.pvtrend import LocalPVTrendModel, local_trend_features
from akkudoktoreos.utils.datetimeutil import to_datetime


class PVForecastAdaptiveAkkudoktorCommonSettings(PVForecastAkkudoktorLocalCommonSettings):
    """Automatic defaults; configure measurements and reference flags on each plane."""

    backend: Literal["remote", "local"] = "local"
    calibration_enabled: bool = False
    regional_weather_model: Optional[str] = "icon_d2"
    regional_horizon_hours: float = Field(default=6, gt=0, le=48)
    learning_days: int = Field(default=21, ge=7, le=30)
    min_training_days: int = Field(default=5, ge=2, le=14)
    max_ac_power_w: float = Field(default=0, ge=0)
    nominal_total_peakpower_kw: Optional[float] = Field(
        default=None,
        gt=0,
        description="Known installed nameplate total; fitted plane powers may be effective estimates.",
        examples=[None],
    )
    diagnostics_days: int = Field(default=7, ge=1, le=30)
    archive_retention_days: int = Field(default=60, ge=31, le=365)
    nowcast_enabled: bool = True
    trend_enabled: bool = True
    correction_mode: Literal["auto", "calibrated", "local", "trend"] = Field(
        default="auto",
        description="Choose validated short-term candidates per group/lead, or explicitly fix a correction stage.",
    )
    validation_days: int = Field(default=7, ge=3, le=30)
    min_validation_days: int = Field(default=3, ge=2, le=14)
    min_validation_samples: int = Field(default=32, ge=8)
    min_validation_improvement: float = Field(default=0.02, ge=0, le=0.5)
    training_delay_minutes: int = Field(default=15, ge=0, le=120)
    total_measurement_key: Optional[str] = Field(
        default=None,
        examples=[None],
        min_length=1,
        max_length=128,
        description="Optional whole-plant power channel when individual plane measurements are unavailable.",
    )
    total_curtailment_sensitive: bool = False
    total_curtailment_key: Optional[str] = Field(
        default=None, min_length=1, max_length=128, examples=[None]
    )
    total_learning_guard_key: Optional[str] = Field(
        default=None, min_length=1, max_length=128, examples=[None]
    )
    total_learning_guard_max: Optional[float] = Field(
        default=None, allow_inf_nan=False, examples=[None]
    )
    total_learning_guard_max_gap_seconds: int = Field(default=600, ge=1, le=3600)

    @model_validator(mode="after")
    def validate_adaptive(self) -> Self:
        """Validate native interval semantics and add regional weather coherently."""
        if (
            self.backend != "local"
            or self.resolution_minutes != 15
            or not self.shift_to_interval_start
        ):
            raise ValueError(
                "Adaptive PV requires local weather and 15-minute interval-start timestamps."
            )
        if self.calibration_enabled:
            raise ValueError("Use adaptive learning instead of legacy calibration.")
        if self.min_training_days > self.learning_days:
            raise ValueError("Training days cannot exceed the learning window.")
        if self.min_validation_days > self.validation_days:
            raise ValueError("Validation days cannot exceed the validation window.")
        if bool(self.total_learning_guard_key) != (self.total_learning_guard_max is not None):
            raise ValueError("A total learning guard requires both key and upper bound.")
        if self.total_curtailment_key and self.total_learning_guard_key:
            raise ValueError("Use either a total curtailment flag or learning guard.")
        if self.regional_weather_model not in (None, "icon_d2"):
            raise ValueError("Only ICON D2 is supported as regional preference.")
        if self.regional_weather_model and self.regional_weather_model not in self.weather_models:
            self.weather_models = [*self.weather_models, self.regional_weather_model]
        if self.weather_models == ["icon_d2"]:
            self.weather_models.append("icon_seamless")
        return self


class PVForecastAdaptiveAkkudoktorDataRecord(PVForecastDataRecord):
    """Normal optimizer power keys plus inspectable correction and learning values."""

    pvforecast_adaptive_raw_ac_power: Optional[float] = None
    pvforecast_adaptive_calibrated_ac_power: Optional[float] = None
    pvforecast_adaptive_nowcast_delta: Optional[float] = None
    pvforecast_adaptive_reference_power: Optional[float] = None
    pvforecast_adaptive_training_days: Optional[float] = None
    pvforecast_adaptive_training_samples: Optional[float] = None
    pvforecast_adaptive_state_code: Optional[float] = None
    pvforecast_adaptive_weather_weight: Optional[float] = None


class PVForecastAdaptiveAkkudoktor(
    PVForecastAkkudoktorLocal[PVForecastAdaptiveAkkudoktorDataRecord]
):
    """Learn from independent arrays; preserve every issued short-term comparison."""

    @classmethod
    def provider_id(cls) -> str:
        return "PVForecastAdaptiveAkkudoktor"

    @property
    def _settings(self) -> PVForecastAdaptiveAkkudoktorCommonSettings:
        return self.config.pvforecast.adaptive

    def store(self) -> PVLearningStore:
        """Private bounded archive under the existing EOS output directory."""
        output = (
            Path(self.config.general.data_folder_path) / self.config.general.data_output_subpath
        )
        return PVLearningStore(output / "adaptive-pv" / "learning.sqlite3")

    def _signature(self) -> str:
        settings = {
            key: self._settings.model_dump(mode="json")[key]
            for key in (
                "weather_models",
                "regional_weather_model",
                "regional_horizon_hours",
                "transposition_model",
                "inverter_efficiency",
                "temperature_coefficient",
                "albedo",
                "apply_iam",
            )
        }
        payload = dict(
            latitude=self.config.general.latitude,
            longitude=self.config.general.longitude,
            timezone=self.config.general.timezone,
            settings=settings,
            planes=[
                plane.model_dump(
                    mode="json",
                    exclude={
                        "name",
                        "parameters_estimated",
                        "correction_group",
                        "curtailment_key",
                        "learning_guard_key",
                        "learning_guard_max",
                        "learning_guard_max_gap_seconds",
                    },
                )
                for plane in self.config.pvforecast.planes or []
            ],
            channels={
                plane.measurement_key: self.measurement._energy_channel(
                    plane.measurement_key
                ).model_dump(mode="json")
                for plane in self.config.pvforecast.planes or []
                if plane.measurement_key
            },
            profiles=self.store().metadata("profiles") or {},
        )
        if self._settings.total_measurement_key:
            payload["total_measurement"] = dict(
                key=self._settings.total_measurement_key,
                channel=self.measurement._energy_channel(
                    self._settings.total_measurement_key
                ).model_dump(mode="json"),
            )
        if any(plane.inverter_paco is not None for plane in self.config.pvforecast.planes or []):
            # Older issued candidates could exceed these limits after calibration.
            # Preserve the archive, but do not validate the bounded model on that
            # incompatible candidate definition. Unrated installations retain
            # their existing physical signature and training history.
            payload["bounded_power_model_version"] = 2
        return hashlib.sha256(PVLearningStore.dump(payload).encode()).hexdigest()

    async def _sync_measurements(self, issue: pd.Timestamp) -> None:
        """Accept normal EOS power channels as well as bounded direct ingestion."""
        keys = self._sample_keys()
        values = []
        async for record in self.measurement.db_iterate_records(
            DatabaseTimestamp.from_datetime(
                to_datetime((issue - pd.Timedelta(hours=3)).to_pydatetime())
            ),
            DatabaseTimestamp.from_datetime(to_datetime(issue.to_pydatetime()).add(seconds=1)),
        ):
            if record.date_time is None or pd.Timestamp(record.date_time) > issue:
                continue
            for key in keys.intersection(record.configured_data):
                value = record.configured_data[key]
                quality = record.sample_quality.get(key, SampleQuality())
                if value is None or not np.isfinite(value) or value < 0:
                    value, quality = None, SampleQuality(status="invalid")
                values.append(
                    MeasurementSample(
                        date_time=record.date_time,
                        key=key,
                        value=float(value) if value is not None else None,
                        quality=quality,
                    )
                )
        if values:
            await asyncio.to_thread(self.store().import_samples, values, issue)

    def _plane_keys(self) -> list[str]:
        return [
            plane.measurement_key or f"plane_{i}"
            for i, plane in enumerate(self.config.pvforecast.planes or [])
        ]

    def _sample_keys(self) -> set[str]:
        keys = {p.measurement_key for p in self.config.pvforecast.planes or []}
        keys.update(p.curtailment_key for p in self.config.pvforecast.planes or [])
        keys.update(p.learning_guard_key for p in self.config.pvforecast.planes or [])
        keys.update((self._settings.total_measurement_key, self._settings.total_curtailment_key))
        keys.add(self._settings.total_learning_guard_key)
        return {key for key in keys if key is not None}

    def _learning_planes(self) -> list[Any]:
        """A total-power channel is one learning entity over all physical planes."""
        if not self._settings.total_measurement_key:
            return list(self.config.pvforecast.planes or [])
        from types import SimpleNamespace

        return [
            SimpleNamespace(
                measurement_key=self._settings.total_measurement_key,
                curtailment_sensitive=self._settings.total_curtailment_sensitive,
                curtailment_key=self._settings.total_curtailment_key,
                learning_guard_key=self._settings.total_learning_guard_key,
                learning_guard_max=self._settings.total_learning_guard_max,
                learning_guard_max_gap_seconds=self._settings.total_learning_guard_max_gap_seconds,
                nowcast_reference=not self._settings.total_curtailment_sensitive
                or bool(
                    self._settings.total_curtailment_key or self._settings.total_learning_guard_key
                ),
                correction_group="plant",
            )
        ]

    def _references(self) -> list[str]:
        planes = self._learning_planes()
        explicit = [p.measurement_key for p in planes if p.nowcast_reference]
        return (
            explicit
            or [p.measurement_key for p in planes if not p.curtailment_sensitive]
            or [p.measurement_key for p in planes if p.curtailment_key or p.learning_guard_key]
        )

    def _groups(self) -> dict[str, list[str]]:
        if self._settings.total_measurement_key:
            return {"plant": [self._settings.total_measurement_key]}
        references = self._references()
        result: dict[str, list[str]] = {}
        for plane in self._learning_planes():
            group = (
                "group:" + plane.correction_group
                if plane.correction_group
                else "reference"
                if plane.measurement_key in references
                else "plane:" + plane.measurement_key
            )
            result.setdefault(group, []).append(plane.measurement_key)
        return result

    def _reference_group(self) -> Optional[str]:
        references = set(self._references())
        return next(
            (name for name, keys in self._groups().items() if set(keys) == references), None
        )

    def _horizon_days(self) -> tuple[int, int]:
        future, _ = super()._horizon_days()
        return future, 2

    def _weather_frame(self, data: dict[str, Any]) -> pd.DataFrame:
        """Preserve radiation gaps, coherent D2 rows and a finite regional transition."""
        raw = data["minutely_15"]
        index = pd.DatetimeIndex(pd.to_datetime(raw["time"], utc=True))
        frame, regional = pd.DataFrame(index=index), pd.DataFrame(index=index)
        preferred = self._settings.regional_weather_model
        for variable in OPENMETEO_VARIABLES:
            members = [name for name in raw if name == variable or name.startswith(variable + "_")]
            fallback = [
                name for name in members if not preferred or name != variable + "_" + preferred
            ]
            if not fallback:
                fallback = members
            if not fallback:
                raise ValueError("Weather missing " + variable)
            frame[variable] = (
                pd.DataFrame({name: raw[name] for name in fallback}, index=index)
                .apply(pd.to_numeric, errors="coerce")
                .mean(axis=1)
            )
            regional[variable] = pd.to_numeric(
                pd.Series(
                    raw.get(variable + "_" + str(preferred), [np.nan] * len(index)), index=index
                ),
                errors="coerce",
            )
        valid = np.isfinite(regional.to_numpy()).all(axis=1)
        lead = (
            index - pd.Timedelta(minutes=15) - pd.Timestamp.now(tz="UTC")
        ).total_seconds() / 3600
        horizon = self._settings.regional_horizon_hours
        weight = np.asarray(np.clip((horizon - lead) / min(2, horizon), 0, 1), dtype=float)
        weight[~valid] = 0
        weight[valid & ~np.isfinite(frame.to_numpy()).all(axis=1)] = 1
        for variable in OPENMETEO_VARIABLES:
            blend = frame[variable] * (1 - weight) + regional[variable] * weight
            frame.loc[weight > 0, variable] = blend.loc[weight > 0]
            frame.loc[weight == 1, variable] = regional.loc[weight == 1, variable]
        for variable in OPENMETEO_VARIABLES[:3]:
            frame[variable] = frame[variable].interpolate(method="time").ffill().bfill()
        frame["wind_speed_10m"] = frame.wind_speed_10m.fillna(1).clip(lower=0)
        frame["temperature_2m"] = frame.temperature_2m.fillna(15)
        for variable in OPENMETEO_VARIABLES[3:]:
            frame[variable] = frame[variable].clip(lower=0)
        frame["regional_weight"] = weight
        return frame

    def _physical_frames(self, data: dict[str, Any]) -> list[pd.DataFrame]:
        """One physical/learned response per measured plane, plus clear-sky envelope."""
        import pvlib

        weather = self._weather_frame(data)
        location = pvlib.location.Location(
            float(self.config.general.latitude),
            float(self.config.general.longitude),
            tz="UTC",
            altitude=data.get("elevation", 200),
        )
        times = pd.DatetimeIndex(weather.index) - pd.Timedelta(minutes=7.5)
        solar_native = location.get_solarposition(times)
        solar = solar_native.set_axis(weather.index)
        extra = pd.Series(
            np.asarray(pvlib.irradiance.get_extra_radiation(times)), index=weather.index
        )
        airmass = location.get_airmass(
            times, solar_position=solar_native
        ).airmass_relative.set_axis(weather.index)
        clear = weather.copy()
        clearsky = location.get_clearsky(times).set_axis(weather.index)
        for variable, name in zip(OPENMETEO_VARIABLES[3:], ("ghi", "dhi", "dni")):
            clear[variable] = clearsky[name]
        profiles = self.store().metadata("profiles") or {}
        frames = []
        for source in (weather, clear):
            frame = pd.DataFrame(index=weather.index)
            az, el = solar.azimuth.to_numpy(), solar.apparent_elevation.to_numpy()
            az_weights = np.maximum(0, 1 - np.abs(az[:, None] - np.arange(60, 301, 30)) / 30)
            el_weights = np.maximum(0, 1 - np.abs(el[:, None] - np.arange(0, 81, 20)) / 20)
            basis = (az_weights[:, :, None] * el_weights[:, None, :]).reshape(len(az), 45)
            beam = source.direct_normal_irradiance.to_numpy() * np.maximum(
                0, np.cos(np.radians(solar.apparent_zenith))
            )
            fraction = np.clip(beam / np.maximum(source.shortwave_radiation.to_numpy(), 1), 0, 1)
            for number, plane in enumerate(self.config.pvforecast.planes or []):
                dc_power, power = self._plane_power(plane, source, solar, extra, airmass)
                profile = profiles.get(plane.measurement_key, {})
                coefficients = profile.get("response")
                if coefficients is not None:
                    response = np.asarray(coefficients, dtype=float)
                    if (
                        response.shape != (46,)
                        or not np.isfinite(response).all()
                        or (response < 0).any()
                    ):
                        raise ValueError("Invalid learned string response.")
                    scale = fraction * (basis @ response[:45]) + (1 - fraction) * response[45]
                    power, dc_power = power * scale, dc_power * scale
                valid = source[list(OPENMETEO_VARIABLES[3:])].notna().all(
                    axis=1
                ) | solar.apparent_elevation.le(0)
                key = plane.measurement_key or f"plane_{number}"
                frame[key] = power.clip(upper=plane.inverter_paco).where(valid)
                frame[key + "_dc"] = dc_power.where(valid)
            frame.index = pd.DatetimeIndex(frame.index) - pd.Timedelta(minutes=15)
            frame["weather_weight"] = weather.regional_weight.to_numpy()
            frame["solar_elevation"] = solar.apparent_elevation.to_numpy()
            if self._settings.total_measurement_key:
                frame[self._settings.total_measurement_key] = frame[self._plane_keys()].sum(
                    axis=1, min_count=len(self._plane_keys())
                )
            frames.append(frame)
        return frames

    def _measurements(self, cutoff: pd.Timestamp, days: int = 2) -> pd.DataFrame:
        """Time-weighted complete intervals, closed by actually available samples."""
        start = (cutoff - pd.Timedelta(days=days)).tz_convert("UTC").floor("15min")
        end = cutoff.tz_convert("UTC").floor("15min")
        index = pd.date_range(start, end, freq="15min", inclusive="left")
        result = pd.DataFrame(index=index)
        store = self.store()
        for plane in self._learning_planes():
            key = plane.measurement_key
            if not key:
                raise ValueError("Each adaptive plane needs its own measurement_key.")
            channel = self.measurement._energy_channel(key)
            if channel.quantity != "power" or channel.unit not in ("W", "kW"):
                raise ValueError("Adaptive string measurements must be power in W or kW.")
            rows = store.samples(key, start - pd.Timedelta(hours=1), cutoff)
            points = [
                (pd.Timestamp(row["time_ms"], unit="ms", tz="UTC").to_pydatetime(), row["value"])
                for row in rows
            ]
            quality = {row["time_ms"] / 1000: SampleQuality(status=row["quality"]) for row in rows}
            intervals = energy_intervals(
                points, channel, start.to_pydatetime(), end.to_pydatetime(), 900, quality
            )
            times = [row["time_ms"] for row in rows]
            power, available = [], []
            for interval in intervals:
                close = bisect.bisect_left(times, int(interval.end.timestamp() * 1000))
                first = max(
                    0, bisect.bisect_right(times, int(interval.start.timestamp() * 1000)) - 1
                )
                supported = rows[first : close + 1]
                valid = (
                    interval.coverage_status == "complete"
                    and interval.energy_wh is not None
                    and interval.energy_wh >= 0
                    and close < len(rows)
                    and all(row["quality"] not in ("invalid", "unavailable") for row in supported)
                )
                power.append(
                    interval.energy_wh * 4 if valid and interval.energy_wh is not None else np.nan
                )
                available.append(
                    pd.Timestamp(max(row["available_ms"] for row in supported), unit="ms", tz="UTC")
                    if valid
                    else pd.NaT
                )
            result[key] = power
            result[key + "_available"] = pd.to_datetime(pd.Index(available), utc=True)
            result[key + "_eligible"] = not plane.curtailment_sensitive
            guard_key = plane.curtailment_key or plane.learning_guard_key
            if guard_key:
                flags = store.samples(guard_key, start - pd.Timedelta(hours=1), cutoff)
                if plane.learning_guard_key and not plane.curtailment_key:
                    if plane.learning_guard_max is None:
                        raise ValueError("A learning guard needs an explicit upper bound.")
                    flags = [
                        {
                            **row,
                            "value": (0.0 if row["value"] < plane.learning_guard_max else 1.0)
                            if row["value"] is not None
                            else None,
                        }
                        for row in flags
                    ]
                flag_points = [
                    (
                        pd.Timestamp(row["time_ms"], unit="ms", tz="UTC").to_pydatetime(),
                        row["value"],
                    )
                    for row in flags
                ]
                flag_quality = {
                    row["time_ms"] / 1000: SampleQuality(status=row["quality"]) for row in flags
                }
                # A flag needs complete support just like power. A single stale
                # zero cannot authorize a whole interval of unrestricted learning.
                from akkudoktoreos.measurement.measurement import (
                    MeasurementChannelSettings,
                )

                flag_channel = MeasurementChannelSettings(
                    quantity="power",
                    unit="W",
                    integration_method="hold",
                    max_gap_seconds=plane.learning_guard_max_gap_seconds,
                )
                flag_intervals = energy_intervals(
                    flag_points,
                    flag_channel,
                    start.to_pydatetime(),
                    end.to_pydatetime(),
                    900,
                    flag_quality,
                )
                flag_ends = [row["time_ms"] for row in flags]
                eligible, flag_available = [], []
                for interval in flag_intervals:
                    close = bisect.bisect_left(flag_ends, int(interval.end.timestamp() * 1000))
                    first = max(
                        0,
                        bisect.bisect_right(flag_ends, int(interval.start.timestamp() * 1000)) - 1,
                    )
                    supported = flags[first : close + 1]
                    valid = (
                        interval.coverage_status == "complete"
                        and close < len(flags)
                        and bool(supported)
                        and all(
                            row["value"] == 0 and row["quality"] == "measured" for row in supported
                        )
                    )
                    eligible.append(valid)
                    flag_available.append(
                        pd.Timestamp(
                            max(row["available_ms"] for row in supported), unit="ms", tz="UTC"
                        )
                        if valid
                        else pd.NaT
                    )
                result[key + "_eligible"] = eligible
                flag_times = pd.Series(
                    pd.to_datetime(pd.Index(flag_available), utc=True), index=index
                )
                result[key + "_available"] = pd.concat(
                    [result[key + "_available"], flag_times], axis=1
                ).max(axis=1)
        planes = self._learning_planes()
        keys = [plane.measurement_key for plane in planes]
        references = self._references()
        reference_values = pd.DataFrame(
            {key: result[key].where(result[key + "_eligible"]) for key in references}, index=index
        )
        result["reference_actual_w"] = (
            reference_values.sum(axis=1, min_count=len(references)) if references else np.nan
        )
        result["actual_w"] = result[keys].sum(axis=1, min_count=len(keys))
        result["available_at"] = result[[key + "_available" for key in (references or keys)]].max(
            axis=1
        )
        return result

    def _models(self, signature: str, issue: pd.Timestamp) -> dict[str, Any]:
        """Fit once per local day and persist parameters across server restarts."""
        store, settings = self.store(), self._settings
        midnight = issue.tz_convert(self.config.general.timezone).normalize()
        day = str(midnight.date())
        ready = issue >= midnight + pd.Timedelta(minutes=settings.training_delay_minutes)
        saved: dict[str, Any] = store.metadata("daily_models")
        fit_parameters = [
            2,
            settings.learning_days,
            settings.min_training_days,
            settings.validation_days,
            settings.min_validation_days,
            settings.min_validation_samples,
            settings.min_validation_improvement,
            settings.nowcast_enabled,
            settings.trend_enabled,
            settings.total_measurement_key,
            settings.total_curtailment_sensitive,
            settings.total_curtailment_key,
            settings.total_learning_guard_key,
            settings.total_learning_guard_max,
            settings.total_learning_guard_max_gap_seconds,
            [
                [
                    p.measurement_key,
                    p.correction_group,
                    p.curtailment_key,
                    p.learning_guard_key,
                    p.learning_guard_max,
                    p.learning_guard_max_gap_seconds,
                ]
                for p in self._learning_planes()
            ],
        ]
        if (
            saved
            and saved["signature"] == signature
            and saved.get("fit_parameters") == fit_parameters
            and (
                (saved["day"] == day and (saved.get("after_delay", False) or not ready))
                or (saved["day"] != day and not ready)
            )
        ):
            return saved
        observed = self._measurements(issue, settings.learning_days)
        store.resolve(signature, observed, issue)
        baselines = store.baselines(
            signature, issue - pd.Timedelta(days=settings.learning_days + 1), issue
        )
        saved = dict(
            signature=signature,
            day=day,
            fitted_at=issue.isoformat(),
            after_delay=ready,
            fit_parameters=fit_parameters,
            gains={},
        )
        for plane in self._learning_planes():
            key = plane.measurement_key
            raw = pd.Series(
                {
                    time: (
                        sum(values["plane_raw"].values())
                        if settings.total_measurement_key
                        else values["plane_raw"].get(key, np.nan)
                    )
                    for time, values in baselines
                },
                dtype=float,
            )
            history = pd.DataFrame(
                dict(
                    raw_power=raw.reindex(observed.index),
                    measured_power=observed[key].where(observed[key + "_eligible"]),
                    available_at=observed[key + "_available"],
                )
            )
            model = AdaptivePVModel(
                timezone=self.config.general.timezone,
                learning_days=settings.learning_days,
                min_days=settings.min_training_days,
                half_life_days=14,
                allow_partial_days=plane.curtailment_sensitive
                or bool(plane.curtailment_key or plane.learning_guard_key),
            )
            if not plane.curtailment_sensitive or plane.curtailment_key or plane.learning_guard_key:
                model.fit(history, issue)
            saved["gains"][key] = dict(
                gain=model.gain,
                shape=model.shape.tolist(),
                days=model.trained_days,
                curtailed=plane.curtailment_sensitive,
                curtailment_key=plane.curtailment_key,
                learning_guard_key=plane.learning_guard_key,
                learning_guard_max=plane.learning_guard_max,
                eligibility_policy=(
                    "configured_guard"
                    if plane.learning_guard_key
                    else "status_flag"
                    if plane.curtailment_key
                    else "disabled"
                    if plane.curtailment_sensitive
                    else "unrestricted"
                ),
                eligible_intervals=int(observed[key + "_eligible"].sum()),
            )
        trend_samples = store.training(signature, issue, settings.learning_days)
        if not trend_samples.empty:
            unrestricted_times = observed.index[observed.reference_actual_w.notna()]
            trend_samples = trend_samples[trend_samples.time.isin(unrestricted_times)]
        trend = LocalPVTrendModel(
            timezone=self.config.general.timezone,
            learning_days=settings.learning_days,
            min_days=settings.min_training_days,
            scale_features=True,
        ).fit(trend_samples, issue)
        saved["coefficients"] = {
            str(lead): values.tolist() for lead, values in trend.coefficients.items()
        }
        saved["training_counts"] = {
            str(lead): count for lead, count in trend.training_counts.items()
        }
        validation_rows = store.validation_rows(
            signature, midnight - pd.DateOffset(days=settings.validation_days), midnight
        )
        saved["selection"] = select_corrections(
            validation_rows,
            observed,
            self._groups(),
            issue,
            self.config.general.timezone,
            self._reference_group(),
            settings.validation_days,
            settings.min_validation_days,
            settings.min_validation_samples,
            settings.min_validation_improvement,
            ("calibrated",)
            + (("local",) if settings.nowcast_enabled else ())
            + (("trend",) if settings.nowcast_enabled and settings.trend_enabled else ()),
        )
        store.metadata("daily_models", saved)
        store.prune(issue, settings.archive_retention_days)
        return saved

    def _diagnostics(
        self,
        signature: str,
        issue: pd.Timestamp,
        models: dict[str, Any],
        observed: pd.DataFrame,
        corrected: pd.DataFrame,
    ) -> dict[str, Any]:
        """Report paired errors for identical issued leads, plus training/data health."""
        store, settings = self.store(), self._settings
        rows = store.metric_rows(
            signature,
            issue - pd.Timedelta(days=settings.diagnostics_days),
            issue,
        )
        metrics = []
        for lead in (15, 30, 60, 120):
            paired = [row for row in rows if int(np.ceil(row["lead_minutes"] / 15) * 15) == lead]
            for prefix, truth in (("reference", "reference_actual_w"), ("plant", "actual_w")):
                usable = [
                    row
                    for row in paired
                    if row[truth] is not None
                    and ((row[truth] or 0) > 50 or (row[prefix + "_raw_w"] or 0) > 50)
                ]
                for method in ("raw", "calibrated", "local", "adaptive"):
                    finite = [
                        row for row in usable if row[prefix + "_" + method + "_w"] is not None
                    ]
                    error = np.asarray(
                        [row[prefix + "_" + method + "_w"] - row[truth] for row in finite]
                    )
                    metrics.append(
                        dict(
                            horizon_minutes=lead,
                            scope=prefix,
                            method=method,
                            n=len(error),
                            mae_w=float(np.abs(error).mean()) if len(error) else None,
                            rmse_w=float(np.sqrt(np.square(error).mean())) if len(error) else None,
                            bias_w=float(error.mean()) if len(error) else None,
                        )
                    )
        valid = observed.reference_actual_w.notna()
        latest = observed.index[valid][-1] + pd.Timedelta(minutes=15) if valid.any() else None
        age = (issue - latest).total_seconds() / 60 if latest is not None else None
        ready = bool(models["coefficients"])
        night = float(corrected.solar_elevation.iloc[0]) <= 0 if len(corrected) else False
        state = (
            "reference_missing" if age is None or age > 30 else "active" if ready else "learning"
        )
        if night and state != "reference_missing":
            state = "night"
        messages = {
            "learning": "Lernphase: kalibrierte Wetterprognose aktiv; zusätzliche Korrekturen warten auf ausreichende Validierung.",
            "active": "Automatische Korrekturwahl aktiv."
            if settings.correction_mode == "auto"
            else f"PV-Korrekturmodus: {settings.correction_mode}.",
            "night": "Nacht: keine lokale Leistungskorrektur; Lernen und Datenempfang laufen weiter.",
            "reference_missing": "Referenzdaten fehlen oder sind zu alt; Wetterprognose aktiv.",
        }
        result = dict(
            signature=signature,
            updated_at=issue.isoformat(),
            observed_ms=store.milliseconds(issue),
            state=state,
            state_code={"learning": 1, "active": 2, "reference_missing": 3, "night": 4}[state],
            message=messages[state],
            model_fitted_at=models["fitted_at"],
            training_days=min(
                (
                    v["days"]
                    for v in models["gains"].values()
                    if not v["curtailed"] or v["days"] > 0
                ),
                default=0,
            ),
            minimum_training_days=settings.min_training_days,
            trained_leads=len(models["coefficients"]),
            training_counts=models["training_counts"],
            reference_age_minutes=age,
            references=self._references(),
            gains=models["gains"],
            coefficients=models["coefficients"],
            metrics=metrics,
            metrics_window_days=settings.diagnostics_days,
            completed_comparisons=len(rows),
            metrics_target_from_ms=min((row["target_ms"] for row in rows), default=None),
            metrics_target_until_ms=max((row["target_ms"] for row in rows), default=None),
            correction_mode=settings.correction_mode,
            correction_groups=self._groups(),
            selection=models.get("selection", {}),
            algorithm_version=2,
            metrics_algorithm_versions={
                str(version): sum(row["algorithm_version"] == version for row in rows)
                for version in {row["algorithm_version"] for row in rows}
            },
            training_delay_minutes=settings.training_delay_minutes,
            daily_metrics=store.daily_metrics(
                signature,
                issue.tz_convert(self.config.general.timezone).normalize()
                - pd.DateOffset(days=settings.diagnostics_days),
                issue,
                self.config.general.timezone,
            ),
            estimated_parameters=any(
                p.parameters_estimated for p in self.config.pvforecast.planes or []
            ),
            nominal_total_peakpower_kw=settings.nominal_total_peakpower_kw,
            notes="Reference errors exclude hybrid curtailment. Whole-plant errors include delivered production and curtailment. Configured guard thresholds express site assumptions, not detected curtailment. Increasing data does not guarantee monotonically decreasing error.",
        )
        store.metadata("status", result)
        return result

    async def _update_data(self, force_update: Optional[bool] = False) -> None:
        """Fetch outside the event loop; apply cached daily learning and safe fallback."""
        if not self.enabled():
            return
        planes = self.config.pvforecast.planes or []
        keys = self._plane_keys()
        if (
            not planes
            or (
                not self._settings.total_measurement_key
                and any(p.measurement_key is None for p in planes)
            )
            or len(keys) != len(set(keys))
        ):
            raise ValueError("Adaptive planes require distinct measurement keys.")
        if any(
            plane.nowcast_reference
            and plane.curtailment_sensitive
            and not (plane.curtailment_key or plane.learning_guard_key)
            for plane in self._learning_planes()
        ):
            raise ValueError("Curtailed hybrid planes cannot serve as weather references.")
        if self._settings.total_measurement_key in keys:
            raise ValueError("A total measurement key must be distinct from individual plane keys.")
        measured_keys = {p.measurement_key for p in self._learning_planes()}
        status_keys = {p.curtailment_key for p in self._learning_planes()} | {
            p.learning_guard_key for p in self._learning_planes()
        }
        if measured_keys & (status_keys - {None}):
            raise ValueError("Power and learning-status measurements must use distinct keys.")
        issue = pd.Timestamp(to_datetime(in_timezone=self.config.general.timezone))
        await self._sync_measurements(issue)
        store, signature = self.store(), self._signature()
        try:
            request_forecast = cast(Callable[..., dict[str, Any]], self._request_local_forecast)
            data = await asyncio.to_thread(request_forecast, force_update=bool(force_update))
            raw, sky = await asyncio.to_thread(self._physical_frames, data)
            future = raw.index >= issue.tz_convert("UTC").floor("15min")
            if not np.isfinite(raw.loc[future, keys].to_numpy()).all():
                raise ValueError("Future daylight radiation has gaps.")
        except Exception:
            if await self._holds_usable_forecast():
                logger.warning(
                    "Adaptive PV weather update failed; preserving usable previous forecast."
                )
                return
            raise
        models = await asyncio.to_thread(self._models, signature, issue)
        observed = await asyncio.to_thread(self._measurements, issue, 2)
        await asyncio.to_thread(store.resolve, signature, observed, issue)
        calibrated, clear = raw.copy(), sky.copy()
        calibrated_dc = pd.DataFrame(index=raw.index)
        for plane in self._learning_planes():
            key = plane.measurement_key
            model = AdaptivePVModel(timezone=self.config.general.timezone)
            model.gain = models["gains"][key]["gain"]
            model.shape = np.asarray(models["gains"][key]["shape"])
            corrected = model.predict(raw[key])
            corrected_sky = model.predict(sky[key])
            factor = (corrected / raw[key]).where(raw[key] > 0, model.gain)
            sky_factor = (corrected_sky / sky[key]).where(sky[key] > 0, model.gain)
            physical = keys if self._settings.total_measurement_key else [key]
            for physical_key in physical:
                calibrated[physical_key] = raw[physical_key] * factor
                clear[physical_key] = sky[physical_key] * sky_factor
                calibrated_dc[physical_key] = raw[physical_key + "_dc"] * factor
        for key, plane in zip(keys, planes):
            calibrated[key] = calibrated[key].clip(lower=0, upper=plane.inverter_paco)
            clear[key] = clear[key].clip(lower=0, upper=plane.inverter_paco)
        if self._settings.total_measurement_key:
            total_key = self._settings.total_measurement_key
            calibrated[total_key] = calibrated[keys].sum(axis=1, min_count=len(keys))
            clear[total_key] = clear[keys].sum(axis=1, min_count=len(keys))
        references = self._references()

        def reference_sum(frame: pd.DataFrame) -> pd.Series:
            return (
                frame[references].sum(axis=1, min_count=len(references))
                if references
                else pd.Series(np.nan, index=frame.index)
            )

        ref, ref_raw, ref_sky = reference_sum(calibrated), reference_sum(raw), reference_sum(clear)
        raw_power = raw[keys].sum(axis=1, min_count=len(keys))
        base = calibrated[keys].sum(axis=1, min_count=len(keys))
        history = pd.DataFrame(
            dict(
                calibrated_power=ref,
                clear_sky_power=ref_sky,
                measured_power=observed.reference_actual_w,
                available_at=observed.available_at,
            )
        )
        history["eligible"] = history.measured_power.notna()
        future_index = raw.index[future]
        features = local_trend_features(history, ref.reindex(future_index), ref_sky, issue)
        trend = LocalPVTrendModel(scale_features=True)
        trend.coefficients = {
            int(lead): np.asarray(values) for lead, values in models["coefficients"].items()
        }
        candidates = {
            method: calibrated.loc[future_index, keys].copy()
            for method in ("calibrated", "local", "trend")
        }
        dc_candidates = {
            method: calibrated_dc.loc[future_index, keys].copy() for method in candidates
        }
        chosen = candidates["calibrated"].copy()
        chosen_dc = dc_candidates["calibrated"].copy()
        for group, learning_keys in self._groups().items():
            group_keys = keys if self._settings.total_measurement_key else learning_keys
            group_base = calibrated.loc[future_index, group_keys].sum(
                axis=1, min_count=len(group_keys)
            )
            group_sky = clear[group_keys].sum(axis=1, min_count=len(group_keys))
            group_local = group_base.copy()
            if self._settings.nowcast_enabled:
                group_local = apply_local_nowcast(group_base, group_sky, history, issue)
            group_trend = group_local.copy()
            if self._settings.trend_enabled and self._settings.nowcast_enabled:
                group_trend = trend.predict(group_base, group_sky, features, issue, group_local)
            for method, curve in (("local", group_local), ("trend", group_trend)):
                delta = curve - group_base
                for key in group_keys:
                    share = (clear.loc[future_index, key] / group_sky.reindex(future_index)).fillna(
                        0
                    )
                    candidates[method][key] = (candidates["calibrated"][key] + delta * share).clip(
                        lower=0
                    )
                    dc_candidates[method][key] = (
                        dc_candidates["calibrated"][key]
                        + delta * share / self._settings.inverter_efficiency
                    ).clip(lower=0)
            unknown_curtailment = any(
                p.curtailment_sensitive and not (p.curtailment_key or p.learning_guard_key)
                for p in self._learning_planes()
                if p.measurement_key in learning_keys
            )
            mode = "calibrated" if unknown_curtailment else self._settings.correction_mode
            if not self._settings.nowcast_enabled:
                mode = "calibrated"
            elif mode == "trend" and not self._settings.trend_enabled:
                mode = "local"
            for key in group_keys:
                paco = planes[keys.index(key)].inverter_paco
                for method in candidates:
                    candidates[method][key] = candidates[method][key].clip(upper=paco)
                chosen[key] = choose_correction(
                    candidates["calibrated"][key],
                    candidates["local"][key],
                    candidates["trend"][key],
                    issue,
                    models["selection"].get(group, {}),
                    mode,
                )
                chosen_dc[key] = choose_correction(
                    dc_candidates["calibrated"][key],
                    dc_candidates["local"][key],
                    dc_candidates["trend"][key],
                    issue,
                    models["selection"].get(group, {}),
                    mode,
                )
        capacity = self._settings.max_ac_power_w or self._installed_ac_capacity_w()

        def limit(frame: pd.DataFrame) -> pd.DataFrame:
            total = frame.sum(axis=1, min_count=len(keys))
            factor = (
                (capacity / total).clip(upper=1).fillna(1)
                if capacity > 0
                else pd.Series(1.0, index=frame.index)
            )
            return frame.mul(factor, axis=0)

        candidates = {method: limit(frame) for method, frame in candidates.items()}
        chosen = limit(chosen)
        adaptive = chosen.sum(axis=1, min_count=len(keys))
        local = candidates["local"].sum(axis=1, min_count=len(keys))
        base = candidates["calibrated"].sum(axis=1, min_count=len(keys))
        ref_keys = keys if self._settings.total_measurement_key and references else references
        ref_local = (
            candidates["local"][ref_keys].sum(axis=1, min_count=len(ref_keys))
            if ref_keys
            else pd.Series(np.nan, index=future_index)
        )
        ref_adaptive = (
            chosen[ref_keys].sum(axis=1, min_count=len(ref_keys))
            if ref_keys
            else pd.Series(np.nan, index=future_index)
        )
        diagnostic_frame = pd.DataFrame(
            dict(solar_elevation=raw.solar_elevation.reindex(future_index))
        )
        status = await asyncio.to_thread(
            self._diagnostics, signature, issue, models, observed, diagnostic_frame
        )
        snapshots = []
        for timestamp in future_index:
            power = float(adaptive.loc[timestamp])
            values: dict[str, Any] = dict(
                plant_raw_w=float(raw_power.loc[timestamp]),
                plant_calibrated_w=float(base.loc[timestamp]),
                plant_local_w=float(local.loc[timestamp]),
                plant_adaptive_w=power,
                reference_raw_w=float(ref_raw.loc[timestamp]),
                reference_calibrated_w=float(ref.loc[timestamp]),
                reference_local_w=float(ref_local.loc[timestamp]),
                reference_adaptive_w=float(ref_adaptive.loc[timestamp]),
                reference_sky_w=float(ref_sky.loc[timestamp]),
                plane_raw={key: float(raw.loc[timestamp, key]) for key in keys},
                algorithm_version=2,
                groups={
                    group: {
                        **{
                            method: float(
                                frame.loc[
                                    timestamp,
                                    keys if self._settings.total_measurement_key else group_keys,
                                ].sum()
                            )
                            for method, frame in candidates.items()
                        },
                        "selected": float(
                            chosen.loc[
                                timestamp,
                                keys if self._settings.total_measurement_key else group_keys,
                            ].sum()
                        ),
                    }
                    for group, group_keys in self._groups().items()
                },
                features=features.loc[timestamp].to_dict()
                if pd.notna(features.loc[timestamp, "level"])
                else None,
            )
            snapshots.append((timestamp, values))
            await self.update_value(
                to_datetime(timestamp.to_pydatetime()),
                {
                    "pvforecast_ac_power": round(power, 1),
                    "pvforecast_dc_power": round(float(chosen_dc.loc[timestamp].sum()), 1),
                    "pvforecast_adaptive_raw_ac_power": round(values["plant_raw_w"], 1),
                    "pvforecast_adaptive_calibrated_ac_power": round(
                        values["plant_calibrated_w"], 1
                    ),
                    "pvforecast_adaptive_nowcast_delta": round(
                        power - values["plant_calibrated_w"], 1
                    ),
                    "pvforecast_adaptive_reference_power": round(values["reference_adaptive_w"], 1),
                    "pvforecast_adaptive_training_days": status["training_days"],
                    "pvforecast_adaptive_training_samples": min(
                        models["training_counts"].values(), default=0
                    ),
                    "pvforecast_adaptive_state_code": status["state_code"],
                    "pvforecast_adaptive_weather_weight": float(
                        raw.loc[timestamp, "weather_weight"]
                    ),
                },
            )
        await asyncio.to_thread(
            store.remember,
            signature,
            pd.Timestamp(to_datetime(in_timezone=self.config.general.timezone)),
            snapshots,
            self.config.general.timezone,
        )
        self.update_datetime = to_datetime(issue.to_pydatetime())
        logger.info(
            "Adaptive PV: {}; {} training days; {} trained leads",
            status["state"],
            status["training_days"],
            status["trained_leads"],
        )
