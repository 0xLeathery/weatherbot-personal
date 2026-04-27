"""Tests for the closure-ledger dedup contract.

closures.jsonl is at-least-once: the bot may write the same logical
closure twice if a crash happens between record_closure and save_state.
Consumers (the bot's own startup reconciler and the dashboard) must dedup
by (market_id, close_reason), keeping the first row. Non-closure types
(e.g. reset markers) are filtered. Rows lacking market_id pass through
unchanged so legacy data isn't spuriously collapsed.
"""
from __future__ import annotations

import json

import bot_v2


def _row(market_id="mkt_1", reason="stop", pnl=-2.5, ts="2026-01-01T00:00:00Z", **extra):
    base = {
        "type": "closure",
        "ts": ts,
        "market_id": market_id,
        "close_reason": reason,
        "pnl": pnl,
    }
    base.update(extra)
    return base


def _write_jsonl(path, rows):
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


class TestDedupClosures:
    def test_keeps_first_of_duplicate_pair(self):
        a = _row(ts="2026-01-01T00:00:00Z", pnl=-2.5)
        b = _row(ts="2026-01-01T00:00:01Z", pnl=-2.5)  # same (market_id, reason)
        result = bot_v2._dedup_closures([a, b])
        assert result == [a]

    def test_distinct_market_ids_preserved(self):
        a = _row(market_id="mkt_1")
        b = _row(market_id="mkt_2")
        assert bot_v2._dedup_closures([a, b]) == [a, b]

    def test_distinct_close_reasons_preserved(self):
        a = _row(reason="stop")
        b = _row(reason="resolution")
        assert bot_v2._dedup_closures([a, b]) == [a, b]

    def test_legacy_rows_without_market_id_pass_through(self):
        a = _row(market_id=None, reason="stop")
        b = _row(market_id=None, reason="stop")
        assert bot_v2._dedup_closures([a, b]) == [a, b]

    def test_non_closure_types_filtered_out(self):
        marker = {"type": "reset", "ts": "2026-01-01T00:00:00Z", "starting_balance": 1000.0}
        a = _row()
        assert bot_v2._dedup_closures([marker, a]) == [a]


class TestLoadClosures:
    def test_returns_empty_when_file_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(bot_v2, "LEDGER_FILE", tmp_path / "missing.jsonl")
        assert bot_v2._load_closures() == []

    def test_skips_blank_lines(self, tmp_path, monkeypatch):
        path = tmp_path / "closures.jsonl"
        path.write_text("\n" + json.dumps(_row()) + "\n\n", encoding="utf-8")
        monkeypatch.setattr(bot_v2, "LEDGER_FILE", path)
        assert len(bot_v2._load_closures()) == 1

    def test_skips_malformed_lines(self, tmp_path, monkeypatch, capsys):
        path = tmp_path / "closures.jsonl"
        path.write_text("not json\n" + json.dumps(_row()) + "\n", encoding="utf-8")
        monkeypatch.setattr(bot_v2, "LEDGER_FILE", path)
        rows = bot_v2._load_closures()
        assert len(rows) == 1
        captured = capsys.readouterr()
        assert "skipping malformed line 1" in captured.out
