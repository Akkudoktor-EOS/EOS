% SPDX-License-Identifier: Apache-2.0
(configtimewindow-page)=

# Time Window Sequence Configuration

## Overview

The `TimeWindowSequence` model configures allowed time slots for home appliance runs.
It contains a collection of `TimeWindow` objects that define when appliances can operate.

Each window specifies a start time, a duration, and optional restrictions on the calendar
date when the window starts.

Restrictions can be based on a specific date, a day of the week, or a calendar month.
All restrictions apply to the **start date of the window**, not to every date or timestamp
covered by the window.

This distinction is important for windows that cross midnight. For example, a window starting
on Friday at 22:00 and ending on Saturday at 06:00 remains applicable on Saturday morning
when restricted to Friday.

## Basic Structure

A `TimeWindowSequence` is configured as a JSON object with a `windows` array:

```json
{
  "windows": [
    {
      "start_time": "09:00",
      "duration": "PT2H",
      "day_of_week": null,
      "date": null,
      "month": null,
      "locale": null
    }
  ]
}
```

The example defines a daily window from 09:00 to 11:00 without calendar restrictions.

## TimeWindow Fields

Each `TimeWindow` object supports the following fields:

- **`start_time`** (required): Local time when the window begins.
- **`duration`** (optional): Duration of the window, expressed as a time interval. Defaults to zero,
  so it can be omitted when `end_time` defines the window.
- **`end_time`** (optional): Explicit end time of the window. The effective end is the later
  of this time and the end calculated from `start_time` plus `duration`.
- **`day_of_week`** (optional): Restricts the window's start date to a weekday.
- **`date`** (optional): Restricts the window's start date to a specific calendar date.
- **`month`** (optional): Restricts the window's start date to a calendar month, from 1 to 12.
- **`locale`** (optional): Locale used when parsing localized weekday names.

The `date`, `day_of_week`, and `month` restrictions are independent. If more than one is
specified, all specified restrictions must match the window's start date.

## Time Formats

### Start Time (`start_time`)

The `start_time` field accepts various time formats. Each example below is followed by the
wall-clock time it resolves to.

#### 24-Hour Format

```json
{
  "start_time": "14:30"
}
```

→ 14:30 (2:30 PM).

#### 12-Hour Format with AM/PM

```json
{
  "start_time": "2:30 PM"
}
```

→ 14:30 (2:30 PM).

#### Compact Format

```json
{
  "start_time": "1430"
}
```

→ 14:30 (2:30 PM).

#### With Seconds

```json
{
  "start_time": "14:30:45"
}
```

→ 14:30:45 (2:30:45 PM).

#### With Microseconds

```json
{
  "start_time": "14:30:45.123456"
}
```

→ 14:30:45.123456 (2:30:45.123456 PM).

#### European Format

```json
{
  "start_time": "14h30"
}
```

→ 14:30 (2:30 PM).

#### Short Formats

```json
{
  "start_time": "14"
}
```

→ 14:00 (2:00 PM).

```json
{
  "start_time": "2PM"
}
```

→ 14:00 (2:00 PM).

#### Decimal Time

```json
{
  "start_time": "14.5"
}
```

→ 14:30 (2:30 PM).

#### With Timezones

If supported by the configured time parser, timezone information can be included:

```json
{
  "start_time": "14:30 UTC"
}
```

→ 14:30 in the UTC timezone.

```json
{
  "start_time": "2:30 PM EST"
}
```

→ 14:30 in the EST timezone.

```json
{
  "start_time": "14:30 +05:30"
}
```

→ 14:30 at UTC+05:30.

A window's start time defines its position within the calendar day. The calendar restrictions
determine which dates may supply that start time.

### Duration (`duration`)

The `duration` field defines the length of a window beginning at `start_time`.

Duration values can be represented in several formats. ISO 8601 durations are recommended
for configuration files and API usage.

#### ISO 8601 Duration Format (Recommended)

