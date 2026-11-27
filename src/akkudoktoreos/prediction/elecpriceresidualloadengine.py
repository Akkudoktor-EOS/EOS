"""Betriebssteuerung für ``ElecPriceResidualLoad``: Cache, Monatstraining, Fehlerhistorie, Prognose.

Schwere oder langsame Arbeit (Datenabruf, Monatstraining, Aufbau der Fehlerhistorie) läuft in einem
Hintergrund-Thread mit niedriger Priorität; der Prognosepfad (``forecast``) ist schnell und blockiert den
Optimierungslauf nicht. Ist etwas nicht bereit, liefert ``forecast`` ``None`` samt Grund; der Provider fällt dann auf
das bisherige ETS-Verhalten zurück.
"""

from __future__ import annotations

import datetime as dt
import gc
import hashlib
import json
import os
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import pandas as pd
from loguru import logger

from akkudoktoreos.prediction import elecpricecalibration as calibration
from akkudoktoreos.prediction import elecpriceresidualloadcore as core
from akkudoktoreos.prediction import elecpriceresidualloaddata as data
from akkudoktoreos.prediction import elecpricerevisionlive as revisionlive

BASIS_VERSION = "pv_nonnegative_u3_v1"

SMARD_REFRESH_S = 3 * 3600
WEATHER_REFRESH_S = 6 * 3600
BACKOFF_S = 1800  # nach Fehlern (u. a. HTTP 429) so lange nicht erneut abrufen


@dataclass
class ForecastOutcome:
    """Ergebnis des schnellen Prognosepfads."""

    forecast: core.DayForecast
    model_month: dt.date
    quantiles: Optional[np.ndarray]  # Q x H (10/25/50/75/90 %) oder None
    uncertainty_reason: Optional[str]
    calibration: Optional[dict] = None
    revision: Optional[dict] = None


@contextmanager
def _low_priority() -> Iterator[None]:
    """Niedrige Priorität für den Hintergrund-Thread (Linux).

    Bewusst keine Thread-Pool-Steuerung über die Bibliothek der Boosting-Abhängigkeiten: deren `dl_iterate_phdr`
    hält die Lader-Sperre und braucht im Callback den GIL; ein gleichzeitiger `dlopen` (z. B. erster sklearn-Import in
    einem anderen Thread) blockiert dann den ganzen Prozess (am 13.11.2026 beim ersten Live-Versuch beobachtet).
    """
    try:
        if hasattr(os, "setpriority") and hasattr(os, "PRIO_PROCESS"):
            os.setpriority(os.PRIO_PROCESS, threading.get_native_id(), 10)
    except OSError as exc:
        logger.debug("Priority of the price model thread not lowered: {}", exc)
    yield


