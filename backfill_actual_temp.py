#!/usr/bin/env python3
"""
Backfill actual_temp from Open-Meteo historical weather API.
Falls back to resolved market bucket if API fails.
"""

import json
import time
from datetime import datetime
from pathlib import Path

import requests

DATA_DIR = Path(__file__).parent / "data" / "markets"

LOCATIONS = {
    "nyc":          {"lat": 40.6413, "lon": -73.7781, "unit": "F"},
    "chicago":      {"lat": 41.9742, "lon": -87.9073, "unit": "F"},
    "miami":        {"lat": 25.7959, "lon": -80.2870, "unit": "F"},
    "dallas":       {"lat": 32.8471, "lon": -96.8518, "unit": "F"},
    "seattle":      {"lat": 47.4502, "lon": -122.3088, "unit": "F"},
    "atlanta":      {"lat": 33.6407, "lon": -84.4277, "unit": "F"},
    "london":       {"lat": 51.5048, "lon": 0.0495, "unit": "C"},
    "paris":        {"lat": 48.9962, "lon": 2.5979, "unit": "C"},
    "munich":       {"lat": 48.3537, "lon": 11.7750, "unit": "C"},
    "ankara":       {"lat": 40.1281, "lon": 32.9951, "unit": "C"},
    "seoul":        {"lat": 37.4602, "lon": 126.4407, "unit": "C"},
    "tokyo":        {"lat": 35.5494, "lon": 139.7798, "unit": "C"},
    "shanghai":     {"lat": 31.1443, "lon": 121.8083, "unit": "C"},
    "singapore":    {"lat": 1.3502, "lon": 103.9940, "unit": "C"},
    "lucknow":      {"lat": 26.7606, "lon": 80.8893, "unit": "C"},
    "tel-aviv":     {"lat": 32.0114, "lon": 34.8867, "unit": "C"},
    "toronto":      {"lat": 43.6772, "lon": -79.6306, "unit": "C"},
    "sao-paulo":    {"lat": -23.4356, "lon": -46.4731, "unit": "C"},
    "buenos-aires": {"lat": -34.8222, "lon": -58.5358, "unit": "C"},
    "wellington":   {"lat": -41.3272, "lon": 174.8052, "unit": "C"},
}


def fetch_actual_temp(city_slug, date_str):
    """Fetch actual high temp from Open-Meteo historical API."""
    loc = LOCATIONS.get(city_slug)
    if not loc:
        return None

    temp_unit = "fahrenheit" if loc["unit"] == "F" else "celsius"
    url = (
        f"https://archive-api.open-meteo.com/v1/archive"
        f"?latitude={loc['lat']}&longitude={loc['lon']}"
        f"&start_date={date_str}&end_date={date_str}"
        f"&daily=temperature_2m_max&temperature_unit={temp_unit}"
        f"&timezone=auto"
    )

    try:
        r = requests.get(url, timeout=10)
        data = r.json()
        temps = data.get("daily", {}).get("temperature_2m_max", [])
        if temps and temps[0] is not None:
            return round(temps[0], 1)
    except Exception as e:
        print(f"    [API Error] {city_slug} {date_str}: {e}")

    return None


def get_bucket_midpoint(data):
    """Fallback: get midpoint of winning bucket from market outcomes."""
    outcomes = data.get("all_outcomes", [])
    if not outcomes:
        return None

    winner = max(outcomes, key=lambda o: o.get("bid", 0))
    if winner.get("bid", 0) < 0.9:
        return None

    rng = winner.get("range", [])
    if len(rng) != 2:
        return None

    low, high = rng
    if low < -100:
        return high
    elif high > 100:
        return low
    else:
        return (low + high) / 2


def backfill():
    updated_api = 0
    updated_bucket = 0
    skipped = 0
    failed = 0

    market_files = sorted(DATA_DIR.glob("*.json"))
    print(f"Found {len(market_files)} market files\n")

    for market_file in market_files:
        data = json.loads(market_file.read_text())

        # Extract city and date from filename
        name = market_file.stem  # e.g., "chicago_2026-04-19"
        parts = name.rsplit("_", 1)
        if len(parts) != 2:
            continue
        city_slug, date_str = parts

        # Skip future dates (today is 2026-04-22)
        try:
            market_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            if market_date >= datetime.now().date():
                skipped += 1
                continue
        except ValueError:
            continue

        # Try Open-Meteo first
        actual = fetch_actual_temp(city_slug, date_str)
        source = "API"

        # Fallback to bucket midpoint
        if actual is None:
            actual = get_bucket_midpoint(data)
            source = "bucket"

        if actual is not None:
            data["actual_temp"] = actual
            market_file.write_text(json.dumps(data, indent=2))
            print(f"  {name}: {actual}° ({source})")
            if source == "API":
                updated_api += 1
            else:
                updated_bucket += 1
        else:
            failed += 1
            print(f"  {name}: FAILED (no data)")

        time.sleep(0.1)  # Rate limit

    print(f"\nDone:")
    print(f"  {updated_api} updated from Open-Meteo API")
    print(f"  {updated_bucket} updated from bucket midpoint (fallback)")
    print(f"  {skipped} skipped (future dates)")
    print(f"  {failed} failed (no data)")


if __name__ == "__main__":
    print("Backfilling actual_temp from Open-Meteo historical API...\n")
    backfill()
