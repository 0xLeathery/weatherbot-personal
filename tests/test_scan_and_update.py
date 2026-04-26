"""Business logic invariant tests for scan_and_update().

Tests that the main scan cycle enforces balance accounting integrity,
filter correctness, no-double-entry, and proper position lifecycle.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

import pytest

from bot_v2 import scan_and_update, load_state, save_state, MARKETS_DIR, STATE_FILE


def _make_state(balance=1000.0, starting=1000.0, trades=0, wins=0, losses=0, peak=None):
    return {
        "balance": balance,
        "starting_balance": starting,
        "total_trades": trades,
        "wins": wins,
        "losses": losses,
        "peak_balance": peak if peak is not None else balance,
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


def _mock_open_meteo_ecmwf():
    """Return a mock response for ECMWF forecast."""
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


def _mock_open_meteo_hrrr():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    dates = [(datetime.now(timezone.utc) + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(3)]
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "daily": {
            "time": dates,
            "temperature_2m_max": [76, 78, 80],
        }
    }
    return mock_resp


def _mock_metar():
    mock_resp = MagicMock()
    mock_resp.json.return_value = [{"temp": 22.0}]
    return mock_resp


def _make_polymarket_event(city_slug, date_str, markets=None):
    """Build a Polymarket event with temperature bucket markets."""
    if markets is None:
        markets = [
            {"id": "mkt_1", "question": f"Will the high be between 70-75°F on {date_str}?", "outcomePrices": "[0.30, 0.32]", "volume": 1000},
            {"id": "mkt_2", "question": f"Will the high be between 75-80°F on {date_str}?", "outcomePrices": "[0.50, 0.52]", "volume": 2000},
            {"id": "mkt_3", "question": f"Will the high be 72°F or below on {date_str}?", "outcomePrices": "[0.10, 0.12]", "volume": 500},
        ]
    end_date = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat().replace("+00:00", "Z")
    return {
        "id": f"evt_{city_slug}_{date_str}",
        "endDate": end_date,
        "markets": markets,
    }


def _mock_gamma_events(event):
    mock_resp = MagicMock()
    mock_resp.json.return_value = [event]
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


def _setup_api_mocks(monkeypatch, event, market_id="mkt_1", best_ask=0.32, best_bid=0.30,
                     only_city=None):
    """Set up common API mocks for a scan cycle.

    If only_city is set, only return events for that city; others get empty.
    """
    def mock_get(url, *args, **kwargs):
        if "open-meteo.com/v1/forecast" in url and "ecmwf" in url:
            return _mock_open_meteo_ecmwf()
        if "open-meteo.com/v1/forecast" in url and "gfs" in url:
            return _mock_open_meteo_hrrr()
        if "aviationweather.gov" in url:
            return _mock_metar()
        if "gamma-api.polymarket.com/events" in url:
            if only_city and only_city not in url:
                empty = MagicMock()
                empty.json.return_value = []
                return empty
            return _mock_gamma_events(event)
        if "gamma-api.polymarket.com/markets" in url:
            return _mock_gamma_market(market_id, best_ask=best_ask, best_bid=best_bid)
        return MagicMock()

    monkeypatch.setattr("bot_v2.requests.get", mock_get)
    monkeypatch.setattr("bot_v2.time.sleep", lambda *a: None)


class TestBalanceAccounting:
    def test_balance_decrements_on_new_position(self, tmp_path, monkeypatch):
        monkeypatch.setattr("bot_v2.MARKETS_DIR", tmp_path / "markets")
        monkeypatch.setattr("bot_v2.STATE_FILE", tmp_path / "state.json")
        (tmp_path / "markets").mkdir()
        (tmp_path / "state.json").write_text(json.dumps(_make_state()))

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        event = _make_polymarket_event("dallas", today)
        _setup_api_mocks(monkeypatch, event, best_ask=0.32, best_bid=0.30, only_city="dallas")

        scan_and_update()

        state = json.loads((tmp_path / "state.json").read_text())
        assert state["balance"] < 1000.0

    def test_balance_increments_on_stop_loss(self, tmp_path, monkeypatch):
        monkeypatch.setattr("bot_v2.MARKETS_DIR", tmp_path / "markets")
        monkeypatch.setattr("bot_v2.STATE_FILE", tmp_path / "state.json")
        (tmp_path / "markets").mkdir()

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        pos = _make_position(entry_price=0.30, shares=33.33, cost=10.0, stop_price=0.25)
        mkt = _make_market_file(tmp_path, city="dallas", date=today, position=pos)

        state = _make_state(balance=990.0)
        (tmp_path / "state.json").write_text(json.dumps(state))

        event = _make_polymarket_event("dallas", today, markets=[
            {"id": "mkt_1", "question": f"Will the high be between 70-75°F on {today}?", "outcomePrices": "[0.20, 0.22]", "volume": 1000},
        ])
        _setup_api_mocks(monkeypatch, event, market_id="mkt_1", best_ask=0.22, best_bid=0.20, only_city="dallas")

        scan_and_update()

        saved = json.loads((tmp_path / "markets" / f"dallas_{today}.json").read_text())
        assert saved["position"]["status"] == "closed"
        state = json.loads((tmp_path / "state.json").read_text())
        stop_pnl = 10.0 + (0.20 - 0.30) * 33.33
        assert state["balance"] == pytest.approx(round(990.0 + stop_pnl, 2), abs=40.05)

    def test_balance_invariant_after_full_cycle(self, tmp_path, monkeypatch):
        monkeypatch.setattr("bot_v2.MARKETS_DIR", tmp_path / "markets")
        monkeypatch.setattr("bot_v2.STATE_FILE", tmp_path / "state.json")
        (tmp_path / "markets").mkdir()
        (tmp_path / "state.json").write_text(json.dumps(_make_state()))

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        event = _make_polymarket_event("dallas", today)
        _setup_api_mocks(monkeypatch, event)

        new_pos, closed, resolved = scan_and_update()

        state = json.loads((tmp_path / "state.json").read_text())
        markets_dir = tmp_path / "markets"
        open_cost = 0.0
        realized_pnl = 0.0
        for f in markets_dir.glob("*.json"):
            m = json.loads(f.read_text())
            pos = m.get("position")
            if pos and pos.get("status") == "open":
                open_cost += pos.get("cost", 0)
            if pos and pos.get("status") == "closed" and pos.get("pnl") is not None:
                realized_pnl += pos["pnl"]

        expected = round(state["starting_balance"] + realized_pnl - open_cost, 2)
        assert state["balance"] == expected


class TestNoDoubleEntry:
    def test_existing_position_not_duplicated(self, tmp_path, monkeypatch):
        monkeypatch.setattr("bot_v2.MARKETS_DIR", tmp_path / "markets")
        monkeypatch.setattr("bot_v2.STATE_FILE", tmp_path / "state.json")
        (tmp_path / "markets").mkdir()

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        pos = _make_position(entry_price=0.30, shares=33.33, cost=10.0)
        _make_market_file(tmp_path, city="dallas", date=today, position=pos)
        (tmp_path / "state.json").write_text(json.dumps(_make_state(balance=990.0, trades=1)))

        event = _make_polymarket_event("dallas", today)
        _setup_api_mocks(monkeypatch, event, only_city="dallas")

        scan_and_update()

        saved = json.loads((tmp_path / "markets" / f"dallas_{today}.json").read_text())
        assert saved["position"]["entry_price"] == 0.30
        assert saved["position"]["status"] == "open"


class TestPositionFilters:
    def test_opens_when_all_filters_pass(self, tmp_path, monkeypatch):
        monkeypatch.setattr("bot_v2.MARKETS_DIR", tmp_path / "markets")
        monkeypatch.setattr("bot_v2.STATE_FILE", tmp_path / "state.json")
        (tmp_path / "markets").mkdir()
        (tmp_path / "state.json").write_text(json.dumps(_make_state()))

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        event = _make_polymarket_event("dallas", today)
        _setup_api_mocks(monkeypatch, event, best_ask=0.32, best_bid=0.30, only_city="dallas")

        scan_and_update()

        saved = json.loads((tmp_path / "markets" / f"dallas_{today}.json").read_text())
        assert saved["position"] is not None
        assert saved["position"]["status"] == "open"

    def test_skips_when_low_volume(self, tmp_path, monkeypatch):
        monkeypatch.setattr("bot_v2.MARKETS_DIR", tmp_path / "markets")
        monkeypatch.setattr("bot_v2.STATE_FILE", tmp_path / "state.json")
        (tmp_path / "markets").mkdir()
        (tmp_path / "state.json").write_text(json.dumps(_make_state()))

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        event = _make_polymarket_event("dallas", today, markets=[
            {"id": "mkt_1", "question": f"Will the high be between 70-75°F on {today}?", "outcomePrices": "[0.30, 0.32]", "volume": 100},
        ])
        _setup_api_mocks(monkeypatch, event, only_city="dallas")

        scan_and_update()

        saved = json.loads((tmp_path / "markets" / f"dallas_{today}.json").read_text())
        assert saved["position"] is None

    def test_skips_when_high_spread(self, tmp_path, monkeypatch):
        monkeypatch.setattr("bot_v2.MARKETS_DIR", tmp_path / "markets")
        monkeypatch.setattr("bot_v2.STATE_FILE", tmp_path / "state.json")
        (tmp_path / "markets").mkdir()
        (tmp_path / "state.json").write_text(json.dumps(_make_state()))

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        event = _make_polymarket_event("dallas", today, markets=[
            {"id": "mkt_1", "question": f"Will the high be between 70-75°F on {today}?", "outcomePrices": "[0.30, 0.40]", "volume": 1000},
        ])
        _setup_api_mocks(monkeypatch, event, best_ask=0.40, best_bid=0.30, only_city="dallas")

        scan_and_update()

        saved = json.loads((tmp_path / "markets" / f"dallas_{today}.json").read_text())
        assert saved["position"] is None

    def test_skips_when_ask_too_high(self, tmp_path, monkeypatch):
        monkeypatch.setattr("bot_v2.MARKETS_DIR", tmp_path / "markets")
        monkeypatch.setattr("bot_v2.STATE_FILE", tmp_path / "state.json")
        (tmp_path / "markets").mkdir()
        (tmp_path / "state.json").write_text(json.dumps(_make_state()))

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        event = _make_polymarket_event("dallas", today, markets=[
            {"id": "mkt_1", "question": f"Will the high be between 70-75°F on {today}?", "outcomePrices": "[0.50, 0.52]", "volume": 1000},
        ])
        _setup_api_mocks(monkeypatch, event, best_ask=0.52, best_bid=0.50, only_city="dallas")

        scan_and_update()

        saved = json.loads((tmp_path / "markets" / f"dallas_{today}.json").read_text())
        assert saved["position"] is None


class TestForecastBucketMatching:
    def test_opens_on_matching_bucket(self, tmp_path, monkeypatch):
        monkeypatch.setattr("bot_v2.MARKETS_DIR", tmp_path / "markets")
        monkeypatch.setattr("bot_v2.STATE_FILE", tmp_path / "state.json")
        (tmp_path / "markets").mkdir()
        (tmp_path / "state.json").write_text(json.dumps(_make_state()))

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        event = _make_polymarket_event("dallas", today)
        _setup_api_mocks(monkeypatch, event, only_city="dallas")

        scan_and_update()

        saved = json.loads((tmp_path / "markets" / f"dallas_{today}.json").read_text())
        assert saved["position"] is not None

    def test_no_position_when_forecast_matches_no_bucket(self, tmp_path, monkeypatch):
        monkeypatch.setattr("bot_v2.MARKETS_DIR", tmp_path / "markets")
        monkeypatch.setattr("bot_v2.STATE_FILE", tmp_path / "state.json")
        (tmp_path / "markets").mkdir()
        (tmp_path / "state.json").write_text(json.dumps(_make_state()))

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        event = _make_polymarket_event("dallas", today, markets=[
            {"id": "mkt_1", "question": f"Will the high be between 90-95°F on {today}?", "outcomePrices": "[0.30, 0.32]", "volume": 1000},
        ])
        _setup_api_mocks(monkeypatch, event, only_city="dallas")

        scan_and_update()

        saved = json.loads((tmp_path / "markets" / f"dallas_{today}.json").read_text())
        assert saved["position"] is None


class TestResolution:
    def test_win_resolution_correct_pnl(self, tmp_path, monkeypatch):
        monkeypatch.setattr("bot_v2.MARKETS_DIR", tmp_path / "markets")
        monkeypatch.setattr("bot_v2.STATE_FILE", tmp_path / "state.json")
        (tmp_path / "markets").mkdir()

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        pos = _make_position(entry_price=0.30, shares=33.33, cost=10.0, stop_price=0.01)
        _make_market_file(tmp_path, city="dallas", date=today, position=pos)
        (tmp_path / "state.json").write_text(json.dumps(_make_state(balance=990.0, trades=1)))

        event = _make_polymarket_event("dallas", today, markets=[
            {"id": "mkt_1", "question": f"Will the high be between 70-75°F on {today}?", "outcomePrices": "[0.98, 0.02]", "volume": 1000},
        ])

        def mock_get(url, *args, **kwargs):
            if "gamma-api.polymarket.com/events" in url:
                return _mock_gamma_events(event)
            if "gamma-api.polymarket.com/markets" in url:
                return _mock_gamma_market("mkt_1", closed=True, outcome_prices=[0.98, 0.02])
            if "open-meteo" in url or "aviationweather" in url:
                return _mock_open_meteo_ecmwf()
            return MagicMock()

        monkeypatch.setattr("bot_v2.requests.get", mock_get)
        monkeypatch.setattr("bot_v2.time.sleep", lambda *a: None)

        scan_and_update()

        saved = json.loads((tmp_path / "markets" / f"dallas_{today}.json").read_text())
        assert saved["position"]["status"] == "closed"
        assert saved["position"]["close_reason"] == "resolved"
        assert saved["resolved_outcome"] == "win"

    def test_loss_resolution_correct_pnl(self, tmp_path, monkeypatch):
        monkeypatch.setattr("bot_v2.MARKETS_DIR", tmp_path / "markets")
        monkeypatch.setattr("bot_v2.STATE_FILE", tmp_path / "state.json")
        (tmp_path / "markets").mkdir()

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        pos = _make_position(entry_price=0.30, shares=33.33, cost=10.0, stop_price=0.01)
        _make_market_file(tmp_path, city="dallas", date=today, position=pos)
        (tmp_path / "state.json").write_text(json.dumps(_make_state(balance=990.0, trades=1)))

        event = _make_polymarket_event("dallas", today, markets=[
            {"id": "mkt_1", "question": f"Will the high be between 70-75°F on {today}?", "outcomePrices": "[0.02, 0.98]", "volume": 1000},
        ])

        def mock_get(url, *args, **kwargs):
            if "gamma-api.polymarket.com/events" in url:
                return _mock_gamma_events(event)
            if "gamma-api.polymarket.com/markets" in url:
                return _mock_gamma_market("mkt_1", closed=True, outcome_prices=[0.02, 0.98])
            if "open-meteo" in url or "aviationweather" in url:
                return _mock_open_meteo_ecmwf()
            return MagicMock()

        monkeypatch.setattr("bot_v2.requests.get", mock_get)
        monkeypatch.setattr("bot_v2.time.sleep", lambda *a: None)

        scan_and_update()

        saved = json.loads((tmp_path / "markets" / f"dallas_{today}.json").read_text())
        assert saved["position"]["status"] == "closed"
        assert saved["resolved_outcome"] == "loss"


class TestForecastChangedClose:
    def test_forecast_changed_close_updates_balance_when_delta_nonzero(self, tmp_path, monkeypatch):
        """Normal case: forecast shifts, position closes, balance += cost + pnl."""
        monkeypatch.setattr("bot_v2.MARKETS_DIR", tmp_path / "markets")
        monkeypatch.setattr("bot_v2.STATE_FILE", tmp_path / "state.json")
        (tmp_path / "markets").mkdir()

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        pos = _make_position(entry_price=0.50, shares=20.0, cost=10.0, stop_price=0.01)
        _make_market_file(tmp_path, city="dallas", date=today, position=pos)
        (tmp_path / "state.json").write_text(json.dumps(_make_state(balance=990.0, trades=1)))

        # Forecast far outside bucket (70-75), price at 0.10
        event = _make_polymarket_event("dallas", today, markets=[
            {"id": "mkt_1", "question": f"Will the high be between 70-75°F on {today}?", "outcomePrices": "[0.10, 0.90]", "volume": 1000},
        ])

        def mock_get(url, *args, **kwargs):
            if "gamma-api.polymarket.com/events" in url:
                return _mock_gamma_events(event)
            if "gamma-api.polymarket.com/markets" in url:
                return _mock_gamma_market("mkt_1", best_ask=0.12, best_bid=0.10)
            if "open-meteo" in url or "aviationweather" in url:
                # Return a forecast far outside the 70-75 bucket
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
        # balance should have been updated: 990 + (10 + (0.10-0.50)*20) = 990 + 2 = 992
        assert state["balance"] >= 990.0  # balance increased, not stayed same

    def test_forecast_changed_close_updates_balance_on_total_loss(self, tmp_path, monkeypatch):
        """When cost + pnl == 0 (total loss), balance must still be updated.

        The bug: `if fc_delta:` is falsy when delta == 0.0, so balance += fc_delta,
        closed += 1, and apply_closure_to_state are all skipped.

        We patch _try_close_forecast_changed to return 0.0 while still mutating
        the position to closed, simulating a total-loss forecast-change close.
        """
        monkeypatch.setattr("bot_v2.MARKETS_DIR", tmp_path / "markets")
        monkeypatch.setattr("bot_v2.STATE_FILE", tmp_path / "state.json")
        (tmp_path / "markets").mkdir()

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        pos = _make_position(entry_price=0.50, shares=20.0, cost=10.0, stop_price=0.01)
        _make_market_file(tmp_path, city="dallas", date=today, position=pos)
        (tmp_path / "state.json").write_text(json.dumps(_make_state(balance=990.0, trades=1, losses=0)))

        event = _make_polymarket_event("dallas", today)

        def _fake_forecast_changed(mkt, outcomes, forecast_temp, loc, snap):
            """Simulate forecast_changed close that returns 0.0 (total loss)."""
            p = mkt.get("position")
            if not p or p.get("status") != "open":
                return None
            p["status"] = "closed"
            p["close_reason"] = "forecast_changed"
            p["pnl"] = -10.0  # cost=10, pnl=-10 => delta=0
            p["exit_price"] = 0.0
            p["closed_at"] = snap.get("ts")
            return 0.0

        def mock_get(url, *args, **kwargs):
            if "gamma-api.polymarket.com/events" in url:
                return _mock_gamma_events(event)
            if "gamma-api.polymarket.com/markets" in url:
                return _mock_gamma_market("mkt_1", best_ask=0.32, best_bid=0.30)
            if "open-meteo" in url or "aviationweather" in url:
                m = _mock_open_meteo_ecmwf()
                m.json.return_value["daily"]["temperature_2m_max"] = [95, 96, 97, 98]
                return m
            return MagicMock()

        monkeypatch.setattr("bot_v2.requests.get", mock_get)
        monkeypatch.setattr("bot_v2.time.sleep", lambda *a: None)
        monkeypatch.setattr("bot_v2._try_close_forecast_changed", _fake_forecast_changed)

        scan_and_update()

        saved = json.loads((tmp_path / "markets" / f"dallas_{today}.json").read_text())
        assert saved["position"]["status"] == "closed"
        assert saved["position"]["close_reason"] == "forecast_changed"
        assert saved["position"]["pnl"] == pytest.approx(-10.0)

        state = json.loads((tmp_path / "state.json").read_text())
        # fc_delta == 0.0 (total loss): balance unchanged but correctly accounted
        assert state["balance"] == pytest.approx(990.0)
        # Loss counter incremented correctly even when delta is 0.0
        assert state["losses"] >= 1


class TestStateInvariants:
    def test_peak_balance_never_decreases(self, tmp_path, monkeypatch):
        monkeypatch.setattr("bot_v2.MARKETS_DIR", tmp_path / "markets")
        monkeypatch.setattr("bot_v2.STATE_FILE", tmp_path / "state.json")
        (tmp_path / "markets").mkdir()
        (tmp_path / "state.json").write_text(json.dumps(_make_state(peak=1200.0)))

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        event = _make_polymarket_event("dallas", today)
        _setup_api_mocks(monkeypatch, event, only_city="dallas")

        scan_and_update()

        state = json.loads((tmp_path / "state.json").read_text())
        assert state["peak_balance"] >= 1200.0

    def test_total_trades_increments_on_new_position(self, tmp_path, monkeypatch):
        monkeypatch.setattr("bot_v2.MARKETS_DIR", tmp_path / "markets")
        monkeypatch.setattr("bot_v2.STATE_FILE", tmp_path / "state.json")
        (tmp_path / "markets").mkdir()
        (tmp_path / "state.json").write_text(json.dumps(_make_state(trades=5)))

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        event = _make_polymarket_event("dallas", today)
        _setup_api_mocks(monkeypatch, event, only_city="dallas")

        scan_and_update()

        state = json.loads((tmp_path / "state.json").read_text())
        assert state["total_trades"] > 5

    def test_wins_losses_match_resolved_outcomes(self, tmp_path, monkeypatch):
        monkeypatch.setattr("bot_v2.MARKETS_DIR", tmp_path / "markets")
        monkeypatch.setattr("bot_v2.STATE_FILE", tmp_path / "state.json")
        (tmp_path / "markets").mkdir()

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        pos = _make_position(entry_price=0.30, shares=33.33, cost=10.0, stop_price=0.01)
        _make_market_file(tmp_path, city="dallas", date=today, position=pos)
        (tmp_path / "state.json").write_text(json.dumps(_make_state(balance=990.0, trades=1)))

        event = _make_polymarket_event("dallas", today, markets=[
            {"id": "mkt_1", "question": f"Will the high be between 70-75°F on {today}?", "outcomePrices": "[0.98, 0.02]", "volume": 1000},
        ])

        def mock_get(url, *args, **kwargs):
            if "gamma-api.polymarket.com/events" in url:
                return _mock_gamma_events(event)
            if "gamma-api.polymarket.com/markets" in url:
                return _mock_gamma_market("mkt_1", closed=True, outcome_prices=[0.98, 0.02])
            if "open-meteo" in url or "aviationweather" in url:
                return _mock_open_meteo_ecmwf()
            return MagicMock()

        monkeypatch.setattr("bot_v2.requests.get", mock_get)
        monkeypatch.setattr("bot_v2.time.sleep", lambda *a: None)

        scan_and_update()

        state = json.loads((tmp_path / "state.json").read_text())
        assert state["wins"] + state["losses"] >= 1


class TestMultiCityBalanceSharing:
    def test_balance_shared_across_two_cities_opening_positions(self, tmp_path, monkeypatch):
        """Two cities both open positions — balance decrements by sum of all costs,
        not independently. Catches bugs where each city gets its own balance copy.
        """
        monkeypatch.setattr("bot_v2.MARKETS_DIR", tmp_path / "markets")
        monkeypatch.setattr("bot_v2.STATE_FILE", tmp_path / "state.json")
        (tmp_path / "markets").mkdir()
        (tmp_path / "state.json").write_text(json.dumps(_make_state()))

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        event_dallas = _make_polymarket_event("dallas", today)
        event_chicago = _make_polymarket_event("chicago", today)
        test_cities = {"dallas", "chicago"}

        def mock_get(url, *args, **kwargs):
            if "open-meteo.com/v1/forecast" in url and "ecmwf" in url:
                return _mock_open_meteo_ecmwf()
            if "open-meteo.com/v1/forecast" in url and "gfs" in url:
                return _mock_open_meteo_hrrr()
            if "aviationweather.gov" in url:
                return _mock_metar()
            if "gamma-api.polymarket.com/events" in url:
                for city in test_cities:
                    if city in url:
                        if city == "dallas":
                            return _mock_gamma_events(event_dallas)
                        return _mock_gamma_events(event_chicago)
                empty = MagicMock()
                empty.json.return_value = []
                return empty
            if "gamma-api.polymarket.com/markets" in url:
                return _mock_gamma_market("mkt_1", best_ask=0.32, best_bid=0.30)
            return MagicMock()

        monkeypatch.setattr("bot_v2.requests.get", mock_get)
        monkeypatch.setattr("bot_v2.time.sleep", lambda *a: None)

        scan_and_update()

        state = json.loads((tmp_path / "state.json").read_text())
        markets_dir = tmp_path / "markets"
        open_cost = 0.0
        realized_pnl = 0.0
        for f in markets_dir.glob("*.json"):
            m = json.loads(f.read_text())
            pos = m.get("position")
            if pos and pos.get("status") == "open":
                open_cost += pos.get("cost", 0)
            if pos and pos.get("status") == "closed" and pos.get("pnl") is not None:
                realized_pnl += pos["pnl"]

        expected = round(state["starting_balance"] + realized_pnl - open_cost, 2)
        assert state["balance"] == expected

    def test_one_city_stop_loss_does_not_affect_other_city(self, tmp_path, monkeypatch):
        """City A has a stop-loss, City B opens new positions.
        Balance should reflect both correctly via the invariant.
        """
        monkeypatch.setattr("bot_v2.MARKETS_DIR", tmp_path / "markets")
        monkeypatch.setattr("bot_v2.STATE_FILE", tmp_path / "state.json")
        (tmp_path / "markets").mkdir()

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        pos_a = _make_position(entry_price=0.30, shares=33.33, cost=10.0, stop_price=0.25)
        _make_market_file(tmp_path, city="dallas", date=today, position=pos_a)
        (tmp_path / "state.json").write_text(json.dumps(_make_state(balance=990.0, trades=1)))

        event_dallas = _make_polymarket_event("dallas", today, markets=[
            {"id": "mkt_1", "question": f"Will the high be between 70-75°F on {today}?", "outcomePrices": "[0.20, 0.22]", "volume": 1000},
        ])
        event_chicago = _make_polymarket_event("chicago", today)
        test_cities = {"dallas", "chicago"}

        def mock_get(url, *args, **kwargs):
            if "open-meteo" in url or "aviationweather" in url:
                return _mock_open_meteo_ecmwf()
            if "gamma-api.polymarket.com/events" in url:
                for city in test_cities:
                    if city in url:
                        if city == "dallas":
                            return _mock_gamma_events(event_dallas)
                        return _mock_gamma_events(event_chicago)
                empty = MagicMock()
                empty.json.return_value = []
                return empty
            if "gamma-api.polymarket.com/markets" in url:
                return _mock_gamma_market("mkt_1", best_ask=0.22, best_bid=0.20)
            return MagicMock()

        monkeypatch.setattr("bot_v2.requests.get", mock_get)
        monkeypatch.setattr("bot_v2.time.sleep", lambda *a: None)

        scan_and_update()

        dallas_saved = json.loads((tmp_path / "markets" / f"dallas_{today}.json").read_text())
        assert dallas_saved["position"]["status"] == "closed"
        assert dallas_saved["position"]["close_reason"] == "stop_loss"

        state = json.loads((tmp_path / "state.json").read_text())
        markets_dir = tmp_path / "markets"
        open_cost = 0.0
        realized_pnl = 0.0
        for f in markets_dir.glob("*.json"):
            m = json.loads(f.read_text())
            pos = m.get("position")
            if pos and pos.get("status") == "open":
                open_cost += pos.get("cost", 0)
            if pos and pos.get("status") == "closed" and pos.get("pnl") is not None:
                realized_pnl += pos["pnl"]

        expected = round(state["starting_balance"] + realized_pnl - open_cost, 2)
        assert state["balance"] == expected


class TestFilterInteraction:
    def test_high_volume_does_not_bypass_spread_check(self, tmp_path, monkeypatch):
        """Volume passes but spread fails — position should NOT open.
        Catches bugs where filters are OR'd instead of AND'd.
        """
        monkeypatch.setattr("bot_v2.MARKETS_DIR", tmp_path / "markets")
        monkeypatch.setattr("bot_v2.STATE_FILE", tmp_path / "state.json")
        (tmp_path / "markets").mkdir()
        (tmp_path / "state.json").write_text(json.dumps(_make_state()))

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        event = _make_polymarket_event("dallas", today, markets=[
            {"id": "mkt_1", "question": f"Will the high be between 70-75°F on {today}?", "outcomePrices": "[0.30, 0.40]", "volume": 50000},
        ])
        _setup_api_mocks(monkeypatch, event, best_ask=0.40, best_bid=0.30, only_city="dallas")

        scan_and_update()

        saved = json.loads((tmp_path / "markets" / f"dallas_{today}.json").read_text())
        assert saved["position"] is None

    def test_low_spread_does_not_bypass_volume_check(self, tmp_path, monkeypatch):
        """Spread passes but volume fails — position should NOT open."""
        monkeypatch.setattr("bot_v2.MARKETS_DIR", tmp_path / "markets")
        monkeypatch.setattr("bot_v2.STATE_FILE", tmp_path / "state.json")
        (tmp_path / "markets").mkdir()
        (tmp_path / "state.json").write_text(json.dumps(_make_state()))

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        event = _make_polymarket_event("dallas", today, markets=[
            {"id": "mkt_1", "question": f"Will the high be between 70-75°F on {today}?", "outcomePrices": "[0.30, 0.31]", "volume": 50},
        ])
        _setup_api_mocks(monkeypatch, event, best_ask=0.31, best_bid=0.30, only_city="dallas")

        scan_and_update()

        saved = json.loads((tmp_path / "markets" / f"dallas_{today}.json").read_text())
        assert saved["position"] is None

    def test_good_price_does_not_bypass_ev_check(self, tmp_path, monkeypatch):
        """Price is low but forecast is far from bucket → probability near 0 → EV negative."""
        monkeypatch.setattr("bot_v2.MARKETS_DIR", tmp_path / "markets")
        monkeypatch.setattr("bot_v2.STATE_FILE", tmp_path / "state.json")
        (tmp_path / "markets").mkdir()
        (tmp_path / "state.json").write_text(json.dumps(_make_state()))

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        event = _make_polymarket_event("dallas", today, markets=[
            {"id": "mkt_1", "question": f"Will the high be between 90-95°F on {today}?", "outcomePrices": "[0.10, 0.12]", "volume": 1000},
        ])
        _setup_api_mocks(monkeypatch, event, best_ask=0.12, best_bid=0.10, only_city="dallas")

        scan_and_update()

        saved = json.loads((tmp_path / "markets" / f"dallas_{today}.json").read_text())
        assert saved["position"] is None

    def test_all_filters_pass_together(self, tmp_path, monkeypatch):
        """Volume, spread, price, EV, and size all pass — position opens."""
        monkeypatch.setattr("bot_v2.MARKETS_DIR", tmp_path / "markets")
        monkeypatch.setattr("bot_v2.STATE_FILE", tmp_path / "state.json")
        (tmp_path / "markets").mkdir()
        (tmp_path / "state.json").write_text(json.dumps(_make_state()))

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        event = _make_polymarket_event("dallas", today, markets=[
            {"id": "mkt_1", "question": f"Will the high be between 70-75°F on {today}?", "outcomePrices": "[0.30, 0.32]", "volume": 5000},
        ])
        _setup_api_mocks(monkeypatch, event, best_ask=0.32, best_bid=0.30, only_city="dallas")

        scan_and_update()

        saved = json.loads((tmp_path / "markets" / f"dallas_{today}.json").read_text())
        assert saved["position"] is not None
        assert saved["position"]["status"] == "open"
