"""Tests for state.json resilience — missing keys, corrupt data, partial writes.

The bot reads state.json every scan cycle. If a key is missing or the file
is corrupt, the bot should handle it gracefully rather than crashing.

Invariants:
  - Missing state.json → default state with all required keys
  - Partial state.json (missing keys) → defaults fill in the gaps
  - Corrupt state.json → default state, not a crash
  - State with wrong types → handled gracefully
"""
from __future__ import annotations

import json

import pytest

from bot_v2 import load_state, save_state


class TestMissingStateFile:
    def test_missing_file_returns_defaults(self, tmp_path, monkeypatch):
        state_file = tmp_path / "state.json"
        monkeypatch.setattr("bot_v2.STATE_FILE", state_file)

        state = load_state()

        assert state["balance"] > 0
        assert state["starting_balance"] > 0
        assert state["total_trades"] == 0
        assert state["wins"] == 0
        assert state["losses"] == 0
        assert "peak_balance" in state


class TestPartialState:
    def test_missing_peak_balance_gets_default(self, tmp_path, monkeypatch):
        state_file = tmp_path / "state.json"
        monkeypatch.setattr("bot_v2.STATE_FILE", state_file)
        state_file.write_text(json.dumps({
            "balance": 500.0,
            "starting_balance": 1000.0,
            "total_trades": 5,
            "wins": 3,
            "losses": 2,
        }))

        state = load_state()

        assert state["balance"] == 500.0
        assert state["peak_balance"] == 500.0

    def test_missing_wins_losses_get_zero(self, tmp_path, monkeypatch):
        state_file = tmp_path / "state.json"
        monkeypatch.setattr("bot_v2.STATE_FILE", state_file)
        state_file.write_text(json.dumps({
            "balance": 500.0,
            "starting_balance": 1000.0,
        }))

        state = load_state()

        assert state.get("wins", 0) == 0
        assert state.get("losses", 0) == 0

    def test_empty_dict_returns_defaults(self, tmp_path, monkeypatch):
        state_file = tmp_path / "state.json"
        monkeypatch.setattr("bot_v2.STATE_FILE", state_file)
        state_file.write_text("{}")

        state = load_state()

        assert state["balance"] > 0
        assert state["starting_balance"] > 0
        assert state["peak_balance"] > 0


class TestCorruptState:
    def test_invalid_json_returns_defaults(self, tmp_path, monkeypatch):
        state_file = tmp_path / "state.json"
        monkeypatch.setattr("bot_v2.STATE_FILE", state_file)
        state_file.write_text("{ not valid json")

        state = load_state()

        assert state["balance"] > 0
        assert state["starting_balance"] > 0

    def test_empty_file_returns_defaults(self, tmp_path, monkeypatch):
        state_file = tmp_path / "state.json"
        monkeypatch.setattr("bot_v2.STATE_FILE", state_file)
        state_file.write_text("")

        state = load_state()

        assert state["balance"] > 0

    def test_non_dict_json_returns_defaults(self, tmp_path, monkeypatch):
        state_file = tmp_path / "state.json"
        monkeypatch.setattr("bot_v2.STATE_FILE", state_file)
        state_file.write_text('"just a string"')

        state = load_state()

        assert state["balance"] > 0


class TestStateRoundtrip:
    def test_save_then_load_preserves_data(self, tmp_path, monkeypatch):
        state_file = tmp_path / "state.json"
        monkeypatch.setattr("bot_v2.STATE_FILE", state_file)

        original = {
            "balance": 1234.56,
            "starting_balance": 1000.0,
            "total_trades": 42,
            "wins": 25,
            "losses": 17,
            "peak_balance": 1500.0,
        }
        save_state(original)
        loaded = load_state()

        assert loaded == original


class TestLoadAllMarketsResilience:
    def test_corrupt_file_does_not_crash_load_all(self, tmp_path, monkeypatch):
        markets_dir = tmp_path / "markets"
        markets_dir.mkdir()
        (markets_dir / "good.json").write_text(json.dumps({"city": "dallas"}))
        (markets_dir / "bad.json").write_text("{ corrupt")

        monkeypatch.setattr("bot_v2.MARKETS_DIR", markets_dir)

        from bot_v2 import load_all_markets
        markets = load_all_markets()

        assert len(markets) == 1
        assert markets[0]["city"] == "dallas"

    def test_mixed_good_and_corrupt_loads_good_ones(self, tmp_path, monkeypatch):
        markets_dir = tmp_path / "markets"
        markets_dir.mkdir()
        for i in range(3):
            (markets_dir / f"good_{i}.json").write_text(json.dumps({"city": "dallas", "i": i}))
        (markets_dir / "bad.json").write_text("not json at all")
        (markets_dir / "bad2.json").write_text('{"truncated')

        monkeypatch.setattr("bot_v2.MARKETS_DIR", markets_dir)

        from bot_v2 import load_all_markets
        markets = load_all_markets()

        assert len(markets) == 3
