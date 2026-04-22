#!/usr/bin/env python3
"""
Backfill historical calibration data from Open-Meteo APIs.
Creates minimal market files with forecast_snapshots and actual_temp.
"""
import argparse
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


if __name__ == "__main__":
    args = parse_args()
    print(f"Backfill: {args.start} to {args.end}")