```json
{
  "duration": "PT2H30M"
}
```

→ 2 hours 30 minutes.

```json
{
  "duration": "PT3H"
}
```

→ 3 hours.

```json
{
  "duration": "PT90M"
}
```

→ 90 minutes.

```json
{
  "duration": "PT1H30M45S"
}
```

→ 1 hour 30 minutes 45 seconds.

#### Human-Readable String Format

Human-readable duration strings are also supported:

```json
{
  "duration": "2 hours 30 minutes"
}
```

→ 2 hours 30 minutes.

```json
{
  "duration": "3 hours"
}
```

→ 3 hours.

```json
{
  "duration": "90 minutes"
}
```

→ 90 minutes.

```json
{
  "duration": "1 hour 30 minutes 45 seconds"
}
```

→ 1 hour 30 minutes 45 seconds.

```json
{
  "duration": "2 days 5 hours"
}
```

→ 2 days and 5 hours.

```json
{
  "duration": "1 day 2 hours 30 minutes"
}
```

→ 1 day, 2 hours, and 30 minutes.

#### Singular and Plural Forms

Both singular and plural unit names are supported:

```json
{
  "duration": "1 day"
}
```

→ 1 day.

```json
{
  "duration": "2 days"
}
```

→ 2 days.

```json
{
  "duration": "1 hour"
}
```

→ 1 hour.

```json
{
  "duration": "5 hours"
}
```

→ 5 hours.

#### Numeric Formats

A numeric duration represents seconds.

##### Seconds as Integer

```json
{
  "duration": 3600
}
```

→ 3600 seconds = 1 hour.

##### Seconds as Float

```json
{
  "duration": 3600.5
}
```

→ 3600.5 seconds = 1 hour and 0.5 seconds.

##### Tuple Format

A tuple contains four elements: days, hours, minutes, and seconds.

```json
{
  "duration": [0, 2, 30, 0]
}
```

→ 0 days, 2 hours, 30 minutes, 0 seconds = 2 hours 30 minutes.

```json
{
  "duration": [1, 0, 0, 0]
}
```

→ 1 day.

```json
{
  "duration": [0, 0, 45, 30]
}
```

→ 45 minutes 30 seconds.

```json
{
  "duration": [2, 5, 15, 45]
}
```

→ 2 days, 5 hours, 15 minutes, 45 seconds.

#### Validation Rules for Duration

- ISO 8601 strings must use supported duration specifiers.
- Human-readable strings must contain recognized time units.
- Numeric values represent seconds.
- Tuple values must contain exactly four elements.
- Durations must satisfy the model's validation rules.

Use ISO 8601 strings such as `"PT2H30M"` for concise, portable configuration.

### Explicit End Time (`end_time`)

The optional `end_time` field defines an explicit clock time for the window's end.

The effective end is the later of:

- `start_time` plus `duration`;
- the explicit `end_time`, interpreted on the appropriate calendar day.

If `end_time` is omitted, the window ends at `start_time` plus `duration`.

If `end_time` is earlier than or equal to `start_time`, it belongs to the following calendar day.
This allows a window to cross midnight.

For example:

```json
{
  "start_time": "22:00",
  "end_time": "06:00",
  "duration": "PT2H"
}
```

The explicit end is 06:00 on the following day. The effective window therefore runs from
22:00 until 06:00, rather than ending at midnight or after two hours.

If the duration extends beyond the explicit end, the duration determines the effective end:

```json
{
  "start_time": "22:00",
  "end_time": "02:00",
  "duration": "PT10H"
}
```

Here, the effective end is 08:00 on the following day.

When both times are equal, the explicit end is on the following day:

```json
{
  "start_time": "00:00",
  "end_time": "00:00",
  "duration": "PT2H"
}
```

In this example, the explicit end is midnight at the end of the start date.

The end boundary is exclusive. A timestamp exactly at the effective end does not belong to
the window.

