//! Timestamps like EOS (`utils/datetimeutil.to_datetime`, pendulum 3).
//!
//! * Timezone-naive values are local time. In EOS that is the timezone of the
//!   server process; here the run timezone (`general.timezone`) – identical as long as the
//!   Python server runs in the configured timezone (parity: `TZ` = run timezone).
//! * Ambiguous local time (autumn) → the later one (pendulum `fold=1`); local time in the gap
//!   (spring) → with the offset before the transition, i.e. shifted forward
//!   (measured: 2026-03-29 02:30 Berlin → 03:30+02:00, 2026-10-25 02:30 → +01:00).
//! * `add(hours=…)`/`add(seconds=…)` work in absolute time, `add(days=…)` on the wall clock.

use chrono::{DateTime, Duration, FixedOffset, LocalResult, NaiveDate, NaiveDateTime, TimeZone, Utc};
use chrono_tz::Tz;
use serde_json::Value;

/// Local time → timestamp (pendulum `DateTime.create(…, tz=…)`).
pub fn local(tz: Tz, n: NaiveDateTime) -> DateTime<Tz> {
    match tz.from_local_datetime(&n) {
        LocalResult::Single(d) => d,
        LocalResult::Ambiguous(_, later) => later,
        LocalResult::None => {
            // Offset before the transition: one day earlier the old offset applies (practically always).
            let before = tz.from_utc_datetime(&(n - Duration::days(1))).fixed_offset().offset().local_minus_utc();
            let utc = n - Duration::seconds(i64::from(before));
            tz.from_utc_datetime(&utc)
        }
    }
}

/// Unix time [s] as f64 like Python `datetime.timestamp()` (microseconds, correctly rounded).
pub fn ts(dt: &DateTime<Tz>) -> f64 {
    let micros = dt.timestamp() * 1_000_000 + i64::from(dt.timestamp_subsec_micros());
    micros as f64 / 1e6
}

/// pendulum `int_timestamp`.
pub fn int_ts(dt: &DateTime<Tz>) -> i64 {
    dt.timestamp()
}

/// Timestamp from Unix seconds in `tz`.
pub fn from_ts(secs: i64, tz: Tz) -> DateTime<Tz> {
    tz.timestamp_opt(secs, 0).single().unwrap_or_else(|| tz.from_utc_datetime(&NaiveDateTime::default()))
}

/// Timestamp from Unix milliseconds in `tz`.
pub fn from_ms(ms: i64, tz: Tz) -> DateTime<Tz> {
    let secs = ms.div_euclid(1000);
    let nanos = (ms.rem_euclid(1000) * 1_000_000) as u32;
    tz.timestamp_opt(secs, nanos).single().unwrap_or_else(|| tz.from_utc_datetime(&NaiveDateTime::default()))
}

/// `start_of("day")`.
pub fn start_of_day(dt: &DateTime<Tz>) -> DateTime<Tz> {
    let tz = dt.timezone();
    local(tz, dt.date_naive().and_hms_opt(0, 0, 0).unwrap_or_default())
}

/// `add(days=n)` (wall clock).
pub fn add_days(dt: &DateTime<Tz>, days: i64) -> DateTime<Tz> {
    local(dt.timezone(), dt.naive_local() + Duration::days(days))
}

/// `add(seconds=…)` (absolute).
pub fn add_secs(dt: &DateTime<Tz>, secs: i64) -> DateTime<Tz> {
    *dt + Duration::seconds(secs)
}

/// `isoformat()` like Python/pendulum (`+00:00` instead of `Z`, microseconds only if ≠ 0).
pub fn iso(dt: &DateTime<Tz>) -> String {
    let fixed = dt.fixed_offset();
    let us = dt.timestamp_subsec_micros();
    let base = fixed.format("%Y-%m-%dT%H:%M:%S").to_string();
    let off = fixed.format("%:z").to_string();
    if us == 0 { format!("{base}{off}") } else { format!("{base}.{us:06}{off}") }
}

/// Fixed timezone of a timestamp → `FixedOffset` representation.
pub fn to_fixed(dt: &DateTime<Tz>) -> DateTime<FixedOffset> {
    dt.fixed_offset()
}

/// `to_datetime(value)` for JSON values: string (ISO etc.) or Unix timestamp.
pub fn parse_value(v: &Value, tz: Tz) -> Result<DateTime<Tz>, String> {
    match v {
        Value::String(s) => parse_str(s, tz),
        Value::Number(n) => {
            let f = n.as_f64().ok_or("invalid timestamp")?;
            from_float_ts(f, tz)
        }
        _ => Err(format!("Unsupported date input type: {v}")),
    }
}

