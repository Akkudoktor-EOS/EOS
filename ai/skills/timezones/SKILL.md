---
name: timezones
description: Use when code or tests create, parse, render or compare date-times, choose a time zone, or depend on the host's time zone.
---

# Time zones

`akkudoktoreos.utils.datetimeutil` is the single place for time zones, as the
rule in `AGENTS.md` says. This skill holds what that rule leaves out: which
zone to use and how to keep results independent of the host.

## Which zone

- **The location's zone** is `config.general.timezone`: `timezone_override`, or
  the zone looked up from latitude and longitude. Use it for local dates, day
  boundaries, wall-clock times and data shown to the user.
- **UTC** when there is no location zone, and for stored or exchanged instants.
- **The data's own zone** when data arrives with one: take it with
  `to_timezone(date_time=...)` (zoneinfo, pytz and Pendulum zones by name, fixed
  offsets such as pandas' `UTC+02:00` as their `Etc/GMT` zone).
- **The host's zone** (`to_timezone()` without arguments, `to_datetime()`
  without `in_timezone`) is only a last resort for naive input that has no other
  source. Results must not change with the host's `TZ`; CI runs in UTC,
  installations mostly elsewhere.

## Looking zones up

- From a location: `to_timezone(location=(latitude, longitude))`. This is the
  only use of `tzfpy`; call it through `to_timezone`, not directly.
- From a UTC offset: `to_timezone(utc_offset=...)` gives the `Etc/GMT` zone of
  whole-hour offsets from −12 to +14 and raises `ValueError` otherwise (5:30,
  9:45). The `Etc/GMT` sign is inverted: UTC+5 is `Etc/GMT-5`. An offset names
  no daylight saving time, so prefer the location's zone when there is one.
- `to_timezone` returns names that mean UTC (`GMT`, `Etc/GMT`) as `"UTC"`;
  `to_datetime(in_timezone=...)` keeps the name it is given.

## Parsing and rendering

- A string without an offset is wall-clock time in `in_timezone`, or in the
  host's zone without it. Pass the zone the string was meant in.
- A naive `datetime` or pandas `Timestamp` is taken as UTC, a naive string as
  local time. Make input aware at the boundary where it enters.
- Render with `to_datetime(..., as_string=True, in_timezone=zone)`. The same
  instant renders as `...Z` in `UTC` and `...+00:00` in `Etc/UTC`, so
  compare parsed instants (`compare_datetimes`), not strings.
- In Pydantic models, a validator cannot read a field declared after the one it
  validates (`info.data` only holds earlier fields). Apply a zone held in a
  later field in a `model_validator(mode="after")`.
- Do day arithmetic (start of day, hours in a day, adding days) on date-times
  in the location's zone: days have 23 or 25 hours at DST changes.
  `hours_in_day(dt)` counts in the zone of `dt`, without `dt` in the host's.

## Tests

- Run behaviour that depends on a zone under more than one host zone: the
  `set_other_timezone` fixture in `tests/conftest.py` sets Pendulum's local
  zone for one test and restores it.
- Golden files whose text depends on the host's zone (naive input, the
  host-zone fallback) pin the host zone, so they hold the same text on every
  host.
- Before declaring a time-zone change done, run its tests with `TZ=UTC` and
  with `TZ=Europe/Berlin`.
