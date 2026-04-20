"""Tests for run_calibration() — the per-(city, source) sigma estimator."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from bot_v2 import run_calibration, CALIBRATION_MIN


def _mkt(city="dallas", actual_temp=72.0, snapshots=None):
    return {
        "city": city,
        "actual_temp": actual_temp,
        "forecast_snapshots": snapshots or [],
    }


def _snap(**kwargs):
    base = {"ts": "2026-04-01T00:00:00Z", "ecmwf": None, "hrrr": None, "metar": None}
    base.update(kwargs)
    return base


# --- filter tests ---

def test_skips_markets_without_actual_temp():
    mkt = _mkt(actual_temp=None)
    mkt["forecast_snapshots"] = [_snap(ecmwf=70.0)]
    with patch("bot_v2.CALIBRATION_FILE") as mock_path:
        mock_path.read_text.return_value = "{}"
        mock_path.write_text = lambda *a, **kw: None
        cal = run_calibration([mkt])
    assert cal == {}


def test_includes_markets_with_actual_temp():
    snaps = [_snap(ecmwf=70.0 + i) for i in range(CALIBRATION_MIN)]
    mkts = [_mkt(actual_temp=72.0, snapshots=snaps) for _ in range(CALIBRATION_MIN)]
    with patch("bot_v2.CALIBRATION_FILE") as mock_path:
        mock_path.read_text.return_value = "{}"
        written = {}
        mock_path.write_text.side_effect = lambda txt, **kw: written.update({"data": txt})
        run_calibration(mkts)
    assert "dallas_ecmwf" in json.loads(written["data"])


# --- snapshot lookup tests ---

def test_uses_last_snapshot_for_source():
    # Last ecmwf snapshot = 71.0, actual = 72.0 → error = 1.0
    snaps = [_snap(ecmwf=69.0), _snap(ecmwf=71.0)]
    mkts = [_mkt(actual_temp=72.0, snapshots=snaps) for _ in range(CALIBRATION_MIN)]
    with patch("bot_v2.CALIBRATION_FILE") as mock_path:
        mock_path.read_text.return_value = "{}"
        written = {}
        mock_path.write_text.side_effect = lambda txt, **kw: written.update({"data": txt})
        run_calibration(mkts)
    cal = json.loads(written["data"])
    assert "dallas_ecmwf" in cal
    assert abs(cal["dallas_ecmwf"]["sigma"] - 1.0) < 0.01


def test_skips_source_with_no_snapshot_values():
    # hrrr snapshots all None → hrrr key absent from cal
    snaps = [_snap(ecmwf=70.0, hrrr=None) for _ in range(CALIBRATION_MIN)]
    mkts = [_mkt(actual_temp=72.0, snapshots=snaps) for _ in range(CALIBRATION_MIN)]
    with patch("bot_v2.CALIBRATION_FILE") as mock_path:
        mock_path.read_text.return_value = "{}"
        written = {}
        mock_path.write_text.side_effect = lambda txt, **kw: written.update({"data": txt})
        run_calibration(mkts)
    cal = json.loads(written["data"])
    assert "dallas_hrrr" not in cal
    assert "dallas_ecmwf" in cal


def test_below_min_count_skips_calibration():
    snaps = [_snap(ecmwf=70.0)]
    mkts = [_mkt(actual_temp=72.0, snapshots=snaps) for _ in range(CALIBRATION_MIN - 1)]
    with patch("bot_v2.CALIBRATION_FILE") as mock_path:
        mock_path.read_text.return_value = "{}"
        written = {}
        mock_path.write_text.side_effect = lambda txt, **kw: written.update({"data": txt})
        run_calibration(mkts)
    cal = json.loads(written.get("data", "{}"))
    assert "dallas_ecmwf" not in cal
