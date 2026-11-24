"""Runtime control for ``ElecPriceAkkudoktorML``: cache, monthly training, error history, forecast.

Slow work (data download, monthly training, building the error history) runs in a low-priority
background thread. The forecast path (``forecast``) is fast and does not block the optimization run.
If something is not ready, ``forecast`` returns ``None`` together with a reason and the provider
falls back to the seasonal ETS forecast.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import gc
import hashlib
import importlib.metadata
import json
import os
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator, Optional

import numpy as np
import pandas as pd
from loguru import logger

from akkudoktoreos.prediction import elecpriceakkudoktormlcore as core
from akkudoktoreos.prediction import elecpriceakkudoktormldata as data

# SMARD measurements and weather are refreshed together: the background work starts when the weather
# is older than this (the measurements only reach the model with a delay anyway).
DATA_REFRESH_S = 6 * 3600
# Increase when the stored model structure changes (old model files are then replaced).
MODEL_SCHEMA = 2  # 2: weather point ids renamed (feature names are part of the model)
# Stored monthly models kept besides the current month.
KEEP_MODEL_MONTHS = 2
BACKOFF_S = 1800  # do not fetch again for this long after errors (e.g. HTTP 429)


@dataclass
class ForecastOutcome:
    """Result of the fast forecast path."""

    forecast: core.DayForecast
    model_month: dt.date
    quantiles: Optional[np.ndarray]  # Q x H (10/25/50/75/90 %) or None
    uncertainty_reason: Optional[str]


@contextmanager
def _low_priority() -> Iterator[None]:
    """Lower the priority of the background thread (Linux).

    Deliberately no thread-pool control via the library of the boosting dependencies: its
    `dl_iterate_phdr` holds the loader lock and needs the GIL in its callback; a concurrent `dlopen`
    (e.g. the first sklearn import in another thread) then blocks the whole process (observed in a
    live deployment).
    """
    with contextlib.suppress(Exception):  # lowering the priority is best effort
        if hasattr(os, "setpriority") and hasattr(os, "PRIO_PROCESS"):
            os.setpriority(os.PRIO_PROCESS, threading.get_native_id(), 10)
    yield


class AkkudoktorMLEngine:
    """Holds cache, models and error history of one data folder (one object per folder)."""

    def __init__(
        self,
        folder: Path,
        get: data.Getter = data.http_get,
        clock: Callable[[], pd.Timestamp] = lambda: pd.Timestamp.now(tz="UTC"),
    ) -> None:
        self.folder = Path(folder)
        self.cache = data.DataCache(self.folder)
        self.get, self.clock = get, clock
        self.stage_a = core.StageA()
        self.curve = core.PriceCurve()
        self.records: dict[str, dict] = {}
        self._smard: Optional[pd.DataFrame] = None
        self._weather: Optional[dict[int, pd.DataFrame]] = None
        self._smard_at = self._weather_at = 0.0
        self._backoff_until = 0.0
        self._lock = threading.RLock()
        self._thread: Optional[threading.Thread] = None
        self.status = "new"
        self.last_error: Optional[str] = None
        self._loaded = False
        self._result_cache: Optional[tuple[tuple, ForecastOutcome]] = (
            None  # last complete result with its input key
        )

    # ------------------------------------------------------------------ state on disk
    @staticmethod
    def _model_tag() -> str:
        """Version tag of stored models: scikit-learn version and model schema."""
        return f"sk{importlib.metadata.version('scikit-learn')}_s{MODEL_SCHEMA}"

    def _models_path(self, m0: dt.date) -> Path:
        return self.folder / f"stage_a_{m0:%Y-%m}_{self._model_tag()}.joblib"

    def _prune_models(self, current: dt.date) -> None:
        """Delete stored models that are not needed any more.

        Models of other scikit-learn versions or model schemas would never be loaded. Models older
        than
        ``KEEP_MODEL_MONTHS`` months before the current model month are not needed either (a new
        month is always
        trained on all data known at its start).
        """
        suffix = f"_{self._model_tag()}.joblib"
        oldest = current.replace(day=1)
        for _ in range(KEEP_MODEL_MONTHS):
            oldest = (oldest - dt.timedelta(days=1)).replace(day=1)
        for p in self.folder.glob("stage_a_*.joblib"):
            try:
                month = dt.date.fromisoformat(p.name[len("stage_a_") : len("stage_a_") + 7] + "-01")
            except ValueError:
                month = None
            if not p.name.endswith(suffix) or month is None or month < oldest:
                with contextlib.suppress(OSError):
                    p.unlink()

    def _records_path(self) -> Path:
        return self.folder / "error_records.json"

    def _load_disk(self) -> None:
        with self._lock:
            if self._loaded:
                return
            self._loaded = True
            self._smard = self.cache.load_smard()
            self._weather = self.cache.load_weather()
            p = self._records_path()
            if p.exists():
                try:
                    self.records = json.loads(p.read_text(encoding="utf-8"))
                except Exception as exc:
                    logger.warning(
                        "AkkudoktorML: error history not readable ({}), rebuilding it", exc
                    )
                    self.records = {}
            self._smard_at = self._weather_at = 0.0

    def _load_models(self, m0: dt.date, stage: Optional[core.StageA] = None) -> bool:
        """Make the stored model of month ``m0`` available in ``stage`` (default: shared)."""
        target = stage if stage is not None else self.stage_a
        with self._lock:
            if target.has_month(m0):
                return True
        p = self._models_path(m0)
        if not p.exists():
            return False
        try:
            import joblib  # type: ignore[import-untyped]  # no stubs shipped

            state = joblib.load(p)
            with self._lock:
                target.import_month(state)
                return target.has_month(m0)
        except Exception as exc:
            logger.warning(
                "AkkudoktorML: stored model {} not readable ({}), deleting it and retraining",
                p.name,
                exc,
            )
            with contextlib.suppress(OSError):
                p.unlink()
            return False

    def _save_models(self, m0: dt.date, stage: core.StageA) -> None:
        import joblib

        tmp = self._models_path(m0).with_suffix(".tmp")
        joblib.dump(stage.export_month(m0), tmp)
        tmp.replace(self._models_path(m0))
        self._prune_models(m0)

    # ------------------------------------------------------------------ inputs
    def inputs(self, price_override_ct: Optional[pd.Series] = None) -> Optional[core.Inputs]:
        with self._lock:
            smard, weather = self._smard, self._weather
        if smard is None or weather is None:
            return None
        return data.to_inputs(smard, weather, price_override_ct)

    def _ensure_month(
        self, inputs: core.Inputs, m0: dt.date, stage: Optional[core.StageA] = None
    ) -> None:
        """Load or train the model of month ``m0`` into ``stage`` (default: the shared one).

        Training happens in a private object without holding the lock; the result is published
        afterwards.
        """
        target = stage if stage is not None else self.stage_a
        if self._load_models(m0, target):
            return
        t0 = time.time()
        logger.info("AkkudoktorML: training stage A models for {:%Y-%m}", m0)
        work = core.StageA()
        with _low_priority():
            work.train_month(inputs, m0)
        self._save_models(m0, work)
        with self._lock:
            target.import_month(work.export_month(m0))
        logger.info("AkkudoktorML: training {:%Y-%m} finished in {:.0f} s", m0, time.time() - t0)

    # ------------------------------------------------------------------ background work
    def kick(self, local_day: dt.date, model_day: Optional[dt.date] = None) -> None:
        """Start the background work (unless already active); returns immediately.

        ``model_day``: the issue day the forecast path needs (decides which monthly model is
        secured); default
        the day before ``local_day``.
        """
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            if time.time() < self._backoff_until:
                return
            self._thread = threading.Thread(
                target=self._work,
                args=(local_day, model_day),
                name="elecprice-akkudoktorml",
                daemon=True,
            )
            self._thread.start()

    def _work(self, local_day: dt.date, model_day: Optional[dt.date] = None) -> None:
        try:
            self.run_once(local_day, model_day)
            self.status = "ready"
            self.last_error = None
        except data.RateLimited as exc:
            self._backoff_until = time.time() + BACKOFF_S
            self.last_error = f"{exc}; next attempt in {BACKOFF_S // 60} min at the earliest"
            self.status = "waiting (rate limit)"
            logger.warning("AkkudoktorML: {}", self.last_error)
        except Exception as exc:
            self._backoff_until = time.time() + BACKOFF_S
            self.last_error = f"{type(exc).__name__}: {exc}"
            self.status = "error"
            logger.warning(
                "AkkudoktorML: background work failed ({}); falling back to the seasonal ETS "
                "forecast",
                self.last_error,
            )

    def run_once(self, local_day: dt.date, model_day: Optional[dt.date] = None) -> None:
        """Update data, secure the monthly model, extend the error history (blocking)."""
        now = time.time()
        with _low_priority():
            self._load_disk()
            with self._lock:
                smard, weather = self._smard, self._weather
            if smard is None or now - self._smard_at > DATA_REFRESH_S:
                self.status = "loading SMARD data"
                smard = self.cache.update_smard(self.get)
                with self._lock:
                    self._smard, self._smard_at = smard, time.time()
            if weather is None or time.time() - self._weather_at > DATA_REFRESH_S:
                self.status = "loading weather data"
                weather = self.cache.update_weather(self.get, self.clock().date())
                with self._lock:
                    self._weather, self._weather_at = weather, time.time()
        inputs = self.inputs()
        if inputs is None:
            raise RuntimeError("input data not available after the refresh")
        self.status = "training"
        # the month of the day the forecast path needs (on the 1st this is the new month as soon as
        # its model day is reached); the exact day is only fixed in the forecast path
        d_model = model_day if model_day is not None else local_day - dt.timedelta(days=1)
        self._ensure_month(inputs, core.month_start(d_model))
        self.status = "building error history"
        self.extend_records(inputs, d_model)

    def extend_records(self, inputs: core.Inputs, day: dt.date) -> int:
        """Extend the error history for all issue days D' in the window that are usable at ``day``.

        Works on private copies of models, price curves and error history and publishes the result
        under the lock at the end, so a concurrent forecast never sees half-built state.
        """
        with self._lock:
            records = dict(self.records)
            curve = core.PriceCurve()
            curve.curves = dict(self.curve.curves)
        stage = core.StageA()
        added = 0
        last = day - dt.timedelta(days=1 + max(core.TARGET_DAYS))
        first = day - dt.timedelta(days=core.WINDOW_DAYS)
        d = first
        while d <= last:
            key = d.isoformat()
            if key not in records:
                m0 = core.month_start(d)
                stage.drop_months_before(
                    m0
                )  # days run in ascending order: older monthly models are no longer needed
                self._ensure_month(inputs, m0, stage)
                k = core.build_knowledge(inputs, d)
                fc = core.forecast_day(inputs, k, stage, curve)
                rec = core.error_record(inputs, curve, fc)
                if rec is not None:
                    records[key] = rec
                    added += 1
            d += dt.timedelta(days=1)
        del stage
        gc.collect()
        with self._lock:
            # memory: keep only the model of the month the forecast needs, but never drop the bridge
            # month while that model is not yet available
            m_day = core.month_start(day)
            if self.stage_a.has_month(m_day):
                self.stage_a.drop_months_before(m_day)
            if added:
                cutoff = (day - dt.timedelta(days=core.WINDOW_DAYS + 30)).isoformat()
                self.records = {k: v for k, v in records.items() if k >= cutoff}
                self.curve.curves.update(curve.curves)
                self.curve.drop_weeks_before(core.week_start(first) - dt.timedelta(days=7))
                tmp = self._records_path().with_suffix(".tmp")
                tmp.write_text(json.dumps(self.records), encoding="utf-8")
                tmp.replace(self._records_path())
        return added

    # ------------------------------------------------------------------ fast forecast path
    def forecast(
        self, day: dt.date, known_price_ct: pd.Series, local_today: dt.date
    ) -> tuple[Optional[ForecastOutcome], str]:
        """Forecast for issue day ``day`` (D): (result, reason); result ``None`` = not ready."""
        with self._lock:
            if not self._loaded:
                self._load_disk()
            inputs = self.inputs(known_price_ct)
            if inputs is None:
                self.kick(local_today, day)
                return None, "data cache is being built"
            if self._weather_at == 0.0 or time.time() - self._weather_at > DATA_REFRESH_S:
                self.kick(
                    local_today, day
                )  # weather increment in the background; the existing data is usually sufficient
            m0 = core.month_start(day)
            use = m0
            if not self._load_models(m0):
                prev = (m0 - dt.timedelta(days=1)).replace(day=1)
                self.kick(local_today, day)
                if not self._load_models(prev):
                    return None, f"stage A model {m0:%Y-%m} is being trained"
                use = prev
            k = core.build_knowledge(inputs, day)
            for n in core.TARGET_DAYS:
                w = k.weather[n + 1].reindex(k.target_hours(n))
                if int(w.notna().all(axis=1).sum()) < 20:
                    self.kick(local_today, day)
                    return None, f"weather forecast for day D+{n} is missing in the cache"
            # EOS calls update_data several times per planning cycle; without new inputs (known
            # prices, weather state, error history, model month) the result is identical -> do not
            # recompute (the load would otherwise slow down the planning run).
            key = (
                day,
                use,
                len(known_price_ct),
                str(known_price_ct.index.max()),
                hashlib.sha256(known_price_ct.to_numpy().tobytes()).hexdigest(),
                self._weather_at,
                len(self.records),
                max(self.records) if self.records else "",
            )
            if self._result_cache is not None and self._result_cache[0] == key:
                return self._result_cache[1], "ok"
            fc = self._day_forecast(inputs, k, use)
            q, reason = None, None
            try:
                scen = core.residual_scenarios(inputs, k, self.curve, fc, self.records)
                if np.isfinite(scen).all():
                    q = core.scenario_quantiles(scen)
                else:
                    reason = "scenarios contain missing values"
            except ValueError as exc:
                reason = str(exc)
                self.kick(local_today, day)  # fill up the error history in the background
            outcome = ForecastOutcome(fc, use, q, reason)
            if reason is None:  # do not keep incomplete results (too few error days)
                self._result_cache = (key, outcome)
            return outcome, "ok"

    def _day_forecast(
        self, inputs: core.Inputs, k: core.Knowledge, month: dt.date
    ) -> core.DayForecast:
        return core.forecast_day(inputs, k, self.stage_a, self.curve, month=month)
