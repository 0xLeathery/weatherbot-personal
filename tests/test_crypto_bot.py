import json
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import crypto_bot

def test_position_path_uses_market_id(tmp_path, monkeypatch):
    monkeypatch.setattr(crypto_bot, "POSITIONS_DIR", tmp_path)
    assert crypto_bot.position_path("mkt-123") == tmp_path / "mkt-123.json"

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
    """If a position for market_id already exists, load_position returns it (non-None)."""
    monkeypatch.setattr(crypto_bot, "POSITIONS_DIR", tmp_path)
    pos = {"market_id": "mkt-dup", "symbol": "ETH", "status": "open"}
    crypto_bot.save_position(pos)
    # Non-None return value is what the duplicate guard checks
    assert crypto_bot.load_position("mkt-dup") is not None
    assert crypto_bot.load_position("mkt-new") is None