fn from_float_ts(f: f64, tz: Tz) -> Result<DateTime<Tz>, String> {
    if !f.is_finite() {
        return Err(format!("Date string {f} does not match any known formats."));
    }
    let secs = f.floor();
    let micros = ((f - secs) * 1e6).round() as i64;
    let base = Utc.timestamp_opt(secs as i64, 0).single().ok_or("timestamp out of range")?;
    Ok((base + Duration::microseconds(micros)).with_timezone(&tz))
}

/// `to_datetime(text)`: ISO 8601 with/without offset, date only, some date formats of
/// pendulum (`DD/MM/YYYY`, `D.M.YYYY`, …) and Unix timestamps as text.
pub fn parse_str(text: &str, tz: Tz) -> Result<DateTime<Tz>, String> {
    let t = text.trim();
    let err = || format!("Date string {t} does not match any known formats.");
    if t.is_empty() {
        return Err(err());
    }
    if let Some(dt) = parse_aware(t) {
        return Ok(dt.with_timezone(&tz));
    }
    if let Some(n) = parse_naive(t) {
        return Ok(local(tz, n));
    }
    if let Ok(f) = t.parse::<f64>() {
        return from_float_ts(f, tz);
    }
    Err(err())
}

fn parse_aware(t: &str) -> Option<DateTime<FixedOffset>> {
    if let Ok(dt) = DateTime::parse_from_rfc3339(t) {
        return Some(dt);
    }
    for fmt in [
        "%Y-%m-%dT%H:%M:%S%.f%:z",
        "%Y-%m-%dT%H:%M:%S%.f%z",
        "%Y-%m-%dT%H:%M%:z",
        "%Y-%m-%dT%H:%M%z",
        "%Y-%m-%d %H:%M:%S%.f%:z",
        "%Y-%m-%d %H:%M:%S%.f%z",
        "%Y-%m-%d %H:%M%:z",
    ] {
        if let Ok(dt) = DateTime::parse_from_str(t, fmt) {
            return Some(dt);
        }
    }
    let stripped = t.strip_suffix('Z').or_else(|| t.strip_suffix('z'))?;
    parse_naive_time(stripped).map(|n| Utc.from_utc_datetime(&n).fixed_offset())
}

fn parse_naive_time(t: &str) -> Option<NaiveDateTime> {
    for fmt in ["%Y-%m-%dT%H:%M:%S%.f", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S%.f", "%Y-%m-%d %H:%M"] {
        if let Ok(n) = NaiveDateTime::parse_from_str(t, fmt) {
            return Some(n);
        }
    }
    None
}

fn parse_naive(t: &str) -> Option<NaiveDateTime> {
    if let Some(n) = parse_naive_time(t) {
        return Some(n);
    }
    for fmt in ["%Y-%m-%d", "%d/%m/%Y", "%d.%m.%Y", "%Y.%m.%d", "%m-%d-%Y"] {
        if let Ok(d) = NaiveDate::parse_from_str(t, fmt) {
            return d.and_hms_opt(0, 0, 0);
        }
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn dst_wie_pendulum() {
        let tz: Tz = "Europe/Berlin".parse().unwrap_or(Tz::UTC);
        assert_eq!(
            iso(&parse_str("2026-10-25T02:30:00", tz).unwrap_or_else(|e| panic!("{e}"))),
            "2026-10-25T02:30:00+01:00"
        );
        assert_eq!(
            iso(&parse_str("2026-03-29 02:30:00", tz).unwrap_or_else(|e| panic!("{e}"))),
            "2026-03-29T03:30:00+02:00"
        );
        assert_eq!(iso(&parse_str("2026-10-25", tz).unwrap_or_else(|e| panic!("{e}"))), "2026-10-25T00:00:00+02:00");
        let d = parse_str("2026-10-26T02:30:00", tz).unwrap_or_else(|e| panic!("{e}"));
        assert_eq!(iso(&add_days(&d, -1)), "2026-10-25T02:30:00+01:00");
        assert_eq!(
            iso(&parse_str("2026-11-10T10:00:00.123Z", tz).unwrap_or_else(|e| panic!("{e}"))),
            "2026-11-10T11:00:00.123000+01:00"
        );
    }
}
