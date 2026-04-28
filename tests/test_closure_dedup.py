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


class TestReconcileStateFromLedger:
    def _setup(self, tmp_path, monkeypatch, state, ledger_rows):
        state_path  = tmp_path / "state.json"
        ledger_path = tmp_path / "closures.jsonl"
        state_path.write_text(json.dumps(state))
        if ledger_rows:
            _write_jsonl(ledger_path, ledger_rows)
        monkeypatch.setattr(bot_v2, "STATE_FILE",  state_path)
        monkeypatch.setattr(bot_v2, "LEDGER_FILE", ledger_path)
        return state_path, ledger_path

    def _read(self, path):
        return json.loads(path.read_text())

    def test_corrects_stale_realized_pnl(self, tmp_path, monkeypatch):
        rows = [_row(market_id="m1", reason="stop", pnl=-2.5),
                _row(market_id="m2", reason="resolution", pnl=5.0)]
        state_path, _ = self._setup(tmp_path, monkeypatch,
            state={"balance": 1002.5, "starting_balance": 1000.0,
                   "wins": 0, "losses": 0, "realized_pnl": 0.0},
            ledger_rows=rows)
        state = bot_v2.load_state()
        bot_v2.reconcile_state_from_ledger(state)
        assert state["realized_pnl"] == 2.5
        assert state["wins"]   == 1
        assert state["losses"] == 1
        # Persisted to disk
        on_disk = self._read(state_path)
        assert on_disk["realized_pnl"] == 2.5
        assert on_disk["wins"]   == 1
        assert on_disk["losses"] == 1

    def test_dedupes_duplicate_rows_before_summing(self, tmp_path, monkeypatch):
        rows = [_row(market_id="m1", reason="stop", pnl=-2.5),
                _row(market_id="m1", reason="stop", pnl=-2.5)]
        self._setup(tmp_path, monkeypatch,
            state={"balance": 997.5, "starting_balance": 1000.0,
                   "wins": 0, "losses": 0, "realized_pnl": 0.0},
            ledger_rows=rows)
        state = bot_v2.load_state()
        bot_v2.reconcile_state_from_ledger(state)
        # Only one closure counted, not two
        assert state["realized_pnl"] == -2.5
        assert state["losses"] == 1

    def test_idempotent(self, tmp_path, monkeypatch):
        rows = [_row(market_id="m1", reason="stop", pnl=-2.5)]
        state_path, _ = self._setup(tmp_path, monkeypatch,
            state={"balance": 997.5, "starting_balance": 1000.0,
                   "wins": 0, "losses": 1, "realized_pnl": -2.5},
            ledger_rows=rows)
        before = state_path.stat().st_mtime_ns
        state = bot_v2.load_state()
        bot_v2.reconcile_state_from_ledger(state)
        bot_v2.reconcile_state_from_ledger(state)  # second call
        after = state_path.stat().st_mtime_ns
        # No drift means no save_state — file mtime unchanged
        assert before == after

    def test_no_write_when_drift_within_one_cent(self, tmp_path, monkeypatch):
        # ledger sums to -2.50, state has -2.501; drift = 0.001 < $0.01 threshold
        rows = [_row(market_id="m1", reason="stop", pnl=-2.50)]
        state_path, _ = self._setup(tmp_path, monkeypatch,
            state={"balance": 997.499, "starting_balance": 1000.0,
                   "wins": 0, "losses": 1, "realized_pnl": -2.501},
            ledger_rows=rows)
        before = state_path.stat().st_mtime_ns
        state = bot_v2.load_state()
        bot_v2.reconcile_state_from_ledger(state)
        after = state_path.stat().st_mtime_ns
        assert before == after  # threshold protected float noise

    def test_handles_empty_ledger(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch,
            state={"balance": 1000.0, "starting_balance": 1000.0,
                   "wins": 0, "losses": 0, "realized_pnl": 0.0},
            ledger_rows=[])
        state = bot_v2.load_state()
        bot_v2.reconcile_state_from_ledger(state)
        # No drift, nothing to do
        assert state["realized_pnl"] == 0.0
        assert state["wins"] == 0
        assert state["losses"] == 0

    def test_filters_reset_markers(self, tmp_path, monkeypatch):
        rows = [
            {"type": "reset", "ts": "2026-01-01T00:00:00Z", "starting_balance": 1000.0},
            _row(market_id="m1", reason="stop", pnl=-2.5),
        ]
        self._setup(tmp_path, monkeypatch,
            state={"balance": 997.5, "starting_balance": 1000.0,
                   "wins": 0, "losses": 0, "realized_pnl": 0.0},
            ledger_rows=rows)
        state = bot_v2.load_state()
        bot_v2.reconcile_state_from_ledger(state)
        # Reset marker doesn't contribute pnl
        assert state["realized_pnl"] == -2.5
        assert state["losses"] == 1


