"""Tests for tools/reset.py — the explicit reset script.

reset.py replaces today's manual 'rm state.json && rm markets/*.json'
procedure. Crucially, it appends a {type: reset} row to data/closures.jsonl
BEFORE deleting state, so the ledger preserves a session boundary across
the reset.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parent.parent


def _run(args, cwd):
    result = subprocess.run(
        [sys.executable, str(REPO / "tools" / "reset.py")] + list(args),
        cwd=str(cwd),
        capture_output=True,
        text=True,
    )
    return result


class TestResetDryRun:
    def test_dry_run_makes_no_changes(self, tmp_path):
        data = tmp_path / "data"
        (data / "markets").mkdir(parents=True)
        (data / "state.json").write_text(json.dumps({
            "balance": 1000.0, "starting_balance": 1000.0,
            "total_trades": 5, "wins": 3, "losses": 2,
            "peak_balance": 1050.0, "realized_pnl": 12.50,
        }))
        (data / "markets" / "a.json").write_text("{}")
        (data / "closures.jsonl").write_text("")

        result = _run(["--dry-run", "--yes"], cwd=tmp_path)
        assert result.returncode == 0, result.stderr

        # State and markets file untouched
        assert (data / "state.json").exists()
        assert (data / "markets" / "a.json").exists()
        # Ledger has no reset row
        assert (data / "closures.jsonl").read_text() == ""

    def test_real_run_writes_marker_then_deletes(self, tmp_path):
        data = tmp_path / "data"
        (data / "markets").mkdir(parents=True)
        (data / "state.json").write_text(json.dumps({
            "balance": 800.0, "starting_balance": 1000.0,
            "total_trades": 5, "wins": 1, "losses": 4,
            "peak_balance": 1010.0, "realized_pnl": -200.00,
        }))
        (data / "markets" / "a.json").write_text("{}")
        (data / "markets" / "b.json").write_text("{}")

        result = _run(["--yes", "--note", "test reset"], cwd=tmp_path)
        assert result.returncode == 0, result.stderr

        # state.json gone
        assert not (data / "state.json").exists()
        # markets dir empty
        assert list((data / "markets").glob("*.json")) == []
        # ledger has one reset row
        rows = [json.loads(l) for l in (data / "closures.jsonl").read_text().strip().split("\n")]
        assert len(rows) == 1
        assert rows[0]["type"] == "reset"
        assert rows[0]["starting_balance"] == 1000.0  # from state.json
        assert rows[0]["note"] == "test reset"

    def test_starting_balance_override(self, tmp_path):
        data = tmp_path / "data"
        (data / "markets").mkdir(parents=True)
        # No state.json, no config.json — must use --starting-balance.
        result = _run(["--yes", "--starting-balance", "500.0"], cwd=tmp_path)
        assert result.returncode == 0, result.stderr

        rows = [json.loads(l) for l in (data / "closures.jsonl").read_text().strip().split("\n")]
        assert rows[0]["starting_balance"] == 500.0

    def test_falls_back_to_config_json_when_no_state(self, tmp_path):
        data = tmp_path / "data"
        (data / "markets").mkdir(parents=True)
        (tmp_path / "config.json").write_text(json.dumps({"balance": 250.0}))
        result = _run(["--yes"], cwd=tmp_path)
        assert result.returncode == 0, result.stderr

        rows = [json.loads(l) for l in (data / "closures.jsonl").read_text().strip().split("\n")]
        assert rows[0]["starting_balance"] == 250.0