## Calendar Restrictions

The calendar restrictions determine which dates can be used as the start date of a window.

When multiple restrictions are configured, they must all match the same start date.

### Day of Week (`day_of_week`)

The `day_of_week` field restricts a window to a particular weekday.

#### Using Numbers

Weekday numbers range from 0 to 6:

- `0`: Monday
- `1`: Tuesday
- `2`: Wednesday
- `3`: Thursday
- `4`: Friday
- `5`: Saturday
- `6`: Sunday

For example, this window starts on Mondays:

```json
{
  "start_time": "08:00",
  "duration": "PT4H",
  "day_of_week": 0
}
```

#### Using English Day Names

```json
{
  "start_time": "08:00",
  "duration": "PT4H",
  "day_of_week": "Monday"
}
```

```json
{
  "start_time": "08:00",
  "duration": "PT4H",
  "day_of_week": "sunday"
}
```

Day names are parsed according to the supported name formats.

#### Using Localized Day Names

A locale can be supplied when localized weekday names are used:

```json
{
  "start_time": "14:00",
  "duration": "PT2H",
  "day_of_week": "Montag",
  "locale": "de"
}
```

```json
{
  "start_time": "14:00",
  "duration": "PT2H",
  "day_of_week": "Lundi",
  "locale": "fr"
}
```

#### Weekday Restrictions on Overnight Windows

A weekday restriction applies to the date on which the window starts.

For example, this window starts on Friday and continues into Saturday:

```json
{
  "start_time": "22:00",
  "end_time": "06:00",
  "duration": "PT2H",
  "day_of_week": 4
}
```

The window remains applicable at 02:00 on Saturday because its start date is Friday.
The fact that the current timestamp falls on Saturday does not invalidate the window.

### Specific Date (`date`)

The `date` field restricts a window to one calendar date, using the ISO date format
`YYYY-MM-DD`.

For example:

```json
{
  "start_time": "10:00",
  "duration": "PT4H",
  "date": "2026-12-25"
}
```

This window starts on December 25, 2026.

The restriction applies to the window's start date, not every date covered by the window.
Consequently, an overnight window starting on December 25 can remain applicable after
midnight on December 26.

The `date` restriction does not override `day_of_week` or `month`. If multiple restrictions
are configured, all of them must match the start date.

### Month (`month`)

The `month` field restricts the window's start date to a calendar month.

The valid values are integers from 1 to 12:

- `1`: January
- `2`: February
- `3`: March
- `4`: April
- `5`: May
- `6`: June
- `7`: July
- `8`: August
- `9`: September
- `10`: October
- `11`: November
- `12`: December

For example, this window is available on start dates in October:

```json
{
  "start_time": "08:00",
  "duration": "PT4H",
  "month": 10
}
```

The month restriction applies to the start date. If a window starts on October 31 and extends
into November, it remains applicable during the part of the window that falls on November 1.

A month restriction is useful for seasonal schedules, such as restricting an appliance window
to winter or summer months.

### Combining Restrictions

The `date`, `day_of_week`, and `month` fields can be combined.

Every specified restriction must match the start date. For example:

```json
{
  "start_time": "22:00",
  "end_time": "06:00",
  "duration": "PT2H",
  "day_of_week": 4,
  "month": 10
}
```

This window can start only on a Friday in October. Once it starts, it remains applicable
after midnight, even if the following date is in November.

A specific date can be combined with weekday and month restrictions in the same way.
If the restrictions conflict, the window is not applicable.

## Complete Examples

### Example 1: Basic Daily Window

Allow an appliance to run between 09:00 and 11:00 every day:

```json
{
  "windows": [
    {
      "start_time": "09:00",
      "duration": "PT2H"
    }
  ]
}
```

### Example 2: Weekday Only

Allow an appliance to run between 08:00 and 18:00 on weekdays:

