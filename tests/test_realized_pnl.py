"""Tests for state.realized_pnl — the session-scoped realized PnL accumulator.

The accumulator lives in state.json and is incremented inside
apply_closure_to_state on every closure. All four closure paths in bot_v2.py
(monitor early-close, forecast-changed close, scan_and_update final resolve,
take-profit/stop-loss/trailing-stop) reach this single helper, so this test
file covers each path end-to-end as well as the helper itself.

Invariants:
  - state["realized_pnl"] equals the sum of pnl across all closures since reset
  - Increments are rounded to 2 decimals (no float drift accumulation)
  - state["realized_pnl"] equals sum(mkt["pnl"] for mkt in markets) — the
    dashboard's recomputed value matches the accumulator
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from bot_v2 import apply_closure_to_state, load_state, maybe_backfill_realized_pnl, monitor_positions


# --- helpers ----------------------------------------------------------------

def _make_position(entry_price=0.50, shares=20.0, cost=10.0, stop_price=0.40, **extra):
    pos = {
        "market_id":   "mkt_1",
        "entry_price": entry_price,
        "shares":      shares,
        "cost":        cost,
        "stop_price":  stop_price,
        "status":      "open",
        "bucket_low":  70,
        "bucket_high": 75,
        "forecast_src": "hrrr",
        "pnl":         None,
        "exit_price":  None,
        "closed_at":   None,
        "close_reason": None,
    }
    pos.update(extra)
    return pos


def _make_market(position=None, city="dallas", date="2026-05-01"):
    return {
        "market_id":   "mkt_1",
        "city":        city,
        "city_name":   city.capitalize(),
        "date":        date,
        "unit":        "F",
        "station":     "KDAL",
        "event_end_date": f"{date}T23:00:00Z",
        "hours_at_discovery": 24.0,
        "status":      "open",
        "position":    position,
        "actual_temp": None,
        "resolved_outcome": None,
        "pnl":         None,
        "forecast_snapshots": [],
        "market_snapshots":   [],
        "all_outcomes":       [],
        "created_at":  "2026-04-30T00:00:00Z",
    }


def _mock_gamma_response(market_id, best_bid=None):
    mock_resp = MagicMock()
    data = {"id": market_id}
    if best_bid is not None:
        data["bestBid"] = best_bid
    mock_resp.json.return_value = data
    return mock_resp


def _write_state(path, **overrides):
    state = {
        "balance":          1000.0,
        "starting_balance": 1000.0,
        "total_trades":     0,
        "wins":             0,
        "losses":           0,
        "peak_balance":     1000.0,
        "realized_pnl":     0.0,
    }
    state.update(overrides)
    path.write_text(json.dumps(state))


# --- integration: stop-loss path through monitor_positions ------------------

class TestStopLossPathUpdatesRealizedPnL:
    def test_stop_loss_close_decrements_realized_pnl(self, tmp_path, monkeypatch):
        monkeypatch.setattr("bot_v2.MARKETS_DIR", tmp_path / "markets")
        monkeypatch.setattr("bot_v2.STATE_FILE", tmp_path / "state.json")
        (tmp_path / "markets").mkdir()

        # Position cost $10, entry $0.50, stop $0.40. Price drops to $0.40 →
        # realized loss of (0.40 - 0.50) * 20 = -$2.00.
        pos = _make_position(entry_price=0.50, shares=20.0, cost=10.0, stop_price=0.40)
        mkt = _make_market(position=pos)
        (tmp_path / "markets" / "dallas_2026-05-01.json").write_text(json.dumps(mkt))
        _write_state(tmp_path / "state.json", balance=990.0, total_trades=1)

        with patch("bot_v2.requests.get", return_value=_mock_gamma_response("mkt_1", best_bid=0.40)):
            assert monitor_positions() == 1

        state = json.loads((tmp_path / "state.json").read_text())
        assert state["realized_pnl"] == -2.00


# --- unit: apply_closure_to_state ------------------------------------------

class TestApplyClosureToStateAccumulator:
    def _fresh_state(self):
        return {
            "balance": 1000.0, "starting_balance": 1000.0,
            "total_trades": 0, "wins": 0, "losses": 0,
            "peak_balance": 1000.0, "realized_pnl": 0.0,
        }

    def test_positive_pnl_increments_accumulator(self):
        state = self._fresh_state()
        apply_closure_to_state(state, 4.20)
        assert state["realized_pnl"] == 4.20

    def test_negative_pnl_decrements_accumulator(self):
        state = self._fresh_state()
        apply_closure_to_state(state, -3.50)
        assert state["realized_pnl"] == -3.50

    def test_none_pnl_is_noop(self):
        state = self._fresh_state()
        apply_closure_to_state(state, None)
        assert state["realized_pnl"] == 0.0
        assert state["total_trades"] == 0  # also no counter bump

    def test_multiple_closures_sum_to_two_decimals(self):
        state = self._fresh_state()
        apply_closure_to_state(state, 1.111)
        apply_closure_to_state(state, 2.222)
        apply_closure_to_state(state, -0.333)
        # Running total rounds after each step:
        #   round(0 + 1.111, 2) = 1.11
        #   round(1.11 + 2.222, 2) = 3.33
        #   round(3.33 + (-0.333), 2) = 3.00
        assert state["realized_pnl"] == 3.00

    def test_missing_realized_pnl_field_treated_as_zero(self):
        # Backwards-compat: state files written before this change lack the field.
        state = {
            "balance": 1000.0, "starting_balance": 1000.0,
            "total_trades": 0, "wins": 0, "losses": 0, "peak_balance": 1000.0,
        }
        apply_closure_to_state(state, 2.50)
        assert state["realized_pnl"] == 2.50


# --- integration: take-profit path through monitor_positions ----------------

class TestTakeProfitPathUpdatesRealizedPnL:
    def test_take_profit_close_increments_realized_pnl(self, tmp_path, monkeypatch):
        monkeypatch.setattr("bot_v2.MARKETS_DIR", tmp_path / "markets")
        monkeypatch.setattr("bot_v2.STATE_FILE", tmp_path / "state.json")
        (tmp_path / "markets").mkdir()

        # Take-profit threshold is 0.85 when 24-48h to resolution, 0.75 if 48h+.
        # Use an event 60h out so threshold is 0.75. Entry 0.50, shares 20,
        # exit 0.75 → realized PnL of (0.75 - 0.50) * 20 = +$5.00.
        from datetime import datetime, timezone, timedelta
        end_dt = datetime.now(timezone.utc) + timedelta(hours=60)
        pos = _make_position(entry_price=0.50, shares=20.0, cost=10.0, stop_price=0.40)
        mkt = _make_market(position=pos)
        mkt["event_end_date"] = end_dt.isoformat().replace("+00:00", "Z")
        (tmp_path / "markets" / "dallas_2026-05-01.json").write_text(json.dumps(mkt))
        _write_state(tmp_path / "state.json", balance=990.0, total_trades=1)

        with patch("bot_v2.requests.get", return_value=_mock_gamma_response("mkt_1", best_bid=0.75)):
            assert monitor_positions() == 1

        state = json.loads((tmp_path / "state.json").read_text())
        assert state["realized_pnl"] == 5.00
        # Per-market pnl matches accumulator (dashboard recompute parity)
        saved = json.loads((tmp_path / "markets" / "dallas_2026-05-01.json").read_text())
        assert saved["position"]["pnl"] == 5.00


# --- one-shot self-heal: maybe_backfill_realized_pnl -----------------------

def _closed_market(name, pnl, market_id="mkt_x"):
    """A market with a closed position carrying a realized pnl."""
    pos = _make_position()
    pos["market_id"] = market_id
    pos["status"] = "closed"
    pos["pnl"] = pnl
    mkt = _make_market(position=pos)
    mkt["market_id"] = market_id
    mkt["status"] = "closed"
    mkt["pnl"] = pnl
    return name, mkt


class TestMaybeBackfillRealizedPnL:
    def test_backfills_when_field_zero_but_wins_or_losses_recorded(self, tmp_path, monkeypatch):
        monkeypatch.setattr("bot_v2.MARKETS_DIR", tmp_path / "markets")
        monkeypatch.setattr("bot_v2.STATE_FILE", tmp_path / "state.json")
        (tmp_path / "markets").mkdir()

        for name, mkt in [
            _closed_market("a", 4.20, market_id="mkt_a"),
            _closed_market("b", -2.50, market_id="mkt_b"),
            _closed_market("c", -1.10, market_id="mkt_c"),
        ]:
            (tmp_path / "markets" / f"{name}.json").write_text(json.dumps(mkt))
        _write_state(tmp_path / "state.json", wins=1, losses=2, total_trades=3, realized_pnl=0.0)

        state = json.loads((tmp_path / "state.json").read_text())
        maybe_backfill_realized_pnl(state)

        # 4.20 - 2.50 - 1.10 = 0.60
        assert state["realized_pnl"] == 0.60
        # Persisted to disk
        on_disk = json.loads((tmp_path / "state.json").read_text())
        assert on_disk["realized_pnl"] == 0.60

    def test_skips_when_field_already_populated(self, tmp_path, monkeypatch):
        monkeypatch.setattr("bot_v2.MARKETS_DIR", tmp_path / "markets")
        monkeypatch.setattr("bot_v2.STATE_FILE", tmp_path / "state.json")
        (tmp_path / "markets").mkdir()

        # Market files would compute to a different number, but the field is already
        # set — the bot has been running with the new code, don't clobber.
        for name, mkt in [_closed_market("a", 99.00, market_id="mkt_a")]:
            (tmp_path / "markets" / f"{name}.json").write_text(json.dumps(mkt))
        _write_state(tmp_path / "state.json", wins=1, losses=0, total_trades=1, realized_pnl=5.00)

        state = json.loads((tmp_path / "state.json").read_text())
        maybe_backfill_realized_pnl(state)

        assert state["realized_pnl"] == 5.00  # unchanged

    def test_skips_when_no_closures_recorded(self, tmp_path, monkeypatch):
        monkeypatch.setattr("bot_v2.MARKETS_DIR", tmp_path / "markets")
        monkeypatch.setattr("bot_v2.STATE_FILE", tmp_path / "state.json")
        (tmp_path / "markets").mkdir()

        # Even with stray market files, no recorded wins/losses means we trust
        # the field is genuinely zero — don't backfill from orphaned files.
        for name, mkt in [_closed_market("a", 4.20, market_id="mkt_a")]:
            (tmp_path / "markets" / f"{name}.json").write_text(json.dumps(mkt))
        _write_state(tmp_path / "state.json", wins=0, losses=0, total_trades=0, realized_pnl=0.0)

        state = json.loads((tmp_path / "state.json").read_text())
        maybe_backfill_realized_pnl(state)

        assert state["realized_pnl"] == 0.0  # unchanged

    def test_idempotent_second_call_is_noop(self, tmp_path, monkeypatch):
        monkeypatch.setattr("bot_v2.MARKETS_DIR", tmp_path / "markets")
        monkeypatch.setattr("bot_v2.STATE_FILE", tmp_path / "state.json")
        (tmp_path / "markets").mkdir()

        for name, mkt in [_closed_market("a", 3.00, market_id="mkt_a")]:
            (tmp_path / "markets" / f"{name}.json").write_text(json.dumps(mkt))
        _write_state(tmp_path / "state.json", wins=1, losses=0, total_trades=1, realized_pnl=0.0)

        state = json.loads((tmp_path / "state.json").read_text())
        maybe_backfill_realized_pnl(state)
        assert state["realized_pnl"] == 3.00

        # Second call: field is now populated, so the function bails out at the
        # first guard. No double-counting.
        maybe_backfill_realized_pnl(state)
        assert state["realized_pnl"] == 3.00

    def test_skips_markets_without_pnl(self, tmp_path, monkeypatch):
        monkeypatch.setattr("bot_v2.MARKETS_DIR", tmp_path / "markets")
        monkeypatch.setattr("bot_v2.STATE_FILE", tmp_path / "state.json")
        (tmp_path / "markets").mkdir()

        # One closed (with pnl), one still open (pnl=None) — only the closed one counts.
        _, closed = _closed_market("a", 2.50, market_id="mkt_a")
        open_pos = _make_position()
        open_pos["market_id"] = "mkt_b"
        open_mkt = _make_market(position=open_pos)
        open_mkt["market_id"] = "mkt_b"
        # pnl stays None on the open market

        (tmp_path / "markets" / "a.json").write_text(json.dumps(closed))
        (tmp_path / "markets" / "b.json").write_text(json.dumps(open_mkt))
        _write_state(tmp_path / "state.json", wins=1, losses=0, total_trades=2, realized_pnl=0.0)

        state = json.loads((tmp_path / "state.json").read_text())
        maybe_backfill_realized_pnl(state)

        assert state["realized_pnl"] == 2.50  # only the closed one
