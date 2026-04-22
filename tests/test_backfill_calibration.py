"""Tests for backfill_calibration.py"""
from datetime import date, timedelta
from unittest.mock import patch, MagicMock
import json
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


def test_should_skip_corrupt_json(tmp_path):
    """Don't skip if file contains invalid JSON."""
    from backfill_calibration import should_skip

    market_file = tmp_path / "corrupt.json"
    market_file.write_text("{ not valid json")
    assert should_skip(market_file) is False
