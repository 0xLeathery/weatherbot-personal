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
