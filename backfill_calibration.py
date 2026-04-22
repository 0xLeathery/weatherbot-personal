#!/usr/bin/env python3
"""
Backfill historical calibration data from Open-Meteo APIs.
Creates minimal market files with forecast_snapshots and actual_temp.
"""
from __future__ import annotations

import argparse
import json
import time
import requests
from datetime import date, datetime, timedelta
from pathlib import Path

from bot_v2 import LOCATIONS, TIMEZONES

DATA_DIR = Path(__file__).parent / "data" / "markets"


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
        r.raise_for_status()
        data = r.json()
        temps = data.get("daily", {}).get("temperature_2m_max", [])
        if temps and temps[0] is not None:
            return round(temps[0], 1)
    except Exception as e:
        print(f"    [API Error] actual temp {city_slug} {date_str}: {e}")

    return None


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
        r.raise_for_status()
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
        r.raise_for_status()
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


def parse_args():
    parser = argparse.ArgumentParser(
        description="Backfill calibration data from Open-Meteo APIs"
    )
    today = date.today()
    parser.add_argument(
        "--start",
        type=lambda s: datetime.strptime(s, "%Y-%m-%d").date(),
        default=today - timedelta(days=30),
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