class ResidualLoadEngine:
    """Hält Cache, Modelle und Fehlerhistorie eines Datenordners (ein Objekt je Ordner)."""

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
        self._calibration_archive = calibration.new_archive(BASIS_VERSION)
        self._calibration_archive_dirty = False
        self._calibration_policy: Optional[dict] = None
        self._smard: Optional[pd.DataFrame] = None
        self._weather: Optional[dict[int, pd.DataFrame]] = None
        self._smard_at = self._weather_at = 0.0
        self._backoff_until = 0.0
        self._lock = threading.RLock()
        self._thread: Optional[threading.Thread] = None
        self.status = "neu"
        self.last_error: Optional[str] = None
        self._loaded = False
        self._result_cache: Optional[tuple[tuple, ForecastOutcome]] = (
            None  # letztes vollständiges Ergebnis samt Eingangsschlüssel
        )

    # ------------------------------------------------------------------ Zustand auf Platte
    def _models_path(self, m0: dt.date) -> Path:
        return self.folder / f"stage_a_{m0:%Y-%m}.joblib"

    def _records_path(self) -> Path:
        return self.folder / "error_records_pv_nonnegative_v1.json"

    def _calibration_path(self) -> Path:
        return self.folder / "calibration_raw_pv_nonnegative_v1.json"

    def _load_calibration(self) -> None:
        try:
            path = self._calibration_path()
            if path.exists():
                archived = json.loads(path.read_text(encoding="utf-8"))
                if (
                    archived.get("schema_version") != calibration.SCHEMA_VERSION
                    or archived.get("basis_version") != BASIS_VERSION
                    or not isinstance(archived.get("entries"), dict)
                ):
                    raise ValueError("calibration archive basis differs")
                self._calibration_archive = archived
            policy_path = self.folder / "calibration_policy_pv_nonnegative_v1.json"
            if policy_path.exists():
                policy = json.loads(policy_path.read_text(encoding="utf-8"))
                if (
                    policy.get("basis_version") != BASIS_VERSION
                    or policy.get("mode") not in ("additive", "normalized")
                    or policy.get("window_days") not in (60, 120)
                    or not isinstance(policy.get("outward_only", False), bool)
                ):
                    raise ValueError("calibration policy differs")
                self._calibration_policy = policy
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            self._calibration_policy = None
            logger.warning(
                "ResidualLoad: calibration unavailable ({}); raw U3 retained", type(exc).__name__
            )

    def _calibrate(
        self, raw: np.ndarray, inputs: core.Inputs, fc: core.DayForecast, month: dt.date
    ) -> tuple[np.ndarray, dict]:
        """Archive raw quantiles once per issue day; calibrate only matured issues."""
        try:
            added = calibration.archive_raw(
                self._calibration_archive,
                raw.tolist(),
                list(fc.point.index),
                fc.day,
                basis_version=BASIS_VERSION,
                model_month=month.strftime("%Y-%m"),
                issued_at=self.clock().isoformat(),
                origin="live",
            )
            if added:
                self._calibration_archive_dirty = True
            if self._calibration_archive_dirty:
                cutoff = (fc.day - dt.timedelta(days=150)).isoformat()
                self._calibration_archive["entries"] = {
                    key: value
                    for key, value in self._calibration_archive["entries"].items()
                    if key >= cutoff
                }
                self.folder.mkdir(parents=True, exist_ok=True)
                path = self._calibration_path()
                tmp = path.with_suffix(".tmp")
                tmp.write_text(
                    json.dumps(self._calibration_archive, allow_nan=False), encoding="utf-8"
                )
                tmp.replace(path)
                self._calibration_archive_dirty = False
            if self._calibration_policy is None:
                return raw, {"status": "unconfigured", "basis_version": BASIS_VERSION}
            corrected, diagnostic = calibration.apply(
                raw.tolist(),
                list(fc.point.index),
                fc.day,
                self._calibration_archive,
                inputs.price.dropna().to_dict(),
                basis_version=BASIS_VERSION,
                window_days=self._calibration_policy["window_days"],
                mode=self._calibration_policy["mode"],
                outward_only=self._calibration_policy.get("outward_only", False),
            )
            diagnostic["basis_version"] = BASIS_VERSION
            return np.asarray(corrected, dtype=float), diagnostic
        except (OSError, ValueError, KeyError, TypeError, AttributeError, OverflowError) as exc:
            logger.warning(
                "ResidualLoad: calibration failed ({}); raw U3 retained", type(exc).__name__
            )
            return raw, {"status": "unavailable", "basis_version": BASIS_VERSION}

    def _load_disk(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        self._smard = self.cache.load_smard()
        self._weather = self.cache.load_weather()
        p = self._records_path()
        if p.exists():
            try:
                self.records = json.loads(p.read_text(encoding="utf-8"))
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "ResidualLoad: Fehlerhistorie nicht lesbar ({}), wird neu aufgebaut", exc
                )
                self.records = {}
        self._load_calibration()
        self._smard_at = self._weather_at = 0.0

    def _load_models(self, m0: dt.date) -> bool:
        if self.stage_a.has_month(m0):
            return True
        p = self._models_path(m0)
        if not p.exists():
            return False
        try:
            import joblib

            self.stage_a.import_month(joblib.load(p))
            return self.stage_a.has_month(m0)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "ResidualLoad: gespeichertes Modell {} nicht lesbar ({}), wird neu trainiert",
                p.name,
                exc,
            )
            return False

    def _save_models(self, m0: dt.date) -> None:
        import joblib

        tmp = self._models_path(m0).with_suffix(".tmp")
        joblib.dump(self.stage_a.export_month(m0), tmp)
        tmp.replace(self._models_path(m0))

    # ------------------------------------------------------------------ Eingaben
    def inputs(self, price_override_ct: Optional[pd.Series] = None) -> Optional[core.Inputs]:
        if self._smard is None or self._weather is None:
            return None
        return data.to_inputs(self._smard, self._weather, price_override_ct)

    def _ensure_month(self, inputs: core.Inputs, m0: dt.date) -> None:
        if self._load_models(m0):
            return
        t0 = time.time()
        logger.info("ResidualLoad: trainiere Stufe-A-Modelle für {:%Y-%m}", m0)
        with _low_priority():
            self.stage_a.train_month(inputs, m0)
        self._save_models(m0)
        logger.info("ResidualLoad: Training {:%Y-%m} fertig in {:.0f} s", m0, time.time() - t0)

    # ------------------------------------------------------------------ Hintergrundarbeit
    def kick(self, local_day: dt.date) -> None:
        """Startet (falls nicht schon aktiv) die Hintergrundarbeit; kehrt sofort zurück."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            if time.time() < self._backoff_until:
                return
            self._thread = threading.Thread(
                target=self._work, args=(local_day,), name="elecprice-residualload", daemon=True
            )
            self._thread.start()

    def wait(self, timeout: Optional[float] = None) -> None:
        t = self._thread
        if t is not None:
            t.join(timeout)

    def _work(self, local_day: dt.date) -> None:
        try:
            self.run_once(local_day)
            self.status = "bereit"
            self.last_error = None
        except data.RateLimited as exc:
            self._backoff_until = time.time() + BACKOFF_S
            self.last_error = f"{exc}; erneuter Versuch frühestens in {BACKOFF_S // 60} min"
            self.status = "wartet (Limit)"
            logger.warning("ResidualLoad: {}", self.last_error)
        except Exception as exc:  # noqa: BLE001
            self._backoff_until = time.time() + BACKOFF_S
            self.last_error = f"{type(exc).__name__}: {exc}"
            self.status = "Fehler"
            logger.warning(
                "ResidualLoad: Hintergrundarbeit fehlgeschlagen ({}); Rückfall auf ETS",
                self.last_error,
            )

    def run_once(self, local_day: dt.date) -> None:
        """Daten aktualisieren, Monatsmodell sichern, Fehlerhistorie ergänzen (blockierend; nur im Hintergrund aufrufen)."""
        now = time.time()
        with _low_priority():
            self._load_disk()
            if self._smard is None or now - self._smard_at > SMARD_REFRESH_S:
                self.status = "lädt SMARD"
                self._smard = self.cache.update_smard(self.get)
                self._smard_at = time.time()
            if self._weather is None or time.time() - self._weather_at > WEATHER_REFRESH_S:
                self.status = "lädt Wetter"
                self._weather = self.cache.update_weather(self.get, self.clock().date())
                self._weather_at = time.time()
        inputs = self.inputs()
        if inputs is None:
            raise RuntimeError("No price model inputs available")
        self.status = "trainiert"
        d_model = local_day - dt.timedelta(
            days=1
        )  # spätestens bekannte Tageszahl; genauer Tag steht erst im Prognosepfad fest
        self._ensure_month(inputs, core.month_start(d_model))
        self.status = "Fehlerhistorie"
        self.extend_records(inputs, d_model)

    def extend_records(self, inputs: core.Inputs, day: dt.date) -> int:
        """Ergänzt die Fehlerhistorie für alle Ausgabetage D' im Fenster, die an ``day`` nutzbar sind."""
        added = 0
        last = day - dt.timedelta(days=1 + max(core.TARGET_DAYS))
        first = day - dt.timedelta(days=core.WINDOW_DAYS)
        d = first
        while d <= last:
            key = d.isoformat()
            if key not in self.records:
                m0 = core.month_start(d)
                self.stage_a.drop_months_before(
                    m0
                )  # Tage laufen aufsteigend: ältere Monatsmodelle werden nicht mehr gebraucht
                if not self._load_models(m0):
                    self._ensure_month(inputs, m0)
                k = core.build_knowledge(inputs, d)
                fc = core.forecast_day(inputs, k, self.stage_a, self.curve)
                rec = core.error_record(inputs, self.curve, fc)
                if rec is not None:
                    self.records[key] = rec
                    added += 1
            d += dt.timedelta(days=1)
        # Speicher: nur das Modell des aktuell benötigten Monats behalten (ältere liegen weiter auf Platte)
        self.stage_a.drop_months_before(core.month_start(day))
        gc.collect()
        if added:
            cutoff = (day - dt.timedelta(days=core.WINDOW_DAYS + 30)).isoformat()
            self.records = {k: v for k, v in self.records.items() if k >= cutoff}
            tmp = self._records_path().with_suffix(".tmp")
            tmp.write_text(json.dumps(self.records), encoding="utf-8")
            tmp.replace(self._records_path())
            self.curve.drop_weeks_before(core.week_start(first) - dt.timedelta(days=7))
        return added

    # ------------------------------------------------------------------ schneller Prognosepfad
    def forecast(
        self, day: dt.date, known_price_ct: pd.Series, local_today: dt.date
    ) -> tuple[Optional[ForecastOutcome], str]:
        """Prognose für Ausgabetag ``day`` (D). Gibt (Ergebnis, Grund) zurück; Ergebnis ``None`` = nicht bereit."""
        with self._lock:
            if not self._loaded:
                self._load_disk()
            inputs = self.inputs(known_price_ct)
            if inputs is None:
                self.kick(local_today)
                return None, "Datencache wird aufgebaut"
            if self._weather_at == 0.0 or time.time() - self._weather_at > WEATHER_REFRESH_S:
                self.kick(
                    local_today
                )  # Wetter-Zuwachs im Hintergrund; vorhandene Daten genügen meist
            m0 = core.month_start(day)
            use = m0
            if not self._load_models(m0):
                prev = (m0 - dt.timedelta(days=1)).replace(day=1)
                self.kick(local_today)
                if not self._load_models(prev):
                    return None, f"Stufe-A-Modell {m0:%Y-%m} wird trainiert"
                use = prev
            k = core.build_knowledge(inputs, day)
            for n in core.TARGET_DAYS:
                w = k.weather[n + 1].reindex(k.target_hours(n))
                if int(w.notna().all(axis=1).sum()) < 20:
                    self.kick(local_today)
                    return None, f"Wetterprognose für Tag D+{n} fehlt im Cache"
            # EOS ruft update_data mehrfach je Planzyklus auf; ohne neue Eingänge (bekannte Preise, Wetterstand, Fehlerhistorie, Modellmonat)
            # ist das Ergebnis identisch -> nicht neu rechnen (Rechenlast würde sonst den Planlauf verlangsamen).
            key = (
                day,
                use,
                len(known_price_ct),
                str(known_price_ct.index.max()),
                hashlib.sha1(
                    known_price_ct.to_numpy().tobytes(), usedforsecurity=False
                ).hexdigest(),
                self._weather_at,
                len(self.records),
                max(self.records) if self.records else "",
            )
            if self._result_cache is not None and self._result_cache[0] == key:
                return self._result_cache[1], "ok"
            fc = self._day_forecast(inputs, k, use)
            q, reason, diagnostic = None, None, None
            try:
                scen = core.residual_scenarios(inputs, k, self.curve, fc, self.records)
                if np.isfinite(scen).all():
                    q = core.scenario_quantiles(scen)
                    q, diagnostic = self._calibrate(q, inputs, fc, use)
                else:
                    reason = "Szenarien enthalten fehlende Werte"
            except ValueError as exc:
                reason = str(exc)
                self.kick(local_today)  # Fehlerhistorie im Hintergrund auffüllen
            try:
                fc, q, revision_info = revisionlive.correct(
                    self, inputs, k, use, known_price_ct, fc, q
                )
            except Exception as exc:
                logger.warning("Price revision unavailable: {}", exc)
                revision_info = {"status": "unavailable"}
            outcome = ForecastOutcome(fc, use, q, reason, diagnostic, revision_info)
            if (
                revision_info.get("status") in {"revision", "profile_fallback"}
                and reason is None
                and not self._calibration_archive_dirty
                and (diagnostic or {}).get("status") != "unavailable"
            ):
                self._result_cache = (key, outcome)
            return outcome, "ok"

    def _day_forecast(
        self, inputs: core.Inputs, k: core.Knowledge, month: dt.date
    ) -> core.DayForecast:
        return core.forecast_day(inputs, k, self.stage_a, self.curve, month=month)
