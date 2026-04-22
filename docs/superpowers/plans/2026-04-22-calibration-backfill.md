# Calibration Backfill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create `backfill_calibration.py` to populate 30 days of historical forecast/actual data for calibration sigma calculation.

**Architecture:** Single-file script with CLI. Fetches actual temps from Open-Meteo Historical Weather API, forecasts from Previous Runs API (ECMWF all cities, GFS/HRRR US-only). Writes minimal market JSON files to `data/markets/`. Sequential with 0.1s rate limiting.

**Tech Stack:** Python 3.9+, requests, argparse, bot_v2.py (imports LOCATIONS, TIMEZONES)

---

## File Structure

- **Create:** `backfill_calibration.py` — main script
- **Create:** `tests/test_backfill_calibration.py` — unit/integration tests
- **Reference:** `bot_v2.py:54-86` — LOCATIONS and TIMEZONES dicts
- **Reference:** `backfill_actual_temp.py` — pattern for rate-limited backfill

---

### Task 1: Script Skeleton with CLI

**Files:**
- Create: `backfill_calibration.py`
- Test: `tests/test_backfill_calibration.py`

- [ ] **Step 1: Write test for CLI argument parsing**

```python
# tests/test_backfill_calibration.py
"""Tests for backfill_calibration.py"""
from datetime import date, timedelta
from unittest.mock import patch
import sys

import pytest


def test_default_date_range():
    """Default: 30 days ago to yesterday."""
    with patch.object(sys, 'argv', ['backfill_calibration.py']):
        from backfill_calibration import parse_args
        args = parse_args()
        today = date.today()
        assert args.end == today - timedelta(days=1)
        assert args.start == today - timedelta(days=31)
        assert args.dry_run is False


def test_custom_date_range():
    with patch.object(sys, 'argv', [
        'backfill_calibration.py',
        '--start', '2026-03-20',
        '--end', '2026-04-15'
    ]):
        from backfill_calibration import parse_args
        args = parse_args()
        assert args.start == date(2026, 3, 20)
        assert args.end == date(2026, 4, 15)


def test_dry_run_flag():
    with patch.object(sys, 'argv', ['backfill_calibration.py', '--dry-run']):
        from backfill_calibration import parse_args
        args = parse_args()
        assert args.dry_run is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_backfill_calibration.py -v`
Expected: FAIL with "No module named 'backfill_calibration'"

- [ ] **Step 3: Write minimal implementation**

```python
#!/usr/bin/env python3
"""
Backfill historical calibration data from Open-Meteo APIs.
Creates minimal market files with forecast_snapshots and actual_temp.
"""
import argparse
import json
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

from bot_v2 import LOCATIONS, TIMEZONES

DATA_DIR = Path(__file__).parent / "data" / "markets"


def parse_args():
    parser = argparse.ArgumentParser(
        description="Backfill calibration data from Open-Meteo APIs"
    )
    today = date.today()
    parser.add_argument(
        "--start",
        type=lambda s: datetime.strptime(s, "%Y-%m-%d").date(),
        default=today - timedelta(days=31),
        help="Start date YYYY-MM-DD (default: 30 days ago)",
    )
    parser.add_argument(
        "--end",
        type=lambda s: datetime.strptime(s, "%Y-%m-%d").date(),
        default=today - timedelta(days=1),
        help="End date YYYY-MM-DD (default: yesterday)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print plan without fetching or writing",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    print(f"Backfill: {args.start} to {args.end}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_backfill_calibration.py::test_default_date_range tests/test_backfill_calibration.py::test_custom_date_range tests/test_backfill_calibration.py::test_dry_run_flag -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backfill_calibration.py tests/test_backfill_calibration.py
git commit -m "feat: add backfill_calibration.py skeleton with CLI"
```

---

### Task 2: Fetch Actual Temperature

**Files:**
- Modify: `backfill_calibration.py`
- Modify: `tests/test_backfill_calibration.py`

- [ ] **Step 1: Write test for fetch_actual_temp**