```json
{
  "windows": [
    { "start_time": "08:00", "duration": "PT10H", "day_of_week": 0 },
    { "start_time": "08:00", "duration": "PT10H", "day_of_week": 1 },
    { "start_time": "08:00", "duration": "PT10H", "day_of_week": 2 },
    { "start_time": "08:00", "duration": "PT10H", "day_of_week": 3 },
    { "start_time": "08:00", "duration": "PT10H", "day_of_week": 4 }
  ]
}
```

### Example 3: Multiple Daily Windows

Allow an appliance to run during morning and evening hours:

```json
{
  "windows": [
    {
      "start_time": "06:00",
      "duration": "PT3H"
    },
    {
      "start_time": "18:00",
      "duration": "PT4H"
    }
  ]
}
```

### Example 4: Weekend Special Hours

Use different hours on weekdays and weekends:

```json
{
  "windows": [
    { "start_time": "08:00", "duration": "PT8H", "day_of_week": "Monday" },
    { "start_time": "08:00", "duration": "PT8H", "day_of_week": "Tuesday" },
    { "start_time": "08:00", "duration": "PT8H", "day_of_week": "Wednesday" },
    { "start_time": "08:00", "duration": "PT8H", "day_of_week": "Thursday" },
    { "start_time": "08:00", "duration": "PT8H", "day_of_week": "Friday" },
    { "start_time": "10:00", "duration": "PT6H", "day_of_week": "Saturday" },
    { "start_time": "10:00", "duration": "PT6H", "day_of_week": "Sunday" }
  ]
}
```

### Example 5: Holiday Schedule

Allow an appliance to run on a specific date:

```json
{
  "windows": [
    {
      "start_time": "10:00",
      "duration": "PT4H",
      "date": "2026-12-25"
    }
  ]
}
```

### Example 6: Localized Configuration

Use German weekday names:

```json
{
  "windows": [
    {
      "start_time": "14:00",
      "duration": "PT2H",
      "day_of_week": "Montag",
      "locale": "de"
    },
    {
      "start_time": "14:00",
      "duration": "PT2H",
      "day_of_week": "Mittwoch",
      "locale": "de"
    },
    {
      "start_time": "14:00",
      "duration": "PT2H",
      "day_of_week": "Freitag",
      "locale": "de"
    }
  ]
}
```

### Example 7: Month Restriction

Allow an appliance to run during daytime in June, July, and August:

```json
{
  "windows": [
    { "start_time": "09:00", "duration": "PT6H", "month": 6 },
    { "start_time": "09:00", "duration": "PT6H", "month": 7 },
    { "start_time": "09:00", "duration": "PT6H", "month": 8 }
  ]
}
```

### Example 8: Overnight Window with Calendar Restrictions

Allow a window to start on Fridays in October and continue overnight:

```json
{
  "windows": [
    {
      "start_time": "22:00",
      "end_time": "06:00",
      "duration": "PT2H",
      "day_of_week": 4,
      "month": 10
    }
  ]
}
```

The window can start only on a Friday in October. It remains applicable after midnight,
including when the calendar date changes to November.

### Example 9: Night Shift Schedule

Allow an appliance to run overnight, from 22:00 until 06:00:

```json
{
  "windows": [
    {
      "start_time": "22:00",
      "end_time": "06:00",
      "duration": "PT2H"
    }
  ]
}
```

The window starts at 22:00 and ends at 06:00 the next day. The explicit end time determines
the effective end because it is later than the end calculated from the duration.

## Advanced Usage Patterns

### Off-Peak Hours

Configure windows for overnight off-peak periods:

```json
{
  "windows": [
    {
      "start_time": "23:00",
      "end_time": "07:00",
      "duration": "PT1H"
    }
  ]
}
```

The explicit end time extends the window to 07:00 the next day.

### Workday Lunch Break

Allow an appliance to run during a lunch break on weekdays:

```json
{
  "windows": [
    { "start_time": "12:00", "duration": "PT1H", "day_of_week": 0 },
    { "start_time": "12:00", "duration": "PT1H", "day_of_week": 1 },
    { "start_time": "12:00", "duration": "PT1H", "day_of_week": 2 },
    { "start_time": "12:00", "duration": "PT1H", "day_of_week": 3 },
    { "start_time": "12:00", "duration": "PT1H", "day_of_week": 4 }
  ]
}
```

### Seasonal Schedule

Use month restrictions for recurring seasonal availability:

```json
{
  "windows": [
    { "start_time": "08:00", "duration": "PT10H", "month": 6 },
    { "start_time": "08:00", "duration": "PT10H", "month": 7 },
    { "start_time": "08:00", "duration": "PT10H", "month": 8 }
  ]
}
```

These windows are available in June, July, and August every year.

## Common Patterns

### 1. Always Available

Define a window covering a full day:

```json
{
  "windows": [
    {
      "start_time": "00:00",
      "duration": "PT24H"
    }
  ]
}
```

### 2. Business Hours

Allow operation from 09:00 to 17:00 on weekdays:

```json
{
  "windows": [
    { "start_time": "09:00", "duration": "PT8H", "day_of_week": 0 },
    { "start_time": "09:00", "duration": "PT8H", "day_of_week": 1 },
    { "start_time": "09:00", "duration": "PT8H", "day_of_week": 2 },
    { "start_time": "09:00", "duration": "PT8H", "day_of_week": 3 },
    { "start_time": "09:00", "duration": "PT8H", "day_of_week": 4 }
  ]
}
```

### 3. Never Available

An empty `windows` array defines a sequence with no available windows:

```json
{
  "windows": []
}
```

## Validation Rules

- `start_time` must use a supported time format.
- `duration` must satisfy the model's duration validation rules.
- `end_time`, when supplied, must use a supported time format.
- `day_of_week` must be an integer from 0 to 6 or a supported weekday name.
- `date` must be a valid calendar date.
- `month` must be an integer from 1 to 12.
- `date`, `day_of_week`, and `month` all apply to the window's start date.
- All specified calendar restrictions must match the start date.
- `locale` is used when parsing localized weekday names.

## Tips and Best Practices

1. Prefer 24-hour time notation, such as `"14:30"`.
2. Choose durations appropriate for the appliance's operating cycle.
3. Use `end_time` when a window should end at a specific clock time.
4. Use `date` for one-off schedules and `month` for recurring seasonal schedules.
5. Combine calendar restrictions when a window should apply only on specific dates.
6. Remember that calendar restrictions apply to the start date, even across midnight.
7. Avoid overlapping windows unless overlapping availability is intentional.
8. Test timezone-aware schedules, especially around daylight-saving transitions.

## Error Handling

Common configuration errors include:

- **Invalid time format**: Use a supported time representation.
- **Invalid duration**: Use a supported duration representation, such as `"PT1H30M"`.
- **Invalid weekday**: Use a weekday number from 0 to 6 or a supported day name.
- **Invalid date**: Use a valid calendar date.
- **Invalid month**: Use an integer from 1 to 12.
- **Conflicting restrictions**: Ensure that all specified restrictions can match the same date.
- **Invalid locale**: Use a locale supported by the configured day-name parser.

## Integration Examples

### Python Usage

```python
from pydantic import ValidationError

try:
    config = TimeWindowSequence.model_validate_json(json_string)
    print(f"Configured {len(config.windows)} time windows")
except ValidationError as e:
    print(f"Configuration error: {e}")
```

### API Configuration

```json
{
  "device_id": "dishwasher_01",
  "time_windows": {
    "windows": [
      {
        "start_time": "22:00",
        "end_time": "06:00",
        "duration": "PT2H",
        "day_of_week": 4,
        "month": 10
      },
      {
        "start_time": "06:00",
        "duration": "PT2H"
      }
    ]
  }
}
```
