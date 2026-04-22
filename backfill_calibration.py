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