```python
# Add to tests/test_backfill_calibration.py
from unittest.mock import MagicMock

def test_fetch_actual_temp_success():
    """Fetch actual temp from Open-Meteo archive API."""
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "daily": {"temperature_2m_max": [72.5]}
    }
    
    with patch('backfill_calibration.requests.get', return_value=mock_response):
        from backfill_calibration import fetch_actual_temp
        result = fetch_actual_temp("chicago", "2026-04-01")
    
    assert result == 72.5


def test_fetch_actual_temp_uses_correct_unit():
    """US cities use fahrenheit, international use celsius."""
    mock_response = MagicMock()
    mock_response.json.return_value = {"daily": {"temperature_2m_max": [20.0]}}
    
    with patch('backfill_calibration.requests.get', return_value=mock_response) as mock_get:
        from backfill_calibration import fetch_actual_temp
        fetch_actual_temp("london", "2026-04-01")
    
    call_url = mock_get.call_args[0][0]
    assert "temperature_unit=celsius" in call_url


def test_fetch_actual_temp_api_error_returns_none():
    """API errors return None, don't crash."""
    with patch('backfill_calibration.requests.get', side_effect=Exception("timeout")):
        from backfill_calibration import fetch_actual_temp
        result = fetch_actual_temp("chicago", "2026-04-01")
    
    assert result is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_backfill_calibration.py::test_fetch_actual_temp_success -v`
Expected: FAIL with "cannot import name 'fetch_actual_temp'"

- [ ] **Step 3: Write implementation**

```python
# Add to backfill_calibration.py after imports, before parse_args

def fetch_actual_temp(city_slug: str, date_str: str) -> float | None:
    """Fetch actual high temp from Open-Meteo historical API."""
    loc = LOCATIONS.get(city_slug)
    if not loc:
        return None

    temp_unit = "fahrenheit" if loc["unit"] == "F" else "celsius"
    url = (
        f"https://archive-api.open-meteo.com/v1/archive"
        f"?latitude={loc['lat']}&longitude={loc['lon']}"
        f"&start_date={date_str}&end_date={date_str}"
        f"&daily=temperature_2m_max"
        f"&temperature_unit={temp_unit}"
        f"&timezone=auto"
    )

    try:
        r = requests.get(url, timeout=10)
        data = r.json()
        temps = data.get("daily", {}).get("temperature_2m_max", [])
        if temps and temps[0] is not None:
            return round(temps[0], 1)
    except Exception as e:
        print(f"    [API Error] actual temp {city_slug} {date_str}: {e}")

    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_backfill_calibration.py::test_fetch_actual_temp_success tests/test_backfill_calibration.py::test_fetch_actual_temp_uses_correct_unit tests/test_backfill_calibration.py::test_fetch_actual_temp_api_error_returns_none -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backfill_calibration.py tests/test_backfill_calibration.py
git commit -m "feat: add fetch_actual_temp from Open-Meteo archive API"
```

---

### Task 3: Fetch ECMWF Forecasts with Lead Times

**Files:**
- Modify: `backfill_calibration.py`
- Modify: `tests/test_backfill_calibration.py`

- [ ] **Step 1: Write test for fetch_ecmwf_forecasts**