class TestMigrationTwoStep:
    """End-to-end test for the migration scenario: state has wins/losses
    recorded but realized_pnl=0 (because the field was added retroactively),
    closures.jsonl is empty, and market files have the closed positions.
    The startup path (maybe_backfill_ledger then reconcile_state_from_ledger)
    must self-heal both the ledger and state."""

    def test_two_step_migration_path(self, tmp_path, monkeypatch):
        # Wire up isolated paths
        markets_dir = tmp_path / "markets"
        markets_dir.mkdir()
        state_path  = tmp_path / "state.json"
        ledger_path = tmp_path / "closures.jsonl"

        monkeypatch.setattr(bot_v2, "MARKETS_DIR", markets_dir)
        monkeypatch.setattr(bot_v2, "DATA_DIR",    tmp_path)
        monkeypatch.setattr(bot_v2, "STATE_FILE",  state_path)
        monkeypatch.setattr(bot_v2, "LEDGER_FILE", ledger_path)

        # Pre-migration state: counters are recorded, but realized_pnl is 0
        # (the field was added after these closures happened) AND the ledger
        # is empty (closures.jsonl was added even later, in the closure-ledger
        # feature).
        state_path.write_text(json.dumps({
            "balance":          1002.5,
            "starting_balance": 1000.0,
            "wins":   1,
            "losses": 1,
            "realized_pnl": 0.0,
        }))
        # No closures.jsonl yet.
        assert not ledger_path.exists()

        # Two market files representing the recorded closures
        win_market = {
            "city": "dallas", "city_name": "Dallas", "date": "2026-01-01",
            "unit": "F", "station": "KDAL", "status": "resolved",
            "position": {
                "market_id": "mkt_win", "status": "closed",
                "entry_price": 0.30, "shares": 33.33, "cost": 10.0,
                "exit_price": 0.45, "pnl": 5.0, "close_reason": "resolution",
                "opened_at": "2026-01-01T00:00:00Z",
                "closed_at": "2026-01-02T00:00:00Z",
                "bucket_low": 70, "bucket_high": 75,
            },
            "actual_temp": 72.0, "resolved_outcome": "yes", "pnl": 5.0,
            "forecast_snapshots": [], "market_snapshots": [], "all_outcomes": [],
            "event_end_date": "", "hours_at_discovery": 24.0,
        }
        loss_market = {
            "city": "houston", "city_name": "Houston", "date": "2026-01-03",
            "unit": "F", "station": "KIAH", "status": "closed",
            "position": {
                "market_id": "mkt_loss", "status": "closed",
                "entry_price": 0.40, "shares": 25.0, "cost": 10.0,
                "exit_price": 0.30, "pnl": -2.5, "close_reason": "stop",
                "opened_at": "2026-01-03T00:00:00Z",
                "closed_at": "2026-01-04T00:00:00Z",
                "bucket_low": 75, "bucket_high": 80,
            },
            "actual_temp": None, "resolved_outcome": None, "pnl": None,
            "forecast_snapshots": [], "market_snapshots": [], "all_outcomes": [],
            "event_end_date": "", "hours_at_discovery": 24.0,
        }
        (markets_dir / "dallas_2026-01-01.json").write_text(json.dumps(win_market))
        (markets_dir / "houston_2026-01-03.json").write_text(json.dumps(loss_market))

        # Run the startup self-heal sequence in the SAME order run() does
        # (state load first, then ledger backfill, then reconcile against
        # the now-populated ledger). Order-mirroring matters as a regression
        # guard if maybe_backfill_ledger ever grows state-mutating effects.
        state = bot_v2.load_state()
        bot_v2.maybe_backfill_ledger()
        bot_v2.reconcile_state_from_ledger(state)

        # Step 1 outcome: ledger now has both closures
        assert ledger_path.exists()
        ledger_rows = [json.loads(l) for l in ledger_path.read_text().splitlines() if l.strip()]
        assert len(ledger_rows) == 2

        # Step 2 outcome: realized_pnl reflects the ledger sum (5.0 - 2.5 = 2.5)
        on_disk = json.loads(state_path.read_text())
        assert on_disk["realized_pnl"] == 2.5
        assert on_disk["wins"]   == 1
        assert on_disk["losses"] == 1
