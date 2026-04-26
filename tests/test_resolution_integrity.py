"""Tests for resolution integrity — no double-counting PnL across close paths.

A position can be closed by:
  1. Stop-loss (in monitor_positions or scan_and_update stop-loss branch)
  2. Forecast-changed close (_try_close_forecast_changed)
  3. Auto-resolution (Polymarket market resolved)

Invariant: once a position is closed (status != "open"), no other path
should re-close it or credit PnL a second time.

This is the real-world scenario: a stop-loss fires at 10am, then the
resolution pass runs at noon and sees the Polymarket market is closed.
If the guard `pos.get("status") != "open"` is missing, PnL gets credited twice.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

import pytest

from bot_v2 import scan_and_update, apply_closure_to_state


def _make_state(balance=1000.0, starting=1000.0, trades=0, wins=0, losses=0, peak=None):
    return {
        "balance": balance,
        "starting_balance": starting,
        "total_trades": trades,
        "wins": wins,
        "losses": losses,
        "peak_balance": peak if peak is not None else balance,
    }


def _make_position(entry_price=0.30, shares=33.33, cost=10.0, stop_price=None, status="open"):
    return {
        "market_id": "mkt_1",
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
        "forecast_src": "ecmwf",
    }


def _make_market_file(tmp_path, city="dallas", date="2026-05-01", **extra):
    mkt = {
        "city": city,
        "city_name": "Dallas",
        "date": date,
        "unit": "F",
        "station": "KDAL",
        "status": "open",
        "position": None,
        "event_end_date": "",
        "forecast_snapshots": [],
        "market_snapshots": [],
        "all_outcomes": [],
        "actual_temp": None,
        "resolved_outcome": None,
        "pnl": None,
        "hours_at_discovery": 24.0,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    mkt.update(extra)
    (tmp_path / "markets" / f"{city}_{date}.json").write_text(json.dumps(mkt))
    return mkt


def _mock_open_meteo_ecmwf():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    dates = [(datetime.now(timezone.utc) + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(4)]
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "daily": {
            "time": dates,
            "temperature_2m_max": [72, 74, 76, 78],
        }
    }
    return mock_resp


def _mock_gamma_market(market_id, best_ask=None, best_bid=None, closed=False, outcome_prices=None):
    mock_resp = MagicMock()
    data = {"id": market_id, "closed": closed}
    if best_ask is not None:
        data["bestAsk"] = best_ask
    if best_bid is not None:
        data["bestBid"] = best_bid
    if outcome_prices is not None:
        data["outcomePrices"] = json.dumps(outcome_prices)
    mock_resp.json.return_value = data
    return mock_resp


class TestNoDoubleCloseStopLossThenResolution:
    def test_stop_loss_close_not_re_credited_by_resolution(self, tmp_path, monkeypatch):
        """Position closed by stop-loss should NOT get PnL credited again
        when the resolution pass sees the Polymarket market is closed.
        """
        monkeypatch.setattr("bot_v2.MARKETS_DIR", tmp_path / "markets")
        monkeypatch.setattr("bot_v2.STATE_FILE", tmp_path / "state.json")
        (tmp_path / "markets").mkdir()

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        pos = _make_position(entry_price=0.30, shares=33.33, cost=10.0, stop_price=0.25)
        _make_market_file(tmp_path, city="dallas", date=today, position=pos)
        (tmp_path / "state.json").write_text(json.dumps(_make_state(balance=990.0, trades=1)))

        event = {
            "id": "evt",
            "endDate": (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat().replace("+00:00", "Z"),
            "markets": [
                {"id": "mkt_1", "question": f"Will the high be between 70-75°F on {today}?", "outcomePrices": "[0.20, 0.22]", "volume": 1000},
            ],
        }

        def mock_get(url, *args, **kwargs):
            if "gamma-api.polymarket.com/events" in url:
                if "dallas" in url:
                    mock = MagicMock()
                    mock.json.return_value = [event]
                    return mock
                empty = MagicMock()
                empty.json.return_value = []
                return empty
            if "gamma-api.polymarket.com/markets" in url:
                return _mock_gamma_market("mkt_1", best_ask=0.22, best_bid=0.20, closed=True, outcome_prices=[0.98, 0.02])
            if "open-meteo" in url or "aviationweather" in url:
                return _mock_open_meteo_ecmwf()
            return MagicMock()

        monkeypatch.setattr("bot_v2.requests.get", mock_get)
        monkeypatch.setattr("bot_v2.time.sleep", lambda *a: None)

        scan_and_update()

        saved = json.loads((tmp_path / "markets" / f"dallas_{today}.json").read_text())
        assert saved["position"]["status"] == "closed"
        assert saved["position"]["close_reason"] == "stop_loss"
        assert saved["position"]["pnl"] == pytest.approx(-3.33, abs=0.05)

        state = json.loads((tmp_path / "state.json").read_text())
        assert state["losses"] >= 1
        assert state["balance"] > 990.0


class TestNoDoubleCloseForecastChangedThenResolution:
    def test_forecast_changed_close_not_re_credited(self, tmp_path, monkeypatch):
        """Position closed by forecast_changed should NOT get PnL credited
        again when resolution pass runs.
        """
        monkeypatch.setattr("bot_v2.MARKETS_DIR", tmp_path / "markets")
        monkeypatch.setattr("bot_v2.STATE_FILE", tmp_path / "state.json")
        (tmp_path / "markets").mkdir()

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        pos = _make_position(entry_price=0.50, shares=20.0, cost=10.0, stop_price=0.01)
        _make_market_file(tmp_path, city="dallas", date=today, position=pos)
        (tmp_path / "state.json").write_text(json.dumps(_make_state(balance=990.0, trades=1, losses=0)))

        event = {
            "id": "evt",
            "endDate": (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat().replace("+00:00", "Z"),
            "markets": [
                {"id": "mkt_1", "question": f"Will the high be between 70-75°F on {today}?", "outcomePrices": "[0.10, 0.12]", "volume": 1000},
            ],
        }

        def mock_get(url, *args, **kwargs):
            if "gamma-api.polymarket.com/events" in url:
                mock = MagicMock()
                mock.json.return_value = [event]
                return mock
            if "gamma-api.polymarket.com/markets" in url:
                return _mock_gamma_market("mkt_1", best_ask=0.12, best_bid=0.10, closed=True, outcome_prices=[0.98, 0.02])
            if "open-meteo" in url or "aviationweather" in url:
                m = _mock_open_meteo_ecmwf()
                m.json.return_value["daily"]["temperature_2m_max"] = [95, 96, 97, 98]
                return m
            return MagicMock()

        monkeypatch.setattr("bot_v2.requests.get", mock_get)
        monkeypatch.setattr("bot_v2.time.sleep", lambda *a: None)

        scan_and_update()

        saved = json.loads((tmp_path / "markets" / f"dallas_{today}.json").read_text())
        assert saved["position"]["status"] == "closed"
        assert saved["position"]["close_reason"] == "forecast_changed"

        state = json.loads((tmp_path / "state.json").read_text())
        assert state["losses"] == 1


class TestApplyClosureIdempotency:
    def test_applying_closure_twice_double_counts(self):
        """This documents the risk: if apply_closure_to_state is called
        twice for the same PnL, counters get inflated.
        """
        state = {"wins": 0, "losses": 0}
        apply_closure_to_state(state, pnl=5.0)
        apply_closure_to_state(state, pnl=5.0)
        assert state["wins"] == 2

    def test_mixed_pnl_accumulates_correctly(self):
        state = {"wins": 0, "losses": 0}
        for pnl in [10.0, -5.0, 3.0, -2.0, 0.0]:
            apply_closure_to_state(state, pnl=pnl)
        assert state["wins"] == 2
        assert state["losses"] == 3