```python
# Add to tests/test_backfill_calibration.py

def test_fetch_ecmwf_forecasts_extracts_daily_max():
    """Convert hourly temps to daily max for d0, d1, d2."""
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "hourly": {
            "temperature_2m": [70, 75, 72, 68] * 6,  # 24 hours, max=75
            "temperature_2m_previous_day1": [65, 70, 68, 64] * 6,  # max=70
            "temperature_2m_previous_day2": [60, 65, 63, 58] * 6,  # max=65
        }
    }
    
    with patch('backfill_calibration.requests.get', return_value=mock_response):
        from backfill_calibration import fetch_ecmwf_forecasts
        result = fetch_ecmwf_forecasts("chicago", "2026-04-01")
    
    assert result == {"d0": 75, "d1": 70, "d2": 65}


def test_fetch_ecmwf_forecasts_handles_none_values():
    """Filter out None values from hourly arrays."""
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "hourly": {
            "temperature_2m": [70, None, 75, None],
            "temperature_2m_previous_day1": [None, None, None, None],
            "temperature_2m_previous_day2": [60, 65],
        }
    }
    
    with patch('backfill_calibration.requests.get', return_value=mock_response):
        from backfill_calibration import fetch_ecmwf_forecasts
        result = fetch_ecmwf_forecasts("chicago", "2026-04-01")
    
    assert result["d0"] == 75
    assert result["d1"] is None  # all None
    assert result["d2"] == 65


def test_fetch_ecmwf_forecasts_uses_timezone():
    """Use city timezone from TIMEZONES dict."""
    mock_response = MagicMock()
    mock_response.json.return_value = {"hourly": {
        "temperature_2m": [70], 
        "temperature_2m_previous_day1": [65],
        "temperature_2m_previous_day2": [60],
    }}
    
    with patch('backfill_calibration.requests.get', return_value=mock_response) as mock_get:
        from backfill_calibration import fetch_ecmwf_forecasts
        fetch_ecmwf_forecasts("tokyo", "2026-04-01")
    
    call_url = mock_get.call_args[0][0]
    assert "timezone=Asia%2FTokyo" in call_url or "timezone=Asia/Tokyo" in call_url
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_backfill_calibration.py::test_fetch_ecmwf_forecasts_extracts_daily_max -v`
Expected: FAIL with "cannot import name 'fetch_ecmwf_forecasts'"

- [ ] **Step 3: Write implementation**

```python
# Add to backfill_calibration.py after fetch_actual_temp

def _max_filtered(arr: list) -> float | None:
    """Return max of non-None values, or None if all None."""
    vals = [v for v in arr if v is not None]
    return round(max(vals), 1) if vals else None


def fetch_ecmwf_forecasts(city_slug: str, date_str: str) -> dict | None:
    """Fetch ECMWF forecasts for d0, d1, d2 from Previous Runs API."""
    loc = LOCATIONS.get(city_slug)
    if not loc:
        return None

    tz = TIMEZONES.get(city_slug, "UTC")
    temp_unit = "fahrenheit" if loc["unit"] == "F" else "celsius"
    url = (
        f"https://previous-runs-api.open-meteo.com/v1/forecast"
        f"?latitude={loc['lat']}&longitude={loc['lon']}"
        f"&start_date={date_str}&end_date={date_str}"
        f"&hourly=temperature_2m,temperature_2m_previous_day1,temperature_2m_previous_day2"
        f"&temperature_unit={temp_unit}"
        f"&timezone={tz}"
        f"&models=ecmwf_ifs025"
    )

    try:
        r = requests.get(url, timeout=15)
        data = r.json()
        hourly = data.get("hourly", {})
        return {
            "d0": _max_filtered(hourly.get("temperature_2m", [])),
            "d1": _max_filtered(hourly.get("temperature_2m_previous_day1", [])),
            "d2": _max_filtered(hourly.get("temperature_2m_previous_day2", [])),
        }
    except Exception as e:
        print(f"    [API Error] ECMWF {city_slug} {date_str}: {e}")
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_backfill_calibration.py -k "fetch_ecmwf" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backfill_calibration.py tests/test_backfill_calibration.py
git commit -m "feat: add fetch_ecmwf_forecasts with lead time extraction"
```

---

### Task 4: Fetch HRRR/GFS Forecasts (US Only)

**Files:**
- Modify: `backfill_calibration.py`
- Modify: `tests/test_backfill_calibration.py`

- [ ] **Step 1: Write test for fetch_hrrr_forecasts**

