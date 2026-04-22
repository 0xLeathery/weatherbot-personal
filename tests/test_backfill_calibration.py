"""Tests for backfill_calibration.py"""
from datetime import date, timedelta
from unittest.mock import patch, MagicMock
import sys

import pytest


def test_default_date_range():
    """Default: 30 days ago to yesterday."""
    with patch.object(sys, 'argv', ['backfill_calibration.py']):
        from backfill_calibration import parse_args
        args = parse_args()
        today = date.today()
        assert args.end == today - timedelta(days=1)
        assert args.start == today - timedelta(days=30)
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
