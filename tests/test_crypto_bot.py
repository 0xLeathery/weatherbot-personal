import json
import sys
from pathlib import Path
import pytest
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))
import crypto_bot


def test_load_position_missing_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(crypto_bot, "POSITIONS_DIR", tmp_path)
    assert crypto_bot.load_position("does-not-exist") is None

def test_save_and_load_position_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(crypto_bot, "POSITIONS_DIR", tmp_path)
    pos = {"market_id": "mkt-abc", "symbol": "BTC", "status": "open", "cost": 10.0}
    crypto_bot.save_position(pos)
    loaded = crypto_bot.load_position("mkt-abc")
    assert loaded == pos

def test_two_positions_same_symbol_same_day_no_collision(tmp_path, monkeypatch):
    monkeypatch.setattr(crypto_bot, "POSITIONS_DIR", tmp_path)
    pos1 = {"market_id": "mkt-1", "symbol": "BTC", "status": "open"}
    pos2 = {"market_id": "mkt-2", "symbol": "BTC", "status": "open"}
    crypto_bot.save_position(pos1)
    crypto_bot.save_position(pos2)
    assert len(list(tmp_path.glob("*.json"))) == 2

def test_load_position_returns_existing_for_duplicate_check(tmp_path, monkeypatch):
    monkeypatch.setattr(crypto_bot, "POSITIONS_DIR", tmp_path)
    pos = {"market_id": "mkt-dup", "symbol": "ETH", "status": "open"}
    crypto_bot.save_position(pos)
    assert crypto_bot.load_position("mkt-dup") is not None
    assert crypto_bot.load_position("mkt-new") is None

def _mock_gamma_response(closed, yes_price):
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"closed": closed, "outcomePrices": f"[{yes_price}, {1 - yes_price}]"}
    mock_resp.raise_for_status.return_value = None
    return mock_resp

def test_check_market_resolved_still_open():
    with patch("crypto_bot.requests.get") as mock_get:
        mock_get.return_value = _mock_gamma_response(closed=False, yes_price=0.55)
        outcome, price = crypto_bot.check_market_resolved("mkt-open")
        assert outcome is None and price == 0.5

def test_check_market_resolved_yes_wins():
    with patch("crypto_bot.requests.get") as mock_get:
        mock_get.return_value = _mock_gamma_response(closed=True, yes_price=0.98)
        outcome, price = crypto_bot.check_market_resolved("mkt-win")
        assert outcome is True and price == 0.98

def test_check_market_resolved_no_wins():
    with patch("crypto_bot.requests.get") as mock_get:
        mock_get.return_value = _mock_gamma_response(closed=True, yes_price=0.02)
        outcome, price = crypto_bot.check_market_resolved("mkt-loss")
        assert outcome is False and price == 0.02

def test_check_market_resolved_ambiguous():
    with patch("crypto_bot.requests.get") as mock_get:
        mock_get.return_value = _mock_gamma_response(closed=True, yes_price=0.50)
        outcome, price = crypto_bot.check_market_resolved("mkt-ambig")
        assert outcome is None and price == 0.50

def test_check_market_resolved_api_error():
    with patch("crypto_bot.requests.get") as mock_get:
        mock_get.side_effect = Exception("timeout")
        outcome, price = crypto_bot.check_market_resolved("mkt-err")
        assert outcome is None and price == 0.5

