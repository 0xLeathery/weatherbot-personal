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
