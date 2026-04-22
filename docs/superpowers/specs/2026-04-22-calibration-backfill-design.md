# Historical Calibration Data Backfill

**Date:** 2026-04-22  
**Status:** Approved

## Purpose

Create a script to backfill 30 days of historical calibration data for the weatherbot. This enables the calibration system to calculate accurate sigma (forecast error standard deviation) values per city/source for Kelly criterion bet sizing.

## Approach

**Sequential with rate limiting** — Loop through dates and cities one at a time with small delays. Simple, debuggable, and sufficient for a one-time-ish backfill (~15-20 min runtime).

## Data Sources

| Data | API | Endpoint |
|------|-----|----------|
| Actual temps | Open-Meteo Historical Weather | `archive-api.open-meteo.com/v1/archive` |
| ECMWF forecasts | Open-Meteo Previous Runs | `previous-runs-api.open-meteo.com/v1/forecast?models=ecmwf_ifs025` |
| HRRR forecasts (US only) | Open-Meteo Previous Runs | `previous-runs-api.open-meteo.com/v1/forecast?models=gfs_seamless` |

No Polymarket API calls needed — calibration only requires forecast/actual data.

## Output Format

Minimal market files in `data/markets/{city}_{date}.json`:

```json
{
  "city": "chicago",
  "date": "2026-04-01",
  "status": "resolved",
  "actual_temp": 78.5,
  "forecast_snapshots": [
    {
      "ts": "2026-03-30T12:00:00Z",
      "horizon": "D+2",
      "hours_left": 60.0,
      "ecmwf": 74.5,
      "hrrr": 75.2,
      "metar": null,
      "best": 75.2,
      "best_source": "hrrr"
    },
    {
      "ts": "2026-03-31T12:00:00Z",
      "horizon": "D+1",
      "hours_left": 36.0,
      "ecmwf": 78.3,
      "hrrr": 77.5,
      "metar": null,
      "best": 77.5,
      "best_source": "hrrr"
    },
    {
      "ts": "2026-04-01T12:00:00Z",
      "horizon": "D+0",
      "hours_left": 12.0,
      "ecmwf": 80.3,
      "hrrr": 79.1,
      "metar": null,
      "best": 79.1,
      "best_source": "hrrr"
    }
  ]
}
```

**Field requirements verified against bot_v2.py:**
- `city`, `status`, `actual_temp`, `forecast_snapshots` are required
- `status: "resolved"` ensures bot skips these for betting
- Files without `pnl` are excluded from reports
- Calibration only reads these four fields

**Snapshot ordering:** D+2 first, D+0 last. Calibration uses `vals[-1]` (last snapshot), so D+0 (most accurate) is used for error calculation.

## Data Flow

For each (city, date) pair:

1. **Skip check**: File exists with `actual_temp` and 3+ `forecast_snapshots` → skip
2. **Fetch actual temp** (Historical Weather API):
   ```
   GET https://archive-api.open-meteo.com/v1/archive
     ?latitude={lat}&longitude={lon}
     &start_date={date}&end_date={date}
     &daily=temperature_2m_max
     &temperature_unit={fahrenheit if unit=="F" else celsius}
     &timezone=auto
   ```
3. **Fetch forecasts** (Previous Runs API, hourly → max):
   ```
   GET https://previous-runs-api.open-meteo.com/v1/forecast
     ?latitude={lat}&longitude={lon}
     &start_date={date}&end_date={date}
     &hourly=temperature_2m,temperature_2m_previous_day1,temperature_2m_previous_day2
     &temperature_unit={fahrenheit if unit=="F" else celsius}
     &timezone={city_timezone}
     &models=ecmwf_ifs025
   ```
   Then calculate daily max from hourly arrays:
   - `d0 = max(temperature_2m)`
   - `d1 = max(temperature_2m_previous_day1)`
   - `d2 = max(temperature_2m_previous_day2)`
   
   Repeat with `models=gfs_seamless` for US cities (nyc, chicago, miami, dallas, seattle, atlanta).

4. **Build market file**: Create minimal structure with forecasts and actual
   - `best` = HRRR value if available (US cities), else ECMWF
   - `best_source` = "hrrr" or "ecmwf" accordingly
   - `metar` = null (not available historically)
   - Timezone from TIMEZONES dict in bot_v2.py (or UTC fallback)
5. **Write and log**: Save to `data/markets/{city}_{date}.json`, print result
6. **Rate limit**: Sleep 0.1s

## Error Handling

- **Idempotent**: Skip files that already have complete data; safe to re-run
- **Fail forward**: Log failures, continue with remaining markets
- **All-or-nothing per market**: Don't write partial data
- **Summary at end**: Print counts of created/skipped/failed

## CLI Interface

**Script:** `backfill_calibration.py`

```bash
# Default: last 30 days
python backfill_calibration.py

# Custom date range
python backfill_calibration.py --start 2026-03-20 --end 2026-04-15

# Dry run
python backfill_calibration.py --dry-run
```

**Arguments:**
- `--start YYYY-MM-DD` — Start date (default: 30 days ago)
- `--end YYYY-MM-DD` — End date (default: yesterday)
- `--dry-run` — Print plan without fetching or writing

## Cities

All 20 cities from bot_v2.py LOCATIONS:

**US (ECMWF + HRRR):** nyc, chicago, miami, dallas, seattle, atlanta

**International (ECMWF only):** london, paris, munich, ankara, seoul, tokyo, shanghai, singapore, lucknow, tel-aviv, toronto, sao-paulo, buenos-aires, wellington

## Expected Output

30 days × 20 cities = 600 market files

Calibration will then have sufficient data (30 per city) to calculate sigma for all city/source pairs, meeting the default CALIBRATION_MIN threshold of 30. (Note: Railway deployment uses CALIBRATION_MIN=3 via env var.)
