"""Tests for tools/backfill_resolution.py."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from tools.backfill_resolution import main


def _setup(tmp_path, markets):
    d = tmp_path / "markets"
    d.mkdir(parents=True)
    for i, m in enumerate(markets):
        (d / f"m{i}.json").write_text(json.dumps(m), encoding="utf-8")
    return tmp_path


def _mkt(status="closed", actual_temp=None, city="dallas", date="2026-04-01"):
    return {"city": city, "date": date, "status": status, "actual_temp": actual_temp}


def test_fills_missing_actual_temp(tmp_path):
    data = _setup(tmp_path, [_mkt(status="closed")])
    with patch("tools.backfill_resolution.get_actual_temp", return_value=72.5):
        rc = main(["--data", str(data)])
    assert rc == 0
    saved = json.loads((data / "markets" / "m0.json").read_text())
    assert saved["actual_temp"] == pytest.approx(72.5)


def test_dry_run_does_not_write(tmp_path):
    data = _setup(tmp_path, [_mkt(status="closed")])
    with patch("tools.backfill_resolution.get_actual_temp", return_value=72.5):
        rc = main(["--data", str(data), "--dry-run"])
    assert rc == 0
    saved = json.loads((data / "markets" / "m0.json").read_text())
    assert saved["actual_temp"] is None


def test_skips_open_markets(tmp_path):
    data = _setup(tmp_path, [_mkt(status="open")])
    with patch("tools.backfill_resolution.get_actual_temp", return_value=72.5) as mock_fn:
        main(["--data", str(data)])
    mock_fn.assert_not_called()
    saved = json.loads((data / "markets" / "m0.json").read_text())
    assert saved["actual_temp"] is None


def test_skips_markets_already_filled(tmp_path):
    data = _setup(tmp_path, [_mkt(status="closed", actual_temp=70.0)])
    with patch("tools.backfill_resolution.get_actual_temp", return_value=72.5) as mock_fn:
        main(["--data", str(data)])
    mock_fn.assert_not_called()


def test_returns_1_when_api_miss(tmp_path):
    data = _setup(tmp_path, [_mkt(status="closed")])
    with patch("tools.backfill_resolution.get_actual_temp", return_value=None):
        rc = main(["--data", str(data)])
    assert rc == 1


def test_handles_resolved_status(tmp_path):
    data = _setup(tmp_path, [_mkt(status="resolved")])
    with patch("tools.backfill_resolution.get_actual_temp", return_value=68.0):
        rc = main(["--data", str(data)])
    assert rc == 0
    saved = json.loads((data / "markets" / "m0.json").read_text())
    assert saved["actual_temp"] == pytest.approx(68.0)