```python
# Add to tests/test_backfill_calibration.py

def test_fetch_hrrr_forecasts_us_city():
    """HRRR fetched for US cities using gfs_seamless model."""
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "hourly": {
            "temperature_2m": [72, 78, 75],
            "temperature_2m_previous_day1": [70, 74, 72],
            "temperature_2m_previous_day2": [68, 72, 70],
        }
    }
    
    with patch('backfill_calibration.requests.get', return_value=mock_response) as mock_get:
        from backfill_calibration import fetch_hrrr_forecasts
        result = fetch_hrrr_forecasts("chicago", "2026-04-01")
    
    call_url = mock_get.call_args[0][0]
    assert "models=gfs_seamless" in call_url
    assert result == {"d0": 78, "d1": 74, "d2": 72}


def test_fetch_hrrr_forecasts_non_us_returns_none():
    """Non-US cities return None immediately, no API call."""
    with patch('backfill_calibration.requests.get') as mock_get:
        from backfill_calibration import fetch_hrrr_forecasts
        result = fetch_hrrr_forecasts("london", "2026-04-01")
    
    mock_get.assert_not_called()
    assert result is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_backfill_calibration.py::test_fetch_hrrr_forecasts_us_city -v`
Expected: FAIL with "cannot import name 'fetch_hrrr_forecasts'"

- [ ] **Step 3: Write implementation**

```python
# Add to backfill_calibration.py after fetch_ecmwf_forecasts

def fetch_hrrr_forecasts(city_slug: str, date_str: str) -> dict | None:
    """Fetch HRRR/GFS forecasts for US cities only."""
    loc = LOCATIONS.get(city_slug)
    if not loc or loc.get("region") != "us":
        return None

    tz = TIMEZONES.get(city_slug, "UTC")
    temp_unit = "fahrenheit" if loc["unit"] == "F" else "celsius"
    url = (
        f"https://previous-runs-api.open-meteo.com/v1/forecast"
        f"?latitude={loc['lat']}&longitude={loc['lon']}"
        f"&start_date={date_str}&end_date={date_str}"
        f"&hourly=temperature_2m,temperature_2m_previous_day1,temperature_2m_previous_day2"
        f"&temperature_unit={temp_unit}"
        f"&timezone={tz}"
        f"&models=gfs_seamless"
    )

    try:
        r = requests.get(url, timeout=15)
        data = r.json()
        hourly = data.get("hourly", {})
        return {
            "d0": _max_filtered(hourly.get("temperature_2m", [])),
            "d1": _max_filtered(hourly.get("temperature_2m_previous_day1", [])),
            "d2": _max_filtered(hourly.get("temperature_2m_previous_day2", [])),
        }
    except Exception as e:
        print(f"    [API Error] HRRR {city_slug} {date_str}: {e}")
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_backfill_calibration.py -k "fetch_hrrr" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backfill_calibration.py tests/test_backfill_calibration.py
git commit -m "feat: add fetch_hrrr_forecasts for US cities"
```

---

### Task 5: Build Market File Structure

**Files:**
- Modify: `backfill_calibration.py`
- Modify: `tests/test_backfill_calibration.py`

- [ ] **Step 1: Write test for build_market_file**

