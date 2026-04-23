"""Risk management invariant tests for monitor_positions().

Tests that the monitoring loop enforces stop-loss, trailing stop, and
take-profit rules correctly — and that balance accounting stays sound
when positions are closed.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

import pytest

from bot_v2 import monitor_positions, load_state, save_state


def _make_market(city="dallas", date="2026-05-01", position=None, event_end_date=None):
    mkt = {
        "city": city,
        "city_name": "Dallas",
        "date": date,
        "unit": "F",
        "station": "KDAL",
        "status": "open",
        "position": position,
        "event_end_date": event_end_date or "",
        "forecast_snapshots": [],
        "market_snapshots": [],
        "all_outcomes": [],
        "actual_temp": None,
        "resolved_outcome": None,
        "pnl": None,
    }
    return mkt


def _make_position(market_id="mkt_1", entry_price=0.50, shares=20.0, cost=10.0,
                   stop_price=None, status="open", **extra):
    pos = {
        "market_id": market_id,
        "entry_price": entry_price,
        "shares": shares,
        "cost": cost,
        "stop_price": stop_price if stop_price is not None else entry_price * 0.80,
        "status": status,
        "bucket_low": 70,
        "bucket_high": 75,
        "pnl": None,
        "exit_price": None,
        "close_reason": None,
        "closed_at": None,
        "trailing_activated": False,
    }
    pos.update(extra)
    return pos


def _mock_gamma_response(market_id, best_bid=None):
    mock_resp = MagicMock()
    data = {"id": market_id}
    if best_bid is not None:
        data["bestBid"] = best_bid
    mock_resp.json.return_value = data
    return mock_resp


class TestNoOpenPositions:
    def test_returns_zero_when_no_markets(self, tmp_path, monkeypatch):
        monkeypatch.setattr("bot_v2.MARKETS_DIR", tmp_path / "markets")
        monkeypatch.setattr("bot_v2.STATE_FILE", tmp_path / "state.json")
        (tmp_path / "markets").mkdir()
        (tmp_path / "state.json").write_text(json.dumps({
            "balance": 1000.0, "starting_balance": 1000.0,
            "total_trades": 0, "wins": 0, "losses": 0, "peak_balance": 1000.0,
        }))
        assert monitor_positions() == 0


class TestStopLoss:
    def test_closes_when_price_hits_stop(self, tmp_path, monkeypatch):
        monkeypatch.setattr("bot_v2.MARKETS_DIR", tmp_path / "markets")
        monkeypatch.setattr("bot_v2.STATE_FILE", tmp_path / "state.json")
        (tmp_path / "markets").mkdir()

        pos = _make_position(entry_price=0.50, shares=20.0, cost=10.0, stop_price=0.40)
        mkt = _make_market(position=pos)
        (tmp_path / "markets" / "dallas_2026-05-01.json").write_text(json.dumps(mkt))
        (tmp_path / "state.json").write_text(json.dumps({
            "balance": 990.0, "starting_balance": 1000.0,
            "total_trades": 1, "wins": 0, "losses": 0, "peak_balance": 1000.0,
        }))

        with patch("bot_v2.requests.get", return_value=_mock_gamma_response("mkt_1", best_bid=0.40)):
            closed = monitor_positions()

        assert closed == 1
        saved = json.loads((tmp_path / "markets" / "dallas_2026-05-01.json").read_text())
        assert saved["position"]["status"] == "closed"
        assert saved["position"]["close_reason"] == "stop_loss"

    def test_does_not_close_when_price_above_stop(self, tmp_path, monkeypatch):
        monkeypatch.setattr("bot_v2.MARKETS_DIR", tmp_path / "markets")
        monkeypatch.setattr("bot_v2.STATE_FILE", tmp_path / "state.json")
        (tmp_path / "markets").mkdir()

        pos = _make_position(entry_price=0.50, shares=20.0, cost=10.0, stop_price=0.40)
        mkt = _make_market(position=pos)
        (tmp_path / "markets" / "dallas_2026-05-01.json").write_text(json.dumps(mkt))
        (tmp_path / "state.json").write_text(json.dumps({
            "balance": 990.0, "starting_balance": 1000.0,
            "total_trades": 1, "wins": 0, "losses": 0, "peak_balance": 1000.0,
        }))

        with patch("bot_v2.requests.get", return_value=_mock_gamma_response("mkt_1", best_bid=0.45)):
            closed = monitor_positions()

        assert closed == 0
        saved = json.loads((tmp_path / "markets" / "dallas_2026-05-01.json").read_text())
        assert saved["position"]["status"] == "open"


class TestTrailingStop:
    def test_activates_when_up_20_percent(self, tmp_path, monkeypatch):
        monkeypatch.setattr("bot_v2.MARKETS_DIR", tmp_path / "markets")
        monkeypatch.setattr("bot_v2.STATE_FILE", tmp_path / "state.json")
        (tmp_path / "markets").mkdir()

        pos = _make_position(entry_price=0.50, shares=20.0, cost=10.0, stop_price=0.40)
        mkt = _make_market(position=pos)
        (tmp_path / "markets" / "dallas_2026-05-01.json").write_text(json.dumps(mkt))
        (tmp_path / "state.json").write_text(json.dumps({
            "balance": 990.0, "starting_balance": 1000.0,
            "total_trades": 1, "wins": 0, "losses": 0, "peak_balance": 1000.0,
        }))

        with patch("bot_v2.requests.get", return_value=_mock_gamma_response("mkt_1", best_bid=0.60)):
            monitor_positions()

        saved = json.loads((tmp_path / "markets" / "dallas_2026-05-01.json").read_text())
        assert saved["position"]["stop_price"] == 0.50
        assert saved["position"]["trailing_activated"] is True

    def test_does_not_activate_below_20_percent(self, tmp_path, monkeypatch):
        monkeypatch.setattr("bot_v2.MARKETS_DIR", tmp_path / "markets")
        monkeypatch.setattr("bot_v2.STATE_FILE", tmp_path / "state.json")
        (tmp_path / "markets").mkdir()

        pos = _make_position(entry_price=0.50, shares=20.0, cost=10.0, stop_price=0.40)
        mkt = _make_market(position=pos)
        (tmp_path / "markets" / "dallas_2026-05-01.json").write_text(json.dumps(mkt))
        (tmp_path / "state.json").write_text(json.dumps({
            "balance": 990.0, "starting_balance": 1000.0,
            "total_trades": 1, "wins": 0, "losses": 0, "peak_balance": 1000.0,
        }))

        with patch("bot_v2.requests.get", return_value=_mock_gamma_response("mkt_1", best_bid=0.59)):
            monitor_positions()

        saved = json.loads((tmp_path / "markets" / "dallas_2026-05-01.json").read_text())
        assert saved["position"]["stop_price"] == 0.40
        assert saved["position"].get("trailing_activated") is not True

    def test_trailing_stop_closes_at_breakeven(self, tmp_path, monkeypatch):
        monkeypatch.setattr("bot_v2.MARKETS_DIR", tmp_path / "markets")
        monkeypatch.setattr("bot_v2.STATE_FILE", tmp_path / "state.json")
        (tmp_path / "markets").mkdir()

        pos = _make_position(entry_price=0.50, shares=20.0, cost=10.0, stop_price=0.50, trailing_activated=True)
        mkt = _make_market(position=pos)
        (tmp_path / "markets" / "dallas_2026-05-01.json").write_text(json.dumps(mkt))
        (tmp_path / "state.json").write_text(json.dumps({
            "balance": 990.0, "starting_balance": 1000.0,
            "total_trades": 1, "wins": 0, "losses": 0, "peak_balance": 1000.0,
        }))

        with patch("bot_v2.requests.get", return_value=_mock_gamma_response("mkt_1", best_bid=0.50)):
            closed = monitor_positions()

        assert closed == 1
        saved = json.loads((tmp_path / "markets" / "dallas_2026-05-01.json").read_text())
        assert saved["position"]["status"] == "closed"
        assert saved["position"]["close_reason"] == "trailing_stop"


class TestTakeProfit:
    def test_24_to_48h_threshold_085(self, tmp_path, monkeypatch):
        monkeypatch.setattr("bot_v2.MARKETS_DIR", tmp_path / "markets")
        monkeypatch.setattr("bot_v2.STATE_FILE", tmp_path / "state.json")
        (tmp_path / "markets").mkdir()

        end_date = (datetime.now(timezone.utc) + timedelta(hours=30)).isoformat()
        pos = _make_position(entry_price=0.50, shares=20.0, cost=10.0, stop_price=0.10)
        mkt = _make_market(position=pos, event_end_date=end_date)
        (tmp_path / "markets" / "dallas_2026-05-01.json").write_text(json.dumps(mkt))
        (tmp_path / "state.json").write_text(json.dumps({
            "balance": 990.0, "starting_balance": 1000.0,
            "total_trades": 1, "wins": 0, "losses": 0, "peak_balance": 1000.0,
        }))

        with patch("bot_v2.requests.get", return_value=_mock_gamma_response("mkt_1", best_bid=0.85)):
            closed = monitor_positions()

        assert closed == 1
        saved = json.loads((tmp_path / "markets" / "dallas_2026-05-01.json").read_text())
        assert saved["position"]["close_reason"] == "take_profit"

    def test_48h_plus_threshold_075(self, tmp_path, monkeypatch):
        monkeypatch.setattr("bot_v2.MARKETS_DIR", tmp_path / "markets")
        monkeypatch.setattr("bot_v2.STATE_FILE", tmp_path / "state.json")
        (tmp_path / "markets").mkdir()

        end_date = (datetime.now(timezone.utc) + timedelta(hours=60)).isoformat()
        pos = _make_position(entry_price=0.50, shares=20.0, cost=10.0, stop_price=0.10)
        mkt = _make_market(position=pos, event_end_date=end_date)
        (tmp_path / "markets" / "dallas_2026-05-01.json").write_text(json.dumps(mkt))
        (tmp_path / "state.json").write_text(json.dumps({
            "balance": 990.0, "starting_balance": 1000.0,
            "total_trades": 1, "wins": 0, "losses": 0, "peak_balance": 1000.0,
        }))

        with patch("bot_v2.requests.get", return_value=_mock_gamma_response("mkt_1", best_bid=0.75)):
            closed = monitor_positions()

        assert closed == 1
        saved = json.loads((tmp_path / "markets" / "dallas_2026-05-01.json").read_text())
        assert saved["position"]["close_reason"] == "take_profit"

    def test_under_24h_no_take_profit(self, tmp_path, monkeypatch):
        monkeypatch.setattr("bot_v2.MARKETS_DIR", tmp_path / "markets")
        monkeypatch.setattr("bot_v2.STATE_FILE", tmp_path / "state.json")
        (tmp_path / "markets").mkdir()

        end_date = (datetime.now(timezone.utc) + timedelta(hours=10)).isoformat()
        pos = _make_position(entry_price=0.50, shares=20.0, cost=10.0, stop_price=0.10)
        mkt = _make_market(position=pos, event_end_date=end_date)
        (tmp_path / "markets" / "dallas_2026-05-01.json").write_text(json.dumps(mkt))
        (tmp_path / "state.json").write_text(json.dumps({
            "balance": 990.0, "starting_balance": 1000.0,
            "total_trades": 1, "wins": 0, "losses": 0, "peak_balance": 1000.0,
        }))

        with patch("bot_v2.requests.get", return_value=_mock_gamma_response("mkt_1", best_bid=0.90)):
            closed = monitor_positions()

        assert closed == 0
        saved = json.loads((tmp_path / "markets" / "dallas_2026-05-01.json").read_text())
        assert saved["position"]["status"] == "open"


class TestBalanceAccounting:
    def test_balance_correct_after_close(self, tmp_path, monkeypatch):
        monkeypatch.setattr("bot_v2.MARKETS_DIR", tmp_path / "markets")
        monkeypatch.setattr("bot_v2.STATE_FILE", tmp_path / "state.json")
        (tmp_path / "markets").mkdir()

        pos = _make_position(entry_price=0.50, shares=20.0, cost=10.0, stop_price=0.40)
        mkt = _make_market(position=pos)
        (tmp_path / "markets" / "dallas_2026-05-01.json").write_text(json.dumps(mkt))
        (tmp_path / "state.json").write_text(json.dumps({
            "balance": 990.0, "starting_balance": 1000.0,
            "total_trades": 1, "wins": 0, "losses": 0, "peak_balance": 1000.0,
        }))

        with patch("bot_v2.requests.get", return_value=_mock_gamma_response("mkt_1", best_bid=0.40)):
            monitor_positions()

        state = json.loads((tmp_path / "state.json").read_text())
        expected_balance = 990.0 + 10.0 + (0.40 - 0.50) * 20.0
        assert state["balance"] == pytest.approx(round(expected_balance, 2))


class TestAPIFailureFallback:
    def test_uses_cached_price_when_api_fails(self, tmp_path, monkeypatch):
        monkeypatch.setattr("bot_v2.MARKETS_DIR", tmp_path / "markets")
        monkeypatch.setattr("bot_v2.STATE_FILE", tmp_path / "state.json")
        (tmp_path / "markets").mkdir()

        pos = _make_position(entry_price=0.50, shares=20.0, cost=10.0, stop_price=0.40)
        mkt = _make_market(position=pos)
        mkt["all_outcomes"] = [{"market_id": "mkt_1", "bid": 0.40, "price": 0.40}]
        (tmp_path / "markets" / "dallas_2026-05-01.json").write_text(json.dumps(mkt))
        (tmp_path / "state.json").write_text(json.dumps({
            "balance": 990.0, "starting_balance": 1000.0,
            "total_trades": 1, "wins": 0, "losses": 0, "peak_balance": 1000.0,
        }))

        with patch("bot_v2.requests.get", side_effect=Exception("timeout")):
            closed = monitor_positions()

        assert closed == 1
        saved = json.loads((tmp_path / "markets" / "dallas_2026-05-01.json").read_text())
        assert saved["position"]["status"] == "closed"


class TestNoDoubleClose:
    def test_already_closed_position_ignored(self, tmp_path, monkeypatch):
        monkeypatch.setattr("bot_v2.MARKETS_DIR", tmp_path / "markets")
        monkeypatch.setattr("bot_v2.STATE_FILE", tmp_path / "state.json")
        (tmp_path / "markets").mkdir()

        pos = _make_position(entry_price=0.50, shares=20.0, cost=10.0,
                             stop_price=0.40, status="closed", exit_price=0.40, pnl=-2.0)
        mkt = _make_market(position=pos)
        (tmp_path / "markets" / "dallas_2026-05-01.json").write_text(json.dumps(mkt))
        (tmp_path / "state.json").write_text(json.dumps({
            "balance": 998.0, "starting_balance": 1000.0,
            "total_trades": 1, "wins": 0, "losses": 1, "peak_balance": 1000.0,
        }))

        with patch("bot_v2.requests.get", return_value=_mock_gamma_response("mkt_1", best_bid=0.30)):
            closed = monitor_positions()

        assert closed == 0
        saved = json.loads((tmp_path / "markets" / "dallas_2026-05-01.json").read_text())
        assert saved["position"]["pnl"] == -2.0
