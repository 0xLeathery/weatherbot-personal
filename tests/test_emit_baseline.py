"""Tests for walkforward_test.py --emit-baseline.

Produces data/backtest_baseline.json with test-set stats, simulation
outcome, and a config snapshot. Dashboard reads this to render the
live-vs-backtest header strip and to fire a staleness warning when
config drifts from the snapshot.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parent.parent


def _seed_market_files(markets_dir: Path, n: int = 30):
    """Seed enough resolved-with-spread markets for walkforward to compute stats."""
    from datetime import datetime, timezone, timedelta
    markets_dir.mkdir(parents=True)
    base = datetime(2026, 4, 1, tzinfo=timezone.utc)
    for i in range(n):
        d = base + timedelta(days=i)
        date_str = d.strftime("%Y-%m-%d")
        # Include both ecmwf+hrrr in forecast_snapshots and an actual_temp
        # so walkforward picks it up. Spread > 2 to qualify.
        mkt = {
            "market_id":   f"mkt_{i}",
            "city":        "dallas",
            "city_name":   "Dallas",
            "date":        date_str,
            "unit":        "F",
            "station":     "KDAL",
            "event_end_date": d.isoformat(),
            "actual_temp": 70 if i % 2 == 0 else 64,
            "forecast_snapshots": [{
                "ts":    d.isoformat(),
                "ecmwf": 65,
                "hrrr":  68,
                "metar": None,
            }],
            "all_outcomes": [{
                "market_id": f"mkt_{i}_b",
                "question":  "Will high be 67-69?",
                "range":     [67, 69],
                "price":     0.40,
                "bid":       0.38,
                "ask":       0.42,
                "spread":    0.04,
                "volume":    1000,
            }],
            "status": "resolved",
            "position": None,
            "pnl": 5.0 if i % 2 == 0 else -3.0,
            "resolved_outcome": "yes" if i % 2 == 0 else "no",
            "forecast_temp": 68,
            "ecmwf_temp": 65,
        }
        (markets_dir / f"dallas_{date_str}.json").write_text(json.dumps(mkt))


def _run(args, cwd):
    return subprocess.run(
        [sys.executable, str(REPO / "walkforward_test.py")] + list(args),
        cwd=str(cwd),
        capture_output=True,
        text=True,
    )


class TestEmitBaseline:
    def test_writes_well_formed_baseline(self, tmp_path):
        markets = tmp_path / "data" / "markets"
        _seed_market_files(markets, n=30)

        result = _run(
            ["--data-dir", str(markets), "--emit-baseline", "--threshold", "2.0", "--simulate", "1000"],
            cwd=tmp_path,
        )
        assert result.returncode == 0, result.stderr

        baseline = tmp_path / "data" / "backtest_baseline.json"
        assert baseline.exists()
        data = json.loads(baseline.read_text())

        assert data["source"] == "walkforward_test.py"
        assert "generated_at" in data
        assert data["config_snapshot"]["spread_threshold"] == 2.0
        assert data["config_snapshot"]["max_bet"] == 20.0
        assert data["config_snapshot"]["kelly_fraction"] == 0.25
        # min_ev intentionally absent — walkforward doesn't filter on it
        assert "min_ev" not in data["config_snapshot"]
        assert "test_set" in data
        assert "simulation" in data
        assert data["simulation"]["starting_balance"] == 1000.0

    def test_emit_baseline_without_simulate_implies_default(self, tmp_path):
        markets = tmp_path / "data" / "markets"
        _seed_market_files(markets, n=30)

        result = _run(
            ["--data-dir", str(markets), "--emit-baseline", "--threshold", "2.0"],
            cwd=tmp_path,
        )
        assert result.returncode == 0, result.stderr

        baseline = tmp_path / "data" / "backtest_baseline.json"
        data = json.loads(baseline.read_text())
        assert data["simulation"]["starting_balance"] == 1000.0  # default
