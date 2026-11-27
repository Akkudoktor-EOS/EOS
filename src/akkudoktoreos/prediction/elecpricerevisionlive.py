"""Daily immutable as-observed revision snapshot and price/band alignment."""

import datetime as dt
import json
import os
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from akkudoktoreos.prediction import elecpricerevision as runtime


def _write(path: Any, document: Any) -> Any:
    with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False, encoding="utf8") as handle:
        tmp = Path(handle.name)
        try:
            json.dump(document, handle, allow_nan=False, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
    try:
        os.link(tmp, path)  # first publication wins, including concurrent engines
    except FileExistsError:
        pass
    finally:
        tmp.unlink(missing_ok=True)


def _read_frame(doc: Any) -> Any:
    f = pd.DataFrame(doc["raw"], index=pd.to_datetime(doc["hours"], utc=True))
    return f


def correct(engine: Any, inputs: Any, k: Any, month: Any, known: Any, fc: Any, q: Any) -> Any:
    day = fc.day
    folder = engine.folder / "revision_v1"
    folder.mkdir(exist_ok=True)
    snap = folder / ("issue-" + day.isoformat() + ".json")
    if not snap.exists():
        now = engine.clock().tz_convert(runtime.ZONE)
        if now.date() != day or now.hour < 13:
            return fc, q, {"status": "awaiting_observed_issue"}
        monday = day - dt.timedelta(days=day.weekday())
        wp = folder / ("weights-" + monday.isoformat() + ".json")
        if not wp.exists():
            return fc, q, {"status": "missing_weekly_fit"}
        weights = json.loads(wp.read_text())
        if weights["week"] != monday.isoformat():
            raise ValueError("weight week mismatch")
        i = fc.point.index
        d1i = runtime.hours(day + dt.timedelta(days=1))
        a = known.reindex(d1i)
        if not np.isfinite(a).all():
            return fc, q, {"status": "incomplete_published_day"}
        sa = engine.stage_a.predict(k, inputs, month=month)
        curve = engine.curve.price_from_resid
        resid = fc.resid_hat
        base = curve(inputs, day, resid)
        d1 = pd.DataFrame({"actual": a, "curve": curve(inputs, day, sa.resid_mw.reindex(d1i))})
        frame = pd.DataFrame(
            {
                "point": fc.point,
                "rl_gw": resid / 1000,
                "anchor": fc.offset,
                "horizon": sa.n.reindex(i),
                "curve_up10": curve(inputs, day, resid + 10000) - base,
                "curve_down10": base - curve(inputs, day, resid - 10000),
            }
        )
        for col in ("load", "wind_on", "wind_off", "pv"):
            frame[col + "_gw"] = sa[col + "_mw"].reindex(i) / 1000
        if not np.isfinite(frame.to_numpy()).all() or not np.isfinite(d1.to_numpy()).all():
            return fc, q, {"status": "incomplete_inputs"}
        prev = folder / ("issue-" + (day - dt.timedelta(days=1)).isoformat() + ".json")
        previous = _read_frame(json.loads(prev.read_text())) if prev.exists() else None
        features = runtime.feature_frame(frame, d1, previous, day)
        if features is None:
            return fc, q, {"status": "incomplete_hours"}
        prediction = runtime.apply_revision(frame, d1, previous, weights, day)
        snapdoc = {
            "issue": day.isoformat(),
            "observed_at": now.isoformat(),
            "hours": [t.isoformat() for t in i],
            "raw": frame.to_dict("list"),
            "d1_hours": [t.isoformat() for t in d1i],
            "d1": d1.to_dict("list"),
            "corrected": prediction.tolist(),
            "had_previous": previous is not None,
            "week": weights["week"],
        }
        _write(snap, snapdoc)
    doc = json.loads(snap.read_text())
    idx = pd.to_datetime(doc["hours"], utc=True)
    if not idx.equals(fc.point.index):
        raise ValueError("issue grid mismatch")
    corrected = pd.Series(doc["corrected"], index=fc.point.index, name=fc.point.name)
    delta = corrected.to_numpy() - fc.point.to_numpy()
    meta = {k: doc[k] for k in ("observed_at", "had_previous", "week")}
    meta["status"] = "revision" if doc["had_previous"] else "profile_fallback"
    return replace(fc, point=corrected), None if q is None else q + delta[None, :], meta