```python
# Add to tests/test_backfill_calibration.py

def test_build_market_file_us_city():
    """US city: best=hrrr when available."""
    from backfill_calibration import build_market_file
    
    result = build_market_file(
        city_slug="chicago",
        date_str="2026-04-01",
        actual_temp=78.5,
        ecmwf={"d0": 80.3, "d1": 78.3, "d2": 74.5},
        hrrr={"d0": 79.1, "d1": 77.5, "d2": 75.2},
    )
    
    assert result["city"] == "chicago"
    assert result["date"] == "2026-04-01"
    assert result["status"] == "resolved"
    assert result["actual_temp"] == 78.5
    
    snaps = result["forecast_snapshots"]
    assert len(snaps) == 3
    
    # D+2 first, D+0 last
    assert snaps[0]["horizon"] == "D+2"
    assert snaps[0]["best"] == 75.2
    assert snaps[0]["best_source"] == "hrrr"
    
    assert snaps[2]["horizon"] == "D+0"
    assert snaps[2]["best"] == 79.1


def test_build_market_file_international_city():
    """International city: best=ecmwf, hrrr=None."""
    from backfill_calibration import build_market_file
    
    result = build_market_file(
        city_slug="london",
        date_str="2026-04-01",
        actual_temp=18.0,
        ecmwf={"d0": 17.5, "d1": 16.0, "d2": 14.0},
        hrrr=None,
    )
    
    snaps = result["forecast_snapshots"]
    assert snaps[2]["hrrr"] is None
    assert snaps[2]["best"] == 17.5
    assert snaps[2]["best_source"] == "ecmwf"


def test_build_market_file_snapshot_timestamps():
    """Snapshots have correct ts and hours_left."""
    from backfill_calibration import build_market_file
    
    result = build_market_file(
        city_slug="chicago",
        date_str="2026-04-01",
        actual_temp=78.5,
        ecmwf={"d0": 80, "d1": 78, "d2": 75},
        hrrr={"d0": 79, "d1": 77, "d2": 74},
    )
    
    snaps = result["forecast_snapshots"]
    
    # D+2: 60 hours before market date noon
    assert snaps[0]["hours_left"] == 60.0
    assert "2026-03-30" in snaps[0]["ts"]
    
    # D+1: 36 hours
    assert snaps[1]["hours_left"] == 36.0
    assert "2026-03-31" in snaps[1]["ts"]
    
    # D+0: 12 hours
    assert snaps[2]["hours_left"] == 12.0
    assert "2026-04-01" in snaps[2]["ts"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_backfill_calibration.py::test_build_market_file_us_city -v`
Expected: FAIL with "cannot import name 'build_market_file'"

- [ ] **Step 3: Write implementation**

```python
# Add to backfill_calibration.py after fetch_hrrr_forecasts

def build_market_file(
    city_slug: str,
    date_str: str,
    actual_temp: float,
    ecmwf: dict,
    hrrr: dict | None,
) -> dict:
    """Build minimal market file structure for calibration."""
    loc = LOCATIONS.get(city_slug, {})
    is_us = loc.get("region") == "us"
    
    # Parse market date to compute snapshot timestamps
    market_date = datetime.strptime(date_str, "%Y-%m-%d")
    
    snapshots = []
    for horizon, key, hours_back in [("D+2", "d2", 48), ("D+1", "d1", 24), ("D+0", "d0", 0)]:
        # Snapshot taken at noon, hours_left is time until market date noon
        snap_dt = market_date - timedelta(hours=hours_back)
        hours_left = 60.0 - (24.0 * (2 - int(key[1])))  # D+2=60, D+1=36, D+0=12
        
        ecmwf_val = ecmwf.get(key) if ecmwf else None
        hrrr_val = hrrr.get(key) if hrrr else None
        
        # best = HRRR if US and available, else ECMWF
        if is_us and hrrr_val is not None:
            best = hrrr_val
            best_source = "hrrr"
        else:
            best = ecmwf_val
            best_source = "ecmwf"
        
        snapshots.append({
            "ts": snap_dt.strftime("%Y-%m-%dT12:00:00Z"),
            "horizon": horizon,
            "hours_left": hours_left,
            "ecmwf": ecmwf_val,
            "hrrr": hrrr_val,
            "metar": None,
            "best": best,
            "best_source": best_source,
        })
    
    return {
        "city": city_slug,
        "date": date_str,
        "status": "resolved",
        "actual_temp": actual_temp,
        "forecast_snapshots": snapshots,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_backfill_calibration.py -k "build_market_file" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backfill_calibration.py tests/test_backfill_calibration.py
git commit -m "feat: add build_market_file with correct snapshot ordering"
```

---

### Task 6: Skip Check Logic

**Files:**
- Modify: `backfill_calibration.py`
- Modify: `tests/test_backfill_calibration.py`

- [ ] **Step 1: Write test for should_skip**