def test_check_market_resolved_prices_yes_is_index_zero():
    with patch("crypto_bot.requests.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"closed": True, "outcomePrices": "[0.98, 0.02]", "outcomes": ["Yes", "No"]}
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp
        outcome, price = crypto_bot.check_market_resolved("mkt-test")
        assert outcome is True and price == 0.98

def _open_position(market_id, shares, cost, entry_price):
    return {"market_id": market_id, "symbol": "BTC", "question": f"Will BTC hit target? ({market_id})", "side": "above", "shares": shares, "cost": cost, "entry_price": entry_price, "status": "open", "pnl": None}

def test_settle_positions_win(tmp_path, monkeypatch):
    monkeypatch.setattr(crypto_bot, "POSITIONS_DIR", tmp_path)
    pos = _open_position("mkt-w", shares=20.0, cost=10.0, entry_price=0.50)
    crypto_bot.save_position(pos)
    state = {"balance": 990.0, "peak_balance": 990.0, "wins": 0, "losses": 0}
    with patch("crypto_bot.check_market_resolved", return_value=(True, 1.0)):
        crypto_bot.settle_positions(state)
    settled = crypto_bot.load_position("mkt-w")
    assert settled["status"] == "closed" and settled["exit_price"] == 1.0 and settled["pnl"] == 10.0
    assert state["balance"] == 1010.0 and state["wins"] == 1 and state["losses"] == 0

def test_settle_positions_loss(tmp_path, monkeypatch):
    monkeypatch.setattr(crypto_bot, "POSITIONS_DIR", tmp_path)
    pos = _open_position("mkt-l", shares=16.67, cost=10.0, entry_price=0.60)
    crypto_bot.save_position(pos)
    state = {"balance": 990.0, "peak_balance": 990.0, "wins": 0, "losses": 0}
    with patch("crypto_bot.check_market_resolved", return_value=(False, 0.0)):
        crypto_bot.settle_positions(state)
    settled = crypto_bot.load_position("mkt-l")
    assert settled["status"] == "closed" and settled["exit_price"] == 0.0 and settled["pnl"] == -10.0
    assert state["balance"] == 990.0 and state["losses"] == 1

def test_settle_positions_skips_already_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(crypto_bot, "POSITIONS_DIR", tmp_path)
    pos = _open_position("mkt-c", shares=10.0, cost=5.0, entry_price=0.50)
    pos["status"] = "closed"
    pos["pnl"] = 5.0
    crypto_bot.save_position(pos)
    state = {"balance": 1000.0, "peak_balance": 1000.0, "wins": 1, "losses": 0}
    with patch("crypto_bot.check_market_resolved", return_value=(True, 1.0)) as mock_check:
        crypto_bot.settle_positions(state)
    mock_check.assert_not_called()
    assert state["balance"] == 1000.0

def test_settle_positions_skips_still_open(tmp_path, monkeypatch):
    monkeypatch.setattr(crypto_bot, "POSITIONS_DIR", tmp_path)
    pos = _open_position("mkt-s", shares=10.0, cost=5.0, entry_price=0.50)
    crypto_bot.save_position(pos)
    state = {"balance": 995.0, "peak_balance": 1000.0, "wins": 0, "losses": 0}
    with patch("crypto_bot.check_market_resolved", return_value=(None, 0.5)):
        crypto_bot.settle_positions(state)
    loaded = crypto_bot.load_position("mkt-s")
    assert loaded["status"] == "open" and state["balance"] == 995.0

def test_load_position_corrupt_json_skipped(tmp_path, monkeypatch):
    monkeypatch.setattr(crypto_bot, "POSITIONS_DIR", tmp_path)
    (tmp_path / "mkt-bad.json").write_text("{invalid json")
    (tmp_path / "mkt-good.json").write_text('{"market_id": "mkt-good", "status": "closed"}')
    state = {"balance": 1000, "peak_balance": 1000, "wins": 0, "losses": 0}
    crypto_bot.settle_positions(state)
    assert state["balance"] == 1000

def test_settle_positions_two_sequential_no_double_count(tmp_path, monkeypatch):
    monkeypatch.setattr(crypto_bot, "POSITIONS_DIR", tmp_path)
    pos1 = _open_position("mkt-1", shares=10.0, cost=10.0, entry_price=0.50)
    pos2 = _open_position("mkt-2", shares=20.0, cost=20.0, entry_price=0.50)
    crypto_bot.save_position(pos1)
    crypto_bot.save_position(pos2)
    state = {"balance": 970.0, "peak_balance": 1000.0, "wins": 0, "losses": 0}
    with patch("crypto_bot.check_market_resolved") as mock_check:
        mock_check.side_effect = [(True, 1.0), (True, 1.0)]
        crypto_bot.settle_positions(state)
    assert state["balance"] == 1015.0 and state["wins"] == 2
