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
                plane.model_dump(mode="json", exclude={"name", "parameters_estimated"})
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
        return hashlib.sha256(PVLearningStore.dump(payload).encode()).hexdigest()

    async def _sync_measurements(self, issue: pd.Timestamp) -> None:
        """Accept normal EOS power channels as well as bounded direct ingestion."""
        keys = {plane.measurement_key for plane in self.config.pvforecast.planes or []}
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
                _, power = self._plane_power(plane, source, solar, extra, airmass)
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
                    power = power * (
                        fraction * (basis @ response[:45]) + (1 - fraction) * response[45]
                    )
                valid = source[list(OPENMETEO_VARIABLES[3:])].notna().all(
                    axis=1
                ) | solar.apparent_elevation.le(0)
                frame[plane.measurement_key or str(number)] = power.where(valid)
            frame.index = pd.DatetimeIndex(frame.index) - pd.Timedelta(minutes=15)
            frame["weather_weight"] = weather.regional_weight.to_numpy()
            frame["solar_elevation"] = solar.apparent_elevation.to_numpy()
            frames.append(frame)
        return frames

    def _measurements(self, cutoff: pd.Timestamp, days: int = 2) -> pd.DataFrame:
        """Time-weighted complete intervals, closed by actually available samples."""
        start = (cutoff - pd.Timedelta(days=days)).tz_convert("UTC").floor("15min")
        end = cutoff.tz_convert("UTC").floor("15min")
        index = pd.date_range(start, end, freq="15min", inclusive="left")
        result = pd.DataFrame(index=index)
        store = self.store()
        for plane in self.config.pvforecast.planes or []:
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
        planes = self.config.pvforecast.planes or []
        keys = [plane.measurement_key for plane in planes]
        references = [plane.measurement_key for plane in planes if plane.nowcast_reference]
        if not references:
            raise ValueError("At least one independent reference plane is required.")
        result["reference_actual_w"] = result[references].sum(axis=1, min_count=len(references))
        result["actual_w"] = result[keys].sum(axis=1, min_count=len(keys))
        result["available_at"] = result[[key + "_available" for key in references]].max(axis=1)
        return result

    def _models(self, signature: str, issue: pd.Timestamp) -> dict[str, Any]:
        """Fit once per local day and persist parameters across server restarts."""
        store, settings = self.store(), self._settings
        day = str(issue.tz_convert(self.config.general.timezone).date())
        saved: dict[str, Any] = store.metadata("daily_models")
        fit_parameters = [settings.learning_days, settings.min_training_days]
        if (
            saved
            and saved["signature"] == signature
            and saved["day"] == day
            and saved.get("fit_parameters") == fit_parameters
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
            fit_parameters=fit_parameters,
            gains={},
        )
        for plane in self.config.pvforecast.planes or []:
            key = plane.measurement_key
            raw = pd.Series(
                {time: values["plane_raw"].get(key, np.nan) for time, values in baselines},
                dtype=float,
            )
            history = pd.DataFrame(
                dict(
                    raw_power=raw.reindex(observed.index),
                    measured_power=observed[key],
                    available_at=observed[key + "_available"],
                )
            )
            model = AdaptivePVModel(
                timezone=self.config.general.timezone,
                learning_days=settings.learning_days,
                min_days=settings.min_training_days,
                half_life_days=14,
            )
            if not plane.curtailment_sensitive:
                model.fit(history, issue)
            saved["gains"][key] = dict(
                gain=model.gain,
                shape=model.shape.tolist(),
                days=model.trained_days,
                curtailed=plane.curtailment_sensitive,
            )
        trend = LocalPVTrendModel(
            timezone=self.config.general.timezone,
            learning_days=settings.learning_days,
            min_days=settings.min_training_days,
            scale_features=True,
        ).fit(store.training(signature, issue, settings.learning_days), issue)
        saved["coefficients"] = {
            str(lead): values.tolist() for lead, values in trend.coefficients.items()
        }
        saved["training_counts"] = {
            str(lead): count for lead, count in trend.training_counts.items()
        }
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
        rows = store.comparisons(
            signature,
            store.milliseconds(issue - pd.Timedelta(days=settings.diagnostics_days)),
            store.milliseconds(issue),
            limit=20000,
        )
        metrics = []
        for lead in (15, 30, 60, 120):
            paired = [
                row
                for row in rows
                if int(np.ceil(row["lead_minutes"] / 15) * 15) == lead
                and (row["reference_actual_w"] > 50 or row["reference_raw_w"] > 50)
            ]
            for prefix, truth in (("reference", "reference_actual_w"), ("plant", "actual_w")):
                usable = [row for row in paired if row[truth] is not None]
                for method in ("raw", "calibrated", "local", "adaptive"):
                    error = np.asarray(
                        [row[prefix + "_" + method + "_w"] - row[truth] for row in usable]
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
            "learning": "Lernphase: bisherige lokale Korrektur aktiv; mindestens fünf geeignete Tage nötig.",
            "active": "Lernende lokale Prognose aktiv.",
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
                (v["days"] for v in models["gains"].values() if not v["curtailed"]), default=0
            ),
            minimum_training_days=settings.min_training_days,
            trained_leads=len(models["coefficients"]),
            training_counts=models["training_counts"],
            reference_age_minutes=age,
            references=[
                p.measurement_key
                for p in self.config.pvforecast.planes or []
                if p.nowcast_reference
            ],
            gains=models["gains"],
            coefficients=models["coefficients"],
            metrics=metrics,
            metrics_window_days=settings.diagnostics_days,
            completed_comparisons=len(rows),
            estimated_parameters=any(
                p.parameters_estimated for p in self.config.pvforecast.planes or []
            ),
            nominal_total_peakpower_kw=settings.nominal_total_peakpower_kw,
            notes="Reference errors exclude hybrid curtailment. Whole-plant errors include delivered production and curtailment. Increasing data does not guarantee monotonically decreasing error.",
        )
        store.metadata("status", result)
        return result

    async def _update_data(self, force_update: Optional[bool] = False) -> None:
        """Fetch outside the event loop; apply cached daily learning and safe fallback."""
        if not self.enabled():
            return
        planes = self.config.pvforecast.planes or []
        keys = [plane.measurement_key for plane in planes]
        if not planes or None in keys or len(keys) != len(set(keys)):
            raise ValueError("Adaptive planes require distinct measurement keys.")
        if any(plane.nowcast_reference and plane.curtailment_sensitive for plane in planes):
            raise ValueError("Curtailed hybrid planes cannot serve as weather references.")
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
        for key in keys:
            model = AdaptivePVModel(timezone=self.config.general.timezone)
            model.gain = models["gains"][key]["gain"]
            model.shape = np.asarray(models["gains"][key]["shape"])
            calibrated[key] = model.predict(raw[key])
            clear[key] = model.predict(sky[key])
        references = [plane.measurement_key for plane in planes if plane.nowcast_reference]
        ref = calibrated[references].sum(axis=1, min_count=len(references))
        ref_raw = raw[references].sum(axis=1, min_count=len(references))
        ref_sky = clear[references].sum(axis=1, min_count=len(references))
        raw_power = raw[keys].sum(axis=1, min_count=len(keys))
        base = calibrated[keys].sum(axis=1, min_count=len(keys))
        envelope = clear[keys].sum(axis=1, min_count=len(keys))
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
        local = base.reindex(future_index)
        ref_local = ref.reindex(future_index)
        if self._settings.nowcast_enabled:
            local = apply_local_nowcast(local, envelope, history, issue)
            ref_local = apply_local_nowcast(ref_local, ref_sky, history, issue)
        features = local_trend_features(history, ref.reindex(future_index), ref_sky, issue)
        trend = LocalPVTrendModel(scale_features=True)
        trend.coefficients = {
            int(lead): np.asarray(values) for lead, values in models["coefficients"].items()
        }
        adaptive, ref_adaptive = local, ref_local
        if self._settings.trend_enabled and self._settings.nowcast_enabled:
            adaptive = trend.predict(base.reindex(future_index), envelope, features, issue, local)
            ref_adaptive = trend.predict(
                ref.reindex(future_index), ref_sky, features, issue, ref_local
            )
        capacity = self._settings.max_ac_power_w or self._installed_ac_capacity_w()
        if capacity > 0:
            adaptive = adaptive.clip(upper=capacity)
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
                features=features.loc[timestamp].to_dict()
                if pd.notna(features.loc[timestamp, "level"])
                else None,
            )
            snapshots.append((timestamp, values))
            await self.update_value(
                to_datetime(timestamp.to_pydatetime()),
                {
                    "pvforecast_ac_power": round(power, 1),
                    "pvforecast_dc_power": round(power / self._settings.inverter_efficiency, 1),
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