```python
# Add to tests/test_backfill_calibration.py

def test_should_skip_complete_file(tmp_path):
    """Skip file with actual_temp and 3+ snapshots."""
    from backfill_calibration import should_skip
    
    market_file = tmp_path / "chicago_2026-04-01.json"
    market_file.write_text(json.dumps({
        "city": "chicago",
        "actual_temp": 78.5,
        "forecast_snapshots": [
            {"horizon": "D+2"}, {"horizon": "D+1"}, {"horizon": "D+0"}
        ],
    }))
    
    assert should_skip(market_file) is True


def test_should_skip_missing_actual_temp(tmp_path):
    """Don't skip if actual_temp missing."""
    from backfill_calibration import should_skip
    
    market_file = tmp_path / "chicago_2026-04-01.json"
    market_file.write_text(json.dumps({
        "city": "chicago",
        "forecast_snapshots": [
            {"horizon": "D+2"}, {"horizon": "D+1"}, {"horizon": "D+0"}
        ],
    }))
    
    assert should_skip(market_file) is False


def test_should_skip_insufficient_snapshots(tmp_path):
    """Don't skip if fewer than 3 snapshots."""
    from backfill_calibration import should_skip
    
    market_file = tmp_path / "chicago_2026-04-01.json"
    market_file.write_text(json.dumps({
        "city": "chicago",
        "actual_temp": 78.5,
        "forecast_snapshots": [{"horizon": "D+0"}],
    }))
    
    assert should_skip(market_file) is False


def test_should_skip_nonexistent_file(tmp_path):
    """Don't skip if file doesn't exist."""
    from backfill_calibration import should_skip
    
    market_file = tmp_path / "nonexistent.json"
    assert should_skip(market_file) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_backfill_calibration.py::test_should_skip_complete_file -v`
Expected: FAIL with "cannot import name 'should_skip'"

- [ ] **Step 3: Write implementation**

```python
# Add to backfill_calibration.py after build_market_file

def should_skip(market_file: Path) -> bool:
    """Check if file already has complete calibration data."""
    if not market_file.exists():
        return False
    
    try:
        data = json.loads(market_file.read_text())
        has_actual = data.get("actual_temp") is not None
        has_snapshots = len(data.get("forecast_snapshots", [])) >= 3
        return has_actual and has_snapshots
    except (json.JSONDecodeError, KeyError):
        return False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_backfill_calibration.py -k "should_skip" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backfill_calibration.py tests/test_backfill_calibration.py
git commit -m "feat: add should_skip for idempotent backfill"
```

---

### Task 7: Main Backfill Loop

**Files:**
- Modify: `backfill_calibration.py`
- Modify: `tests/test_backfill_calibration.py`

- [ ] **Step 1: Write test for backfill function**

```python
# Add to tests/test_backfill_calibration.py
from datetime import date

def test_backfill_creates_market_files(tmp_path):
    """backfill() creates market files for date range."""
    from backfill_calibration import backfill
    
    mock_actual = MagicMock(return_value=75.0)
    mock_ecmwf = MagicMock(return_value={"d0": 74, "d1": 72, "d2": 70})
    mock_hrrr = MagicMock(return_value={"d0": 75, "d1": 73, "d2": 71})
    
    with patch('backfill_calibration.fetch_actual_temp', mock_actual), \
         patch('backfill_calibration.fetch_ecmwf_forecasts', mock_ecmwf), \
         patch('backfill_calibration.fetch_hrrr_forecasts', mock_hrrr), \
         patch('backfill_calibration.DATA_DIR', tmp_path), \
         patch('backfill_calibration.time.sleep'):
        
        stats = backfill(
            start=date(2026, 4, 1),
            end=date(2026, 4, 2),
            cities=["chicago"],
            dry_run=False,
        )
    
    assert stats["created"] == 2
    assert (tmp_path / "chicago_2026-04-01.json").exists()
    assert (tmp_path / "chicago_2026-04-02.json").exists()


def test_backfill_skips_existing_complete(tmp_path):
    """backfill() skips files that already have complete data."""
    from backfill_calibration import backfill
    
    # Pre-create complete file
    (tmp_path / "chicago_2026-04-01.json").write_text(json.dumps({
        "city": "chicago",
        "actual_temp": 75.0,
        "forecast_snapshots": [{"h": "D+2"}, {"h": "D+1"}, {"h": "D+0"}],
    }))
    
    mock_actual = MagicMock(return_value=75.0)
    mock_ecmwf = MagicMock(return_value={"d0": 74, "d1": 72, "d2": 70})
    mock_hrrr = MagicMock(return_value={"d0": 75, "d1": 73, "d2": 71})
    
    with patch('backfill_calibration.fetch_actual_temp', mock_actual), \
         patch('backfill_calibration.fetch_ecmwf_forecasts', mock_ecmwf), \
         patch('backfill_calibration.fetch_hrrr_forecasts', mock_hrrr), \
         patch('backfill_calibration.DATA_DIR', tmp_path), \
         patch('backfill_calibration.time.sleep'):
        
        stats = backfill(
            start=date(2026, 4, 1),
            end=date(2026, 4, 2),
            cities=["chicago"],
            dry_run=False,
        )
    
    assert stats["skipped"] == 1
    assert stats["created"] == 1


def test_backfill_dry_run_no_writes(tmp_path):
    """dry_run=True prints plan but doesn't write files."""
    from backfill_calibration import backfill
    
    with patch('backfill_calibration.DATA_DIR', tmp_path):
        stats = backfill(
            start=date(2026, 4, 1),
            end=date(2026, 4, 2),
            cities=["chicago"],
            dry_run=True,
        )
    
    assert stats["created"] == 0
    assert not (tmp_path / "chicago_2026-04-01.json").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_backfill_calibration.py::test_backfill_creates_market_files -v`
