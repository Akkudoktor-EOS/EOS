% SPDX-License-Identifier: Apache-2.0
(configtimewindow-page)=

# Time Window Sequence Configuration

## Overview

The `TimeWindowSequence` model is used to configure allowed time slots for home appliance runs.
It contains a collection of `TimeWindow` objects that define when appliances can operate.

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
      "locale": null
    }
  ]
}
```

## TimeWindow Fields

Each `TimeWindow` object has the following fields:

- **`start_time`** (required): Time when the window begins
- **`duration`** (required): How long the window lasts
- **`day_of_week`** (optional): Restrict to specific day of week
- **`date`** (optional): Restrict to specific calendar date
- **`locale`** (optional): Language for day name parsing

## Time Formats

### Start Time (`start_time`)

The `start_time` field accepts various time formats:

#### 24-Hour Format

```json
{
  "start_time": "14:30"        // 2:30 PM
}
```

#### 12-Hour Format with AM/PM

```json
{
  "start_time": "2:30 PM"      // 2:30 PM
}
```

#### Compact Format

```json
{
  "start_time": "1430"         // 2:30 PM
}
```

#### With Seconds

```json
{
  "start_time": "14:30:45"     // 2:30:45 PM
}
```

#### With Microseconds

```json
{
  "start_time": "14:30:45.123456"
}
```

#### European Format

```json
{
  "start_time": "14h30"        // 2:30 PM
}
```

#### Short Formats

```json
{
  "start_time": "14"           // 2:00 PM
}
```

```json
{
  "start_time": "2PM"          // 2:00 PM
}
```

#### Decimal Time

```json
{
  "start_time": "14.5"         // 2:30 PM (14:30)
}
```

#### With Timezones

```json
{
  "start_time": "14:30 UTC"
}
```

```json
{
  "start_time": "2:30 PM EST"
}
```

```json
{
  "start_time": "14:30 +05:30"
}
```

### Duration (`duration`)

The `duration` field supports multiple formats for maximum flexibility:

#### ISO 8601 Duration Format (Recommended)

```json
{
  "duration": "PT2H30M"        // 2 hours 30 minutes
}
```

```json
{
  "duration": "PT3H"           // 3 hours
}
```

```json
{
  "duration": "PT90M"          // 90 minutes
}
```

```json
{
  "duration": "PT1H30M45S"     // 1 hour 30 minutes 45 seconds
}
```

#### Human-Readable String Format

The system accepts natural language duration strings:

```json
{
  "duration": "2 hours 30 minutes"
}
```

```json
{
  "duration": "3 hours"
}
```

```json
{
  "duration": "90 minutes"
}
```

```json
{
  "duration": "1 hour 30 minutes 45 seconds"
}
```

```json
{
  "duration": "2 days 5 hours"
}
```

```json
{
  "duration": "1 day 2 hours 30 minutes"
}
```

#### Singular and Plural Forms

Both singular and plural forms are supported:

```json
{
  "duration": "1 day"          // Singular
}
```

```json
{
  "duration": "2 days"         // Plural
}
```

```json
{
  "duration": "1 hour"         // Singular
}
```

```json
{
  "duration": "5 hours"        // Plural
}
```

#### Numeric Formats

##### Seconds as Integer

```json
{
  "duration": 3600             // 3600 seconds = 1 hour
}
```

```json
{
  "duration": 1800             // 1800 seconds = 30 minutes
}
```

##### Seconds as Float

```json
{
  "duration": 3600.5           // 3600.5 seconds = 1 hour 0.5 seconds
}
```

##### Tuple Format [days, hours, minutes, seconds]

```json
{
  "duration": [0, 2, 30, 0]    // 0 days, 2 hours, 30 minutes, 0 seconds
}
```

```json
{
  "duration": [1, 0, 0, 0]     // 1 day
}
```

```json
{
  "duration": [0, 0, 45, 30]   // 45 minutes 30 seconds
}
```

```json
{
  "duration": [2, 5, 15, 45]   // 2 days, 5 hours, 15 minutes, 45 seconds
}
```

#### Mixed Time Units

You can combine different time units in string format:

```json
{
  "duration": "1 day 4 hours 30 minutes 15 seconds"
}
```

```json
{
  "duration": "3 days 2 hours"
}
```

```json
{
  "duration": "45 minutes 30 seconds"
}
```

#### Common Duration Examples

##### Short Durations

```json
{
  "duration": "30 minutes"     // Quick appliance cycle
}
```

```json
{
  "duration": "PT30M"          // ISO format equivalent
}
```

```json
{
  "duration": 1800             // Numeric equivalent (seconds)
}
```

##### Medium Durations

```json
{
  "duration": "2 hours 15 minutes"
}
```

```json
{
  "duration": "PT2H15M"        // ISO format equivalent
}
```

```json
{
  "duration": [0, 2, 15, 0]    // Tuple format equivalent
}
```

##### Long Durations

```json
{
  "duration": "1 day 8 hours"  // All-day appliance window
}
```

```json
{
  "duration": "PT32H"          // ISO format equivalent
}
```

```json
{
  "duration": [1, 8, 0, 0]     // Tuple format equivalent
}
```

#### Validation Rules for Duration

- **ISO 8601 format**: Must start with `PT` and use valid duration specifiers (H, M, S)
- **String format**: Must contain valid time units (day/days, hour/hours, minute/minutes, second/seconds)
- **Numeric format**: Must be a positive number representing seconds
- **Tuple format**: Must be exactly 4 elements: [days, hours, minutes, seconds]
- **All formats**: Duration must be positive (greater than 0)

#### Duration Format Recommendations

1. **Use ISO 8601 format** for API consistency: `"PT2H30M"`
2. **Use human-readable strings** for configuration files: `"2 hours 30 minutes"`
3. **Use numeric format** for programmatic calculations: `9000` (seconds)
4. **Use tuple format** for structured data: `[0, 2, 30, 0]`

#### Error Handling for Duration

Common duration errors and solutions:

- **Invalid ISO format**: Ensure proper `PT` prefix and valid specifiers
- **Unknown time units**: Use day/days, hour/hours, minute/minutes, second/seconds
- **Negative duration**: All durations must be positive
- **Invalid tuple length**: Tuple must have exactly 4 elements
- **String too long**: Duration strings have a maximum length limit for security

## Day of Week Restrictions

### Using Numbers (0=Monday, 6=Sunday)

```json
{
  "day_of_week": 0             // Monday
}
```

```json
{
  "day_of_week": 6             // Sunday
}
```

### Using English Day Names

```json
{
  "day_of_week": "Monday"
}
```

```json
{
  "day_of_week": "sunday"      // Case insensitive
}
```

### Using Localized Day Names

```json
{
  "day_of_week": "Montag",     // German for Monday
  "locale": "de"
}
```

```json
{
  "day_of_week": "Lundi",      // French for Monday
  "locale": "fr"
}
```

## Date Restrictions

### Specific Date

```json
{
  "date": "2024-12-25"         // Christmas Day 2024
}
```

**Note**: When `date` is specified, `day_of_week` is ignored.

## Complete Examples

### Example 1: Basic Daily Window

Allow appliance to run between 9:00 AM and 11:00 AM every day:

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

Allow appliance to run between 8:00 AM and 6:00 PM on weekdays:

```json
{
  "windows": [
    {
      "start_time": "08:00",
      "duration": "PT10H",
      "day_of_week": 0
    },
    {
      "start_time": "08:00",
      "duration": "PT10H",
      "day_of_week": 1
    },
    {
      "start_time": "08:00",
      "duration": "PT10H",
      "day_of_week": 2
    },
    {
      "start_time": "08:00",
      "duration": "PT10H",
      "day_of_week": 3
    },
    {
      "start_time": "08:00",
      "duration": "PT10H",
      "day_of_week": 4
    }
  ]
}
```

### Example 3: Multiple Daily Windows

Allow appliance to run during morning and evening hours:

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

Different hours for weekdays and weekends:

```json
{
  "windows": [
    {
      "start_time": "08:00",
      "duration": "PT8H",
      "day_of_week": "Monday"
    },
    {
      "start_time": "08:00",
      "duration": "PT8H",
      "day_of_week": "Tuesday"
    },
    {
      "start_time": "08:00",
      "duration": "PT8H",
      "day_of_week": "Wednesday"
    },
    {
      "start_time": "08:00",
      "duration": "PT8H",
      "day_of_week": "Thursday"
    },
    {
      "start_time": "08:00",
      "duration": "PT8H",
      "day_of_week": "Friday"
    },
    {
      "start_time": "10:00",
      "duration": "PT6H",
      "day_of_week": "Saturday"
    },
    {
      "start_time": "10:00",
      "duration": "PT6H",
      "day_of_week": "Sunday"
    }
  ]
}
```

### Example 5: Holiday Schedule

Special schedule for a specific date:

```json
{
  "windows": [
    {
      "start_time": "10:00",
      "duration": "PT4H",
      "date": "2024-12-25"
    }
  ]
}
```

### Example 6: Localized Configuration

Using German day names:

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

### Example 7: Complex Schedule with Timezones

Multiple windows with different timezones:

```json
{
  "windows": [
    {
      "start_time": "09:00 UTC",
      "duration": "PT4H",
      "day_of_week": "Monday"
    },
    {
      "start_time": "2:00 PM EST",
      "duration": "PT3H",
      "day_of_week": "Friday"
    }
  ]
}
```

### Example 8: Night Shift Schedule

Crossing midnight (note: each window is within a single day):

```json
{
  "windows": [
    {
      "start_time": "22:00",
      "duration": "PT2H"
    },
    {
      "start_time": "00:00",
      "duration": "PT6H"
    }
  ]
}
```

## Advanced Usage Patterns

### Off-Peak Hours

Configure appliance to run during off-peak electricity hours:

```json
{
  "windows": [
    {
      "start_time": "23:00",
      "duration": "PT1H"
    },
    {
      "start_time": "00:00",
      "duration": "PT7H"
    }
  ]
}
```

### Workday Lunch Break

Allow appliance to run during lunch break on workdays:

```json
{
  "windows": [
    {
      "start_time": "12:00",
      "duration": "PT1H",
      "day_of_week": 0
    },
    {
      "start_time": "12:00",
      "duration": "PT1H",
      "day_of_week": 1
    },
    {
      "start_time": "12:00",
      "duration": "PT1H",
      "day_of_week": 2
    },
    {
      "start_time": "12:00",
      "duration": "PT1H",
      "day_of_week": 3
    },
    {
      "start_time": "12:00",
      "duration": "PT1H",
      "day_of_week": 4
    }
  ]
}
```

### Seasonal Schedule

Different schedules for different dates:

```json
{
  "windows": [
    {
      "start_time": "08:00",
      "duration": "PT10H",
      "date": "2024-06-21"
    },
    {
      "start_time": "09:00",
      "duration": "PT8H",
      "date": "2024-12-21"
    }
  ]
}
```

## Common Patterns

### 1. Always Available

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

```json
{
  "windows": [
    {
      "start_time": "09:00",
      "duration": "PT8H",
      "day_of_week": 0
    },
    {
      "start_time": "09:00",
      "duration": "PT8H",
      "day_of_week": 1
    },
    {
      "start_time": "09:00",
      "duration": "PT8H",
      "day_of_week": 2
    },
    {
      "start_time": "09:00",
      "duration": "PT8H",
      "day_of_week": 3
    },
    {
      "start_time": "09:00",
      "duration": "PT8H",
      "day_of_week": 4
    }
  ]
}
```

### 3. Never Available

```json
{
  "windows": []
}
```

## Validation Rules

- `start_time` must be a valid time format
- `duration` must be a positive duration
- `day_of_week` must be 0-6 (integer) or valid day name (string)
- `date` must be a valid ISO date format (YYYY-MM-DD)
- If `date` is specified, `day_of_week` is ignored
- `locale` must be a valid locale code when using localized day names

## Tips and Best Practices

1. **Use 24-hour format** for clarity: `"14:30"` instead of `"2:30 PM"`
2. **Keep durations reasonable** for appliance operation cycles
3. **Test timezone handling** if using timezone-aware times
4. **Use specific dates** for holiday schedules
5. **Consider overlapping windows** for flexibility
6. **Use localization** for international deployments
7. **Document your patterns** for maintenance

## Error Handling

Common errors and solutions:

- **Invalid time format**: Use supported time formats listed above
- **Invalid duration**: Use ISO 8601 duration format (PT1H30M)
- **Invalid day name**: Check spelling and locale settings
- **Invalid date**: Use YYYY-MM-DD format
- **Unknown locale**: Use standard locale codes (en, de, fr, etc.)

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
        "duration": "PT2H"
      },
      {
        "start_time": "06:00",
        "duration": "PT2H"
      }
    ]
  }
}
```
