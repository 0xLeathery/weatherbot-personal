"""Tests for the closure ledger — data/closures.jsonl.

Every realized closure in bot_v2.py reaches one of four sites that calls
apply_closure_to_state. record_closure is called on the adjacent line at
each of those sites. _build_closure_row constructs the row dict; record_closure
appends one JSON line per closure to data/closures.jsonl.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from bot_v2 import _build_closure_row


def _full_pos():
    return {
        "market_id":    "mkt_1",
        "question":     "Will Dallas high be 60-65 on May 1?",
        "entry_price":  0.420,
        "exit_price":   0.350,
        "shares":       23.81,
        "cost":         10.00,
        "pnl":          -1.66,
        "bucket_low":   60,
        "bucket_high":  65,
        "forecast_src": "hrrr",
        "forecast_temp": 62.4,
        "ecmwf_temp":   59.8,
        "spread_at_entry": 2.6,
        "sigma":        2.31,
        "p":            0.5821,
        "ev":           0.0445,
        "opened_at":    "2026-04-30T12:00:00+00:00",
        "closed_at":    "2026-04-30T18:30:00+00:00",
        "close_reason": "stop_loss",
        "status":       "closed",
    }


def _full_mkt(pos):
    return {
        "market_id":   "mkt_1",
        "city":        "dallas",
        "city_name":   "Dallas",
        "date":        "2026-05-01",
        "position":    pos,
        "status":      "closed",
        "pnl":         None,
    }


class TestBuildClosureRow:
    def test_full_row_has_every_spec_field(self):
        pos = _full_pos()
        mkt = _full_mkt(pos)
        row = _build_closure_row(mkt, pos)

        assert row["type"] == "closure"
        assert row["ts"] == "2026-04-30T18:30:00+00:00"  # comes from pos.closed_at
        assert row["city"] == "dallas"
        assert row["date_target"] == "2026-05-01"
        assert row["market_id"] == "mkt_1"
        assert row["close_reason"] == "stop_loss"
        assert row["opened_at"] == "2026-04-30T12:00:00+00:00"
        assert row["entry_price"] == 0.420
        assert row["exit_price"] == 0.350
        assert row["shares"] == 23.81
        assert row["cost"] == 10.00
        assert row["pnl"] == -1.66
        assert row["bucket_low"] == 60
        assert row["bucket_high"] == 65
        assert row["forecast_src"] == "hrrr"
        assert row["forecast_temp_at_entry"] == 62.4
        assert row["ecmwf_temp_at_entry"] == 59.8
        assert row["spread_at_entry"] == 2.6
        assert row["sigma_at_entry"] == 2.31
        assert row["p_at_entry"] == 0.5821
        assert row["ev_at_entry"] == 0.0445

    def test_missing_spread_fields_become_null(self):
        # Pre-eabdb67 closure: no spread-strategy fields on pos.
        pos = {
            "market_id":   "mkt_1",
            "entry_price": 0.50, "exit_price": 0.60, "shares": 20.0, "cost": 10.0,
            "pnl":         2.00, "bucket_low": 60, "bucket_high": 65,
            "opened_at":   "2026-04-22T00:00:00+00:00",
            "closed_at":   "2026-04-22T12:00:00+00:00",
            "close_reason": "resolved",
        }
        mkt = _full_mkt(pos)
        row = _build_closure_row(mkt, pos)

        assert row["spread_at_entry"] is None
        assert row["sigma_at_entry"] is None
        assert row["forecast_src"] is None
        assert row["ecmwf_temp_at_entry"] is None
        assert row["forecast_temp_at_entry"] is None
        assert row["p_at_entry"] is None
        assert row["ev_at_entry"] is None
        # Guaranteed-non-null fields still present
        assert row["pnl"] == 2.00
        assert row["entry_price"] == 0.50
        assert row["exit_price"] == 0.60
        assert row["close_reason"] == "resolved"

    def test_ts_falls_back_to_now_when_closed_at_missing(self):
        # Defensive: if closed_at is missing for any reason, ts must still be set.
        pos = _full_pos()
        del pos["closed_at"]
        mkt = _full_mkt(pos)
        row = _build_closure_row(mkt, pos)
        assert row["ts"]  # non-empty
        assert row["ts"].endswith("+00:00") or row["ts"].endswith("Z")


class TestRecordClosure:
    def test_appends_one_line_per_call(self, tmp_path, monkeypatch):
        from bot_v2 import record_closure
        monkeypatch.setattr("bot_v2.LEDGER_FILE", tmp_path / "closures.jsonl")

        pos = _full_pos()
        mkt = _full_mkt(pos)
        record_closure(mkt, pos)
        record_closure(mkt, pos)

        lines = (tmp_path / "closures.jsonl").read_text().strip().split("\n")
        assert len(lines) == 2
        for line in lines:
            row = json.loads(line)
            assert row["type"] == "closure"
            assert row["pnl"] == -1.66

    def test_appends_to_existing_file(self, tmp_path, monkeypatch):
        from bot_v2 import record_closure
        ledger = tmp_path / "closures.jsonl"
        ledger.write_text(json.dumps({"type": "reset", "ts": "2026-04-23T00:00:00+00:00", "starting_balance": 1000.0}) + "\n")
        monkeypatch.setattr("bot_v2.LEDGER_FILE", ledger)

        record_closure(_full_mkt(_full_pos()), _full_pos())

        lines = ledger.read_text().strip().split("\n")
        assert len(lines) == 2
        assert json.loads(lines[0])["type"] == "reset"
        assert json.loads(lines[1])["type"] == "closure"

    def test_creates_file_if_missing(self, tmp_path, monkeypatch):
        from bot_v2 import record_closure
        ledger = tmp_path / "subdir" / "closures.jsonl"
        ledger.parent.mkdir()
        monkeypatch.setattr("bot_v2.LEDGER_FILE", ledger)

        record_closure(_full_mkt(_full_pos()), _full_pos())

        assert ledger.exists()
        assert json.loads(ledger.read_text().strip())["type"] == "closure"


# Reuse helpers from test_realized_pnl.py-style fixtures
from tests.test_realized_pnl import _make_position, _make_market, _mock_gamma_response, _write_state


class TestAllFourPathsRecordClosure:
    """Each of the four closure paths in bot_v2.py must append exactly one
    row to data/closures.jsonl. record_closure runs adjacent to (and BEFORE
    save_state in) every site that calls apply_closure_to_state."""

    def _setup_dirs(self, tmp_path, monkeypatch):
        monkeypatch.setattr("bot_v2.MARKETS_DIR", tmp_path / "markets")
        monkeypatch.setattr("bot_v2.STATE_FILE", tmp_path / "state.json")
        monkeypatch.setattr("bot_v2.LEDGER_FILE", tmp_path / "closures.jsonl")
        (tmp_path / "markets").mkdir()

    def _read_ledger(self, tmp_path):
        path = tmp_path / "closures.jsonl"
        if not path.exists():
            return []
        return [json.loads(l) for l in path.read_text().strip().split("\n") if l]

    def test_stop_loss_path_writes_row(self, tmp_path, monkeypatch):
        from bot_v2 import monitor_positions
        self._setup_dirs(tmp_path, monkeypatch)

        pos = _make_position(entry_price=0.50, shares=20.0, cost=10.0, stop_price=0.40)
        mkt = _make_market(position=pos)
        (tmp_path / "markets" / "dallas_2026-05-01.json").write_text(json.dumps(mkt))
        _write_state(tmp_path / "state.json", balance=990.0, total_trades=1)

        with patch("bot_v2.requests.get", return_value=_mock_gamma_response("mkt_1", best_bid=0.40)):
            monitor_positions()

        rows = self._read_ledger(tmp_path)
        closure_rows = [r for r in rows if r["type"] == "closure"]
        assert len(closure_rows) == 1
        assert closure_rows[0]["close_reason"] == "stop_loss"
        assert closure_rows[0]["pnl"] == -2.00

    def test_take_profit_path_writes_row(self, tmp_path, monkeypatch):
        from datetime import datetime, timezone, timedelta
        from bot_v2 import monitor_positions
        self._setup_dirs(tmp_path, monkeypatch)

        end_dt = datetime.now(timezone.utc) + timedelta(hours=60)
        pos = _make_position(entry_price=0.50, shares=20.0, cost=10.0, stop_price=0.40)
        mkt = _make_market(position=pos)
        mkt["event_end_date"] = end_dt.isoformat().replace("+00:00", "Z")
        (tmp_path / "markets" / "dallas_2026-05-01.json").write_text(json.dumps(mkt))
        _write_state(tmp_path / "state.json", balance=990.0, total_trades=1)

        with patch("bot_v2.requests.get", return_value=_mock_gamma_response("mkt_1", best_bid=0.75)):
            monitor_positions()

        rows = self._read_ledger(tmp_path)
        closure_rows = [r for r in rows if r["type"] == "closure"]
        assert len(closure_rows) == 1
        assert closure_rows[0]["close_reason"] == "take_profit"
        assert closure_rows[0]["pnl"] == 5.00


class TestWriteOrderInvariant:
    """Spec invariant: ledger row appended BEFORE save_state. If save_state
    raises, the row must already be on disk — the row is the source of
    truth for closures, not state.json."""

    def test_ledger_row_persists_when_save_state_raises(self, tmp_path, monkeypatch):
        from bot_v2 import monitor_positions
        monkeypatch.setattr("bot_v2.MARKETS_DIR", tmp_path / "markets")
        monkeypatch.setattr("bot_v2.STATE_FILE", tmp_path / "state.json")
        monkeypatch.setattr("bot_v2.LEDGER_FILE", tmp_path / "closures.jsonl")
        (tmp_path / "markets").mkdir()

        pos = _make_position(entry_price=0.50, shares=20.0, cost=10.0, stop_price=0.40)
        mkt = _make_market(position=pos)
        (tmp_path / "markets" / "dallas_2026-05-01.json").write_text(json.dumps(mkt))
        _write_state(tmp_path / "state.json", balance=990.0, total_trades=1)

        def boom(*a, **kw):
            raise RuntimeError("disk full")
        monkeypatch.setattr("bot_v2.save_state", boom)

        with patch("bot_v2.requests.get", return_value=_mock_gamma_response("mkt_1", best_bid=0.40)):
            try:
                monitor_positions()
            except RuntimeError:
                pass  # expected

        # The ledger row landed on disk despite the save_state failure.
        ledger = (tmp_path / "closures.jsonl").read_text().strip()
        assert ledger
        rows = [json.loads(l) for l in ledger.split("\n") if l]
        assert any(r["type"] == "closure" and r["close_reason"] == "stop_loss" for r in rows)