Expected: FAIL with "cannot import name 'backfill'"

- [ ] **Step 3: Write implementation**

```python
# Add to backfill_calibration.py after should_skip

def backfill(
    start: date,
    end: date,
    cities: list[str],
    dry_run: bool = False,
) -> dict:
    """Run backfill for date range and cities. Returns stats dict."""
    stats = {"created": 0, "skipped": 0, "failed": 0}
    
    # Generate all dates
    current = start
    dates = []
    while current <= end:
        dates.append(current.strftime("%Y-%m-%d"))
        current += timedelta(days=1)
    
    total = len(dates) * len(cities)
    print(f"\nBackfill: {len(dates)} days × {len(cities)} cities = {total} markets")
    
    if dry_run:
        print("\n[DRY RUN] Would process:")
        for date_str in dates[:3]:
            for city in cities[:3]:
                print(f"  {city}_{date_str}")
        if total > 9:
            print(f"  ... and {total - 9} more")
        return stats
    
    for date_str in dates:
        for city in cities:
            market_file = DATA_DIR / f"{city}_{date_str}.json"
            
            # Skip check
            if should_skip(market_file):
                print(f"  [SKIP] {city}_{date_str}")
                stats["skipped"] += 1
                continue
            
            # Fetch data
            actual = fetch_actual_temp(city, date_str)
            if actual is None:
                print(f"  [FAIL] {city}_{date_str}: no actual temp")
                stats["failed"] += 1
                time.sleep(0.1)
                continue
            
            ecmwf = fetch_ecmwf_forecasts(city, date_str)
            if ecmwf is None:
                print(f"  [FAIL] {city}_{date_str}: no ECMWF data")
                stats["failed"] += 1
                time.sleep(0.1)
                continue
            
            hrrr = fetch_hrrr_forecasts(city, date_str)  # None for non-US
            
            # Build and write
            market = build_market_file(city, date_str, actual, ecmwf, hrrr)
            market_file.write_text(json.dumps(market, indent=2))
            print(f"  [OK] {city}_{date_str}: {actual}°")
            stats["created"] += 1
            
            time.sleep(0.1)
    
    return stats
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_backfill_calibration.py -k "backfill" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backfill_calibration.py tests/test_backfill_calibration.py
git commit -m "feat: add backfill main loop with skip/fail handling"
```

---

### Task 8: Wire Up Main Entry Point

**Files:**
- Modify: `backfill_calibration.py`

- [ ] **Step 1: Write test for main entry point**

```python
# Add to tests/test_backfill_calibration.py

def test_main_runs_backfill(tmp_path):
    """main() parses args and runs backfill."""
    mock_backfill = MagicMock(return_value={"created": 10, "skipped": 5, "failed": 0})
    
    with patch.object(sys, 'argv', [
        'backfill_calibration.py',
        '--start', '2026-04-01',
        '--end', '2026-04-02',
    ]), patch('backfill_calibration.backfill', mock_backfill):
        from backfill_calibration import main
        main()
    
    mock_backfill.assert_called_once()
    call_kwargs = mock_backfill.call_args[1]
    assert call_kwargs['start'] == date(2026, 4, 1)
    assert call_kwargs['end'] == date(2026, 4, 2)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_backfill_calibration.py::test_main_runs_backfill -v`
Expected: FAIL with "cannot import name 'main'"

- [ ] **Step 3: Write implementation**

Replace the `if __name__ == "__main__"` block:

```python
# Replace existing __main__ block in backfill_calibration.py

def main():
    args = parse_args()
    
    print("=" * 60)
    print("  CALIBRATION BACKFILL")
    print("=" * 60)
    print(f"  Range: {args.start} to {args.end}")
    print(f"  Cities: {len(LOCATIONS)}")
    print(f"  Dry run: {args.dry_run}")
    
    stats = backfill(
        start=args.start,
        end=args.end,
        cities=list(LOCATIONS.keys()),
        dry_run=args.dry_run,
    )
    
    print("\n" + "=" * 60)
    print("  SUMMARY")
    print("=" * 60)
    print(f"  Created: {stats['created']}")
    print(f"  Skipped: {stats['skipped']}")
    print(f"  Failed:  {stats['failed']}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_backfill_calibration.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add backfill_calibration.py tests/test_backfill_calibration.py
git commit -m "feat: wire up main entry point with summary output"
```

---

### Task 9: End-to-End Verification

**Files:**
- Run: `backfill_calibration.py`

- [ ] **Step 1: Run full test suite**

Run: `pytest tests/test_backfill_calibration.py -v`
Expected: ALL PASS

- [ ] **Step 2: Run dry-run to verify output**

Run: `python backfill_calibration.py --dry-run --start 2026-04-20 --end 2026-04-21`
Expected output:
```
============================================================
  CALIBRATION BACKFILL
============================================================
  Range: 2026-04-20 to 2026-04-21
  Cities: 20
  Dry run: True

Backfill: 2 days × 20 cities = 40 markets

[DRY RUN] Would process:
  nyc_2026-04-20
  chicago_2026-04-20
  miami_2026-04-20
  ... and 31 more

============================================================
  SUMMARY
============================================================
  Created: 0
  Skipped: 0
  Failed:  0
```

- [ ] **Step 3: Run actual backfill for 2 days (limited test)**

Run: `python backfill_calibration.py --start 2026-04-20 --end 2026-04-21`
Expected: Creates 40 market files in `data/markets/`

- [ ] **Step 4: Verify a created file**

Run: `cat data/markets/chicago_2026-04-20.json | python -m json.tool`
Expected: Valid JSON with city, date, status, actual_temp, forecast_snapshots (3 items, D+2 first)

- [ ] **Step 5: Verify idempotency by re-running**

Run: `python backfill_calibration.py --start 2026-04-20 --end 2026-04-21`
Expected: All 40 markets show [SKIP], created=0

- [ ] **Step 6: Final commit**

```bash
git add -A
git commit -m "feat: complete backfill_calibration.py implementation"
```

---

## Summary

This plan creates `backfill_calibration.py` in 9 tasks:

1. CLI skeleton with `--start`, `--end`, `--dry-run`
2. `fetch_actual_temp()` from archive API
3. `fetch_ecmwf_forecasts()` with d0/d1/d2 extraction
4. `fetch_hrrr_forecasts()` for US cities only
5. `build_market_file()` with correct snapshot ordering
6. `should_skip()` for idempotency
7. `backfill()` main loop
8. `main()` entry point with summary
9. End-to-end verification

Total: ~250 lines of script, ~180 lines of tests.
