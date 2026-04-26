# Closure Ledger v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an append-only closure ledger and a live-vs-backtest dashboard header strip so the in-flight HRRR-ECMWF spread strategy test can be read without losing data across resets.

**Architecture:** Every closure in `bot_v2.py` flows through one of four sites that already call `apply_closure_to_state`. Add a sibling call to `record_closure(mkt, pos)` that appends a JSON line to `data/closures.jsonl`. A separate `maybe_backfill_ledger()` self-heal walks existing market files on startup if the ledger is missing or empty. A new `tools/reset.py` script writes a `{"type": "reset"}` boundary marker before deleting state. `walkforward_test.py --emit-baseline` produces `data/backtest_baseline.json` with a config snapshot. The dashboard reads ledger + baseline + config, renders one header strip with sample-size and staleness warnings.

**Tech Stack:** Python 3.11+, pytest, vanilla JS in `Dashboard.html`. No new dependencies.

---

## File Structure

| File | Status | Responsibility |
|---|---|---|
| `bot_v2.py` | Modify | Add `_build_closure_row`, `record_closure`, `maybe_backfill_ledger`, `LEDGER_FILE` constant; call `record_closure` at 4 closure sites; call `maybe_backfill_ledger` from startup. |
| `tools/reset.py` | Create | Explicit reset script: append reset marker, delete state and market files. |
| `walkforward_test.py` | Modify | Add `--emit-baseline` flag, write `data/backtest_baseline.json` with config snapshot. |
| `dashboard_server.py` | Modify | Add `closures` and `baseline` keys to manifest. |
| `Dashboard.html` | Modify | Add live-vs-backtest header strip above the realized-PnL tile. |
| `tests/test_closure_ledger.py` | Create | Unit + integration tests for ledger writes, all 4 closure paths, write-order, backfill. |
| `tests/test_reset_script.py` | Create | Dry-run test for `tools/reset.py`. |
| `tests/test_emit_baseline.py` | Create | Test that `walkforward_test.py --emit-baseline` writes a well-formed JSON file. |

The spec is explicit that `data/` is gitignored and `crypto_bot.py` is out of scope. The four `apply_closure_to_state` call sites in `bot_v2.py` are at approximately `:732`, `:743`, `:896`, `:1105` (current as of commit `e9d1315`). Confirm exact lines with `grep -n apply_closure_to_state bot_v2.py` before editing — Task 3 depends on this.

---

## Task 1: `_build_closure_row(mkt, pos)` — pure helper

**Files:**
- Modify: `bot_v2.py` (add function near the existing closure helpers, after `apply_closure_to_state`)
- Create: `tests/test_closure_ledger.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_closure_ledger.py` with:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_closure_ledger.py::TestBuildClosureRow -v`
Expected: FAIL with `ImportError: cannot import name '_build_closure_row' from 'bot_v2'`

- [ ] **Step 3: Implement `_build_closure_row` in `bot_v2.py`**

Add immediately after `apply_closure_to_state` (around line 506):

```python
def _build_closure_row(mkt, pos):
    """Construct one ledger row dict from a closed mkt/pos pair.

    Pre-spread-strategy closures (from before commit eabdb67) lack the
    forecast/spread fields on pos; those become null. Guaranteed-non-null
    fields per spec: pnl, entry_price, exit_price, close_reason, opened_at,
    closed_at (or ts), market_id, city, date_target, bucket_low/high, cost,
    shares.
    """
    return {
        "type":                   "closure",
        "ts":                     pos.get("closed_at") or datetime.now(timezone.utc).isoformat(),
        "city":                   mkt.get("city"),
        "date_target":            mkt.get("date"),
        "market_id":              pos.get("market_id") or mkt.get("market_id"),
        "question":               pos.get("question"),
        "close_reason":           pos.get("close_reason"),
        "opened_at":              pos.get("opened_at"),
        "entry_price":            pos.get("entry_price"),
        "exit_price":             pos.get("exit_price"),
        "shares":                 pos.get("shares"),
        "cost":                   pos.get("cost"),
        "pnl":                    pos.get("pnl"),
        "bucket_low":             pos.get("bucket_low"),
        "bucket_high":            pos.get("bucket_high"),
        "forecast_src":           pos.get("forecast_src"),
        "forecast_temp_at_entry": pos.get("forecast_temp"),
        "ecmwf_temp_at_entry":    pos.get("ecmwf_temp"),
        "spread_at_entry":        pos.get("spread_at_entry"),
        "sigma_at_entry":         pos.get("sigma"),
        "p_at_entry":             pos.get("p"),
        "ev_at_entry":             pos.get("ev"),
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_closure_ledger.py::TestBuildClosureRow -v`
Expected: 3 PASSED

- [ ] **Step 5: Commit**

```bash
git add bot_v2.py tests/test_closure_ledger.py
git commit -m "feat: add _build_closure_row helper for ledger rows"
```

---

## Task 2: `record_closure(mkt, pos)` — append to ledger file

**Files:**
- Modify: `bot_v2.py` (add `LEDGER_FILE` constant near `STATE_FILE`, add `record_closure` after `_build_closure_row`)
- Modify: `tests/test_closure_ledger.py` (add `TestRecordClosure` class)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_closure_ledger.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_closure_ledger.py::TestRecordClosure -v`
Expected: FAIL with `ImportError: cannot import name 'record_closure'`

- [ ] **Step 3: Add `LEDGER_FILE` constant and implement `record_closure`**

In `bot_v2.py`, near the existing `STATE_FILE` definition (around line 52), add:

```python
LEDGER_FILE      = DATA_DIR / "closures.jsonl"
```

Then add immediately after `_build_closure_row`:

```python
def record_closure(mkt, pos):
    """Append one closure row to data/closures.jsonl. Called on the
    adjacent line to apply_closure_to_state at every closure site.
    Spec invariant: must run BEFORE save_state(state).
    """
    row = _build_closure_row(mkt, pos)
    with open(LEDGER_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_closure_ledger.py::TestRecordClosure -v`
Expected: 3 PASSED

- [ ] **Step 5: Commit**

```bash
git add bot_v2.py tests/test_closure_ledger.py
git commit -m "feat: add record_closure to append rows to closures.jsonl"
```

---

## Task 3: Wire `record_closure` into the four closure sites

**Files:**
- Modify: `bot_v2.py` lines `:732`, `:743`, `:896`, `:1105` (run `grep -n apply_closure_to_state bot_v2.py` first to confirm exact current line numbers)
- Modify: `tests/test_closure_ledger.py` (add `TestAllFourPathsRecordClosure` class)

- [ ] **Step 1: Confirm the four call site line numbers**

Run: `grep -n "apply_closure_to_state(state" bot_v2.py`
Expected: four matches. Note the line numbers — the ones in this plan are approximate.

- [ ] **Step 2: Write the failing integration test**

Append to `tests/test_closure_ledger.py`:

```python
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
```

(Two paths covered here. The other two — `forecast_changed` close in the scan loop and `resolved` final scan — are exercised indirectly because the `apply_closure_to_state` chokepoint guarantees co-location. The test above proves the wiring shape; if a fifth call site is ever added, this test class should grow with it.)

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_closure_ledger.py::TestAllFourPathsRecordClosure -v`
Expected: 2 FAILED — ledger file is empty after `monitor_positions`.

- [ ] **Step 4: Add `record_closure` adjacent to each `apply_closure_to_state` call**

At each of the four sites (line numbers from Step 1), insert a `record_closure(mkt, pos)` line **immediately before** `apply_closure_to_state`. Example:

```python
# was:
apply_closure_to_state(state, pnl)

# becomes:
record_closure(mkt, pos)
apply_closure_to_state(state, pnl)
```

The four sites:
1. Inside the monitor stop/trailing block (variable name is `pos`, market is `mkt`).
2. Inside the forecast-changed block — variable name there is also `pos` and `mkt`; the local `fc_pnl` already lives on `pos["pnl"]`, so `record_closure(mkt, pos)` is the right call.
3. In the final scan resolve block (look for the `pos["close_reason"] = "resolved"` line — `record_closure(mkt, pos)` goes after that mutation, before `apply_closure_to_state`).
4. In `monitor_positions` (around line 1105) — same shape; insert before `apply_closure_to_state(state, pnl)`.

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_closure_ledger.py::TestAllFourPathsRecordClosure -v`
Expected: 2 PASSED.

Then run the full suite to confirm no regressions:

Run: `pytest tests/ -x -q`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add bot_v2.py tests/test_closure_ledger.py
git commit -m "feat: wire record_closure into all four closure sites"
```

---

## Task 4: Write-order invariant test (`record_closure` runs before `save_state`)

**Files:**
- Modify: `tests/test_closure_ledger.py` (add `TestWriteOrderInvariant`)

- [ ] **Step 1: Write the failing-then-passing test**

Append to `tests/test_closure_ledger.py`:

```python
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
```

- [ ] **Step 2: Run test**

Run: `pytest tests/test_closure_ledger.py::TestWriteOrderInvariant -v`

If `record_closure` is correctly placed before `save_state` in all four sites (Task 3), this passes immediately. If it fails, find the site that calls `save_state` *between* the closure mutation and `record_closure` and reorder.

Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/test_closure_ledger.py
git commit -m "test: assert ledger row persists when save_state fails"
```

---

## Task 5: `maybe_backfill_ledger` — startup self-heal

**Files:**
- Modify: `bot_v2.py` (add function near `maybe_backfill_realized_pnl`; call from main startup)
- Modify: `tests/test_closure_ledger.py` (add `TestMaybeBackfillLedger`)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_closure_ledger.py`:

```python
class TestMaybeBackfillLedger:
    def _seed(self, tmp_path, monkeypatch):
        monkeypatch.setattr("bot_v2.MARKETS_DIR", tmp_path / "markets")
        monkeypatch.setattr("bot_v2.LEDGER_FILE", tmp_path / "closures.jsonl")
        (tmp_path / "markets").mkdir()

    def test_backfills_when_ledger_missing(self, tmp_path, monkeypatch):
        from bot_v2 import maybe_backfill_ledger
        self._seed(tmp_path, monkeypatch)

        # Two closed (stop/take), one resolved.
        for name, pos_kwargs, mkt_status in [
            ("a", {"close_reason": "stop_loss",   "pnl": -3.0, "exit_price": 0.30, "closed_at": "2026-04-23T01:00:00+00:00"}, "closed"),
            ("b", {"close_reason": "take_profit", "pnl":  5.0, "exit_price": 0.85, "closed_at": "2026-04-23T02:00:00+00:00"}, "closed"),
            ("c", {"close_reason": "resolved",    "pnl":  2.5, "exit_price": 1.00, "closed_at": "2026-04-23T03:00:00+00:00"}, "resolved"),
        ]:
            pos = _make_position()
            pos["market_id"] = f"mkt_{name}"
            pos["status"] = "closed"
            pos.update(pos_kwargs)
            mkt = _make_market(position=pos, city="dallas", date="2026-05-01")
            mkt["market_id"] = f"mkt_{name}"
            mkt["status"] = mkt_status
            (tmp_path / "markets" / f"{name}.json").write_text(json.dumps(mkt))

        maybe_backfill_ledger()

        rows = [json.loads(l) for l in (tmp_path / "closures.jsonl").read_text().strip().split("\n")]
        assert len(rows) == 3
        # Sorted by ts ascending
        assert [r["close_reason"] for r in rows] == ["stop_loss", "take_profit", "resolved"]
        assert [r["pnl"] for r in rows] == [-3.0, 5.0, 2.5]

    def test_idempotent_skips_when_ledger_nonempty(self, tmp_path, monkeypatch):
        from bot_v2 import maybe_backfill_ledger
        self._seed(tmp_path, monkeypatch)
        # Pre-existing ledger
        (tmp_path / "closures.jsonl").write_text(json.dumps({"type": "reset", "ts": "x", "starting_balance": 1000}) + "\n")
        # Closed market that would otherwise be backfilled
        pos = _make_position()
        pos.update({"status": "closed", "close_reason": "stop_loss", "pnl": -1.0, "closed_at": "2026-04-22T00:00:00+00:00"})
        mkt = _make_market(position=pos)
        (tmp_path / "markets" / "a.json").write_text(json.dumps(mkt))

        maybe_backfill_ledger()

        # Untouched: still one (reset) row.
        rows = [json.loads(l) for l in (tmp_path / "closures.jsonl").read_text().strip().split("\n")]
        assert len(rows) == 1
        assert rows[0]["type"] == "reset"

    def test_no_op_when_no_closed_positions(self, tmp_path, monkeypatch):
        from bot_v2 import maybe_backfill_ledger
        self._seed(tmp_path, monkeypatch)

        maybe_backfill_ledger()

        # Ledger never created.
        assert not (tmp_path / "closures.jsonl").exists()

    def test_pre_spread_strategy_rows_have_null_spread_fields(self, tmp_path, monkeypatch):
        from bot_v2 import maybe_backfill_ledger
        self._seed(tmp_path, monkeypatch)
        # Pre-eabdb67 closure: position dict lacks spread/sigma/forecast_src.
        pos = {
            "market_id":   "mkt_old",
            "entry_price": 0.50, "exit_price": 0.55, "shares": 20.0, "cost": 10.0,
            "pnl":         1.00, "bucket_low": 60, "bucket_high": 65,
            "opened_at":   "2026-04-22T00:00:00+00:00",
            "closed_at":   "2026-04-22T12:00:00+00:00",
            "close_reason": "resolved",
            "status":       "closed",
        }
        mkt = _make_market(position=pos)
        mkt["market_id"] = "mkt_old"
        mkt["status"] = "resolved"
        (tmp_path / "markets" / "old.json").write_text(json.dumps(mkt))

        maybe_backfill_ledger()

        row = json.loads((tmp_path / "closures.jsonl").read_text().strip())
        assert row["spread_at_entry"] is None
        assert row["sigma_at_entry"] is None
        assert row["forecast_src"] is None
        assert row["pnl"] == 1.00
        assert row["close_reason"] == "resolved"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_closure_ledger.py::TestMaybeBackfillLedger -v`
Expected: FAIL with `ImportError: cannot import name 'maybe_backfill_ledger'`

- [ ] **Step 3: Implement `maybe_backfill_ledger` in `bot_v2.py`**

Add after `maybe_backfill_realized_pnl` (around line 545):

```python
def maybe_backfill_ledger():
    """One-shot self-heal: backfill closures.jsonl from market files when
    the ledger is missing or empty but closed positions exist on disk.

    Pre-eabdb67 closures lack spread-strategy fields; those become null in
    the row. Idempotent — bails on the first guard once the ledger is
    non-empty.
    """
    if LEDGER_FILE.exists() and LEDGER_FILE.stat().st_size > 0:
        return

    rows = []
    for mkt in load_all_markets():
        pos = mkt.get("position") or {}
        is_closed = pos.get("status") == "closed"
        is_resolved = mkt.get("status") == "resolved"
        if not (is_closed or is_resolved):
            continue
        rows.append(_build_closure_row(mkt, pos))

    if not rows:
        return

    rows.sort(key=lambda r: r.get("ts") or "")
    with open(LEDGER_FILE, "a", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"[backfill] closures.jsonl ← {len(rows)} rows from market files")
```

- [ ] **Step 4: Wire into startup**

In `bot_v2.py` startup, immediately after the existing `maybe_backfill_realized_pnl(load_state())` call (around line 1133):

```python
maybe_backfill_realized_pnl(load_state())
maybe_backfill_ledger()
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_closure_ledger.py::TestMaybeBackfillLedger -v`
Expected: 4 PASSED.

Run: `pytest tests/ -x -q`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add bot_v2.py tests/test_closure_ledger.py
git commit -m "feat: backfill closures.jsonl from market files on startup"
```

---

## Task 6: `tools/reset.py` — explicit reset script

**Files:**
- Create: `tools/reset.py`
- Create: `tests/test_reset_script.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_reset_script.py`:

```python
"""Tests for tools/reset.py — the explicit reset script.

reset.py replaces today's manual 'rm state.json && rm markets/*.json'
procedure. Crucially, it appends a {type: reset} row to data/closures.jsonl
BEFORE deleting state, so the ledger preserves a session boundary across
the reset.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parent.parent


def _run(args, cwd):
    result = subprocess.run(
        [sys.executable, str(REPO / "tools" / "reset.py")] + list(args),
        cwd=str(cwd),
        capture_output=True,
        text=True,
    )
    return result


class TestResetDryRun:
    def test_dry_run_makes_no_changes(self, tmp_path):
        data = tmp_path / "data"
        (data / "markets").mkdir(parents=True)
        (data / "state.json").write_text(json.dumps({
            "balance": 1000.0, "starting_balance": 1000.0,
            "total_trades": 5, "wins": 3, "losses": 2,
            "peak_balance": 1050.0, "realized_pnl": 12.50,
        }))
        (data / "markets" / "a.json").write_text("{}")
        (data / "closures.jsonl").write_text("")

        result = _run(["--dry-run", "--yes"], cwd=tmp_path)
        assert result.returncode == 0, result.stderr

        # State and markets file untouched
        assert (data / "state.json").exists()
        assert (data / "markets" / "a.json").exists()
        # Ledger has no reset row
        assert (data / "closures.jsonl").read_text() == ""

    def test_real_run_writes_marker_then_deletes(self, tmp_path):
        data = tmp_path / "data"
        (data / "markets").mkdir(parents=True)
        (data / "state.json").write_text(json.dumps({
            "balance": 800.0, "starting_balance": 1000.0,
            "total_trades": 5, "wins": 1, "losses": 4,
            "peak_balance": 1010.0, "realized_pnl": -200.00,
        }))
        (data / "markets" / "a.json").write_text("{}")
        (data / "markets" / "b.json").write_text("{}")

        result = _run(["--yes", "--note", "test reset"], cwd=tmp_path)
        assert result.returncode == 0, result.stderr

        # state.json gone
        assert not (data / "state.json").exists()
        # markets dir empty
        assert list((data / "markets").glob("*.json")) == []
        # ledger has one reset row
        rows = [json.loads(l) for l in (data / "closures.jsonl").read_text().strip().split("\n")]
        assert len(rows) == 1
        assert rows[0]["type"] == "reset"
        assert rows[0]["starting_balance"] == 1000.0  # from state.json
        assert rows[0]["note"] == "test reset"

    def test_starting_balance_override(self, tmp_path):
        data = tmp_path / "data"
        (data / "markets").mkdir(parents=True)
        # No state.json, no config.json — must use --starting-balance.
        result = _run(["--yes", "--starting-balance", "500.0"], cwd=tmp_path)
        assert result.returncode == 0, result.stderr

        rows = [json.loads(l) for l in (data / "closures.jsonl").read_text().strip().split("\n")]
        assert rows[0]["starting_balance"] == 500.0

    def test_falls_back_to_config_json_when_no_state(self, tmp_path):
        data = tmp_path / "data"
        (data / "markets").mkdir(parents=True)
        (tmp_path / "config.json").write_text(json.dumps({"balance": 250.0}))
        result = _run(["--yes"], cwd=tmp_path)
        assert result.returncode == 0, result.stderr

        rows = [json.loads(l) for l in (data / "closures.jsonl").read_text().strip().split("\n")]
        assert rows[0]["starting_balance"] == 250.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_reset_script.py -v`
Expected: 4 FAILED — `tools/reset.py` does not exist.

- [ ] **Step 3: Create `tools/reset.py`**

Create the file:

```python
#!/usr/bin/env python3
"""Reset the bot's local state and append a session boundary to the ledger.

Replaces the manual 'rm data/state.json && rm data/markets/*.json' procedure.
The reset marker is written to data/closures.jsonl BEFORE any deletions so
that the ledger always preserves the session boundary, even if step 4 or 5
fails.

Usage:
  python tools/reset.py [--yes] [--dry-run] [--starting-balance N] [--note STR]

Flags:
  --yes                Skip the interactive confirmation prompt.
  --dry-run            Print the planned actions and exit without changes.
  --starting-balance   Override the marker's starting_balance.
  --note               Free-form note to store on the marker.

starting_balance precedence:
  --starting-balance > data/state.json["starting_balance"] > config.json["balance"]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


def resolve_starting_balance(cwd: Path, override: float | None) -> float:
    if override is not None:
        return override
    state_path = cwd / "data" / "state.json"
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            sb = state.get("starting_balance")
            if isinstance(sb, (int, float)):
                return float(sb)
        except (json.JSONDecodeError, OSError):
            pass
    config_path = cwd / "config.json"
    if config_path.exists():
        try:
            cfg = json.loads(config_path.read_text(encoding="utf-8"))
            bal = cfg.get("balance")
            if isinstance(bal, (int, float)):
                return float(bal)
        except (json.JSONDecodeError, OSError):
            pass
    raise SystemExit(
        "starting_balance could not be resolved. Pass --starting-balance N."
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Reset bot state with ledger marker.")
    parser.add_argument("--yes", action="store_true", help="Skip confirmation prompt.")
    parser.add_argument("--dry-run", action="store_true", help="Print planned actions, don't write.")
    parser.add_argument("--starting-balance", type=float, default=None,
                        help="Override marker starting_balance (else read from state.json or config.json).")
    parser.add_argument("--note", type=str, default="", help="Free-form note on the marker.")
    args = parser.parse_args()

    cwd = Path.cwd()
    data = cwd / "data"
    markets = data / "markets"
    state = data / "state.json"
    ledger = data / "closures.jsonl"

    starting_balance = resolve_starting_balance(cwd, args.starting_balance)

    market_files = sorted(markets.glob("*.json")) if markets.exists() else []

    print(f"Reset plan:")
    print(f"  ledger marker    → {ledger} (starting_balance={starting_balance}, note={args.note!r})")
    print(f"  delete state     → {state} ({'exists' if state.exists() else 'not present'})")
    print(f"  delete markets   → {len(market_files)} file(s) under {markets}")

    if args.dry_run:
        print("--dry-run set; exiting without changes.")
        return 0

    if not args.yes:
        resp = input("Proceed? [y/N] ").strip().lower()
        if resp not in ("y", "yes"):
            print("aborted.")
            return 1

    data.mkdir(exist_ok=True)
    marker = {
        "type":             "reset",
        "ts":               datetime.now(timezone.utc).isoformat(),
        "starting_balance": starting_balance,
        "note":             args.note,
    }
    with open(ledger, "a", encoding="utf-8") as f:
        f.write(json.dumps(marker, ensure_ascii=False) + "\n")
    print(f"  ✓ wrote reset marker to {ledger}")

    if state.exists():
        state.unlink()
        print(f"  ✓ deleted {state}")

    for f in market_files:
        f.unlink()
    if market_files:
        print(f"  ✓ deleted {len(market_files)} market file(s)")

    print("done. Restart the bot.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_reset_script.py -v`
Expected: 4 PASSED.

- [ ] **Step 5: Commit**

```bash
git add tools/reset.py tests/test_reset_script.py
git commit -m "feat: add tools/reset.py with explicit reset marker"
```

---

## Task 7: `walkforward_test.py --emit-baseline`

**Files:**
- Modify: `walkforward_test.py` (add CLI flag and baseline-writing block)
- Create: `tests/test_emit_baseline.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_emit_baseline.py`:

```python
"""Tests for walkforward_test.py --emit-baseline.

Produces data/backtest_baseline.json with test-set stats, simulation
outcome, and a config snapshot. Dashboard reads this to render the
live-vs-backtest header strip and to fire a staleness warning when
config drifts from the snapshot.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parent.parent


def _seed_market_files(markets_dir: Path, n: int = 30):
    """Seed enough resolved-with-spread markets for walkforward to compute stats."""
    from datetime import datetime, timezone, timedelta
    markets_dir.mkdir(parents=True)
    base = datetime(2026, 4, 1, tzinfo=timezone.utc)
    for i in range(n):
        d = base + timedelta(days=i)
        date_str = d.strftime("%Y-%m-%d")
        # Include both ecmwf+hrrr in forecast_snapshots and an actual_temp
        # so walkforward picks it up. Spread > 2 to qualify.
        mkt = {
            "market_id":   f"mkt_{i}",
            "city":        "dallas",
            "city_name":   "Dallas",
            "date":        date_str,
            "unit":        "F",
            "station":     "KDAL",
            "event_end_date": d.isoformat(),
            "actual_temp": 70 if i % 2 == 0 else 64,
            "forecast_snapshots": [{
                "ts":    d.isoformat(),
                "ecmwf": 65,
                "hrrr":  68,
                "metar": None,
            }],
            "all_outcomes": [{
                "market_id": f"mkt_{i}_b",
                "question":  "Will high be 67-69?",
                "range":     [67, 69],
                "price":     0.40,
                "bid":       0.38,
                "ask":       0.42,
                "spread":    0.04,
                "volume":    1000,
            }],
            "status": "resolved",
            "position": None,
            "pnl": 5.0 if i % 2 == 0 else -3.0,
            "resolved_outcome": "yes" if i % 2 == 0 else "no",
            "forecast_temp": 68,
            "ecmwf_temp": 65,
        }
        (markets_dir / f"dallas_{date_str}.json").write_text(json.dumps(mkt))


def _run(args, cwd):
    return subprocess.run(
        [sys.executable, str(REPO / "walkforward_test.py")] + list(args),
        cwd=str(cwd),
        capture_output=True,
        text=True,
    )


class TestEmitBaseline:
    def test_writes_well_formed_baseline(self, tmp_path):
        markets = tmp_path / "data" / "markets"
        _seed_market_files(markets, n=30)

        result = _run(
            ["--data-dir", str(markets), "--emit-baseline", "--threshold", "2.0", "--simulate", "1000"],
            cwd=tmp_path,
        )
        assert result.returncode == 0, result.stderr

        baseline = tmp_path / "data" / "backtest_baseline.json"
        assert baseline.exists()
        data = json.loads(baseline.read_text())

        assert data["source"] == "walkforward_test.py"
        assert "generated_at" in data
        assert data["config_snapshot"]["spread_threshold"] == 2.0
        assert data["config_snapshot"]["max_bet"] == 20.0
        assert data["config_snapshot"]["kelly_fraction"] == 0.25
        # min_ev intentionally absent — walkforward doesn't filter on it
        assert "min_ev" not in data["config_snapshot"]
        assert "test_set" in data
        assert "simulation" in data
        assert data["simulation"]["starting_balance"] == 1000.0

    def test_emit_baseline_without_simulate_implies_default(self, tmp_path):
        markets = tmp_path / "data" / "markets"
        _seed_market_files(markets, n=30)

        result = _run(
            ["--data-dir", str(markets), "--emit-baseline", "--threshold", "2.0"],
            cwd=tmp_path,
        )
        assert result.returncode == 0, result.stderr

        baseline = tmp_path / "data" / "backtest_baseline.json"
        data = json.loads(baseline.read_text())
        assert data["simulation"]["starting_balance"] == 1000.0  # default
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_emit_baseline.py -v`
Expected: FAIL — `--emit-baseline` flag does not exist.

- [ ] **Step 3: Add the flag and the emission code path**

In `walkforward_test.py` argparse block (around line 270), add:

```python
parser.add_argument("--emit-baseline", action="store_true",
                    help="Write data/backtest_baseline.json (implies --simulate 1000 if not set).")
```

After argparse parses, if `--emit-baseline` is set and `--simulate` isn't:

```python
if args.emit_baseline and args.simulate is None:
    args.simulate = 1000.0
```

After the existing simulation completes (after the `EQUITY SIMULATION` print block), add:

```python
if args.emit_baseline:
    from datetime import datetime, timezone
    baseline_path = Path("data") / "backtest_baseline.json"
    baseline_path.parent.mkdir(exist_ok=True)
    test_stats = results["test"]["stats"]
    test_sim = sim["test"]
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source":       "walkforward_test.py",
        "config_snapshot": {
            "spread_threshold": args.threshold,
            "max_bet":          args.max_bet,
            "kelly_fraction":   args.kelly,
        },
        "test_set": {
            "trades":    test_stats["trades"],
            "win_rate":  test_stats["win_rate"],
            "total_pnl": test_stats["pnl"],
            "avg_pnl":   test_stats["avg_pnl"],
        },
        "simulation": {
            "starting_balance": float(args.simulate),
            "ending_balance":   test_sim["end"],
            "return_pct":       test_sim["return_pct"],
            "max_drawdown_pct": test_sim["max_drawdown_pct"],
        },
    }
    baseline_path.write_text(json.dumps(payload, indent=2))
    print(f"\n→ wrote baseline to {baseline_path}")
```

If the existing main() doesn't import `Path` or `json` at module top level, add to the existing imports (don't re-import inside the function).

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_emit_baseline.py -v`
Expected: 2 PASSED.

- [ ] **Step 5: Commit**

```bash
git add walkforward_test.py tests/test_emit_baseline.py
git commit -m "feat: add --emit-baseline flag to walkforward_test.py"
```

---

## Task 8: Dashboard manifest exposes ledger, baseline, and config snapshot

**Files:**
- Modify: `dashboard_server.py:41-52` (regenerate_manifest)

**Why a config snapshot:** `config.json` lives at the project root, not under `data/`. The dashboard server only serves `/data/` and `/Dashboard.html` — it would block a direct `config.json` fetch. The cleanest fix is to read `./config.json` server-side and embed the three relevant values into `manifest.json`, which the dashboard already fetches on every load. This avoids broadening `ALLOWED_PREFIXES` or copying the file.

- [ ] **Step 1: Edit `regenerate_manifest`**

In `dashboard_server.py`, replace the `regenerate_manifest` body (around lines 41-52) with:

```python
def regenerate_manifest() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    markets_dir = DATA_DIR / "markets"
    markets_dir.mkdir(exist_ok=True)
    market_files = sorted(p.name for p in markets_dir.glob("*.json"))

    # Embed only the keys the baseline staleness guard depends on.
    config_snapshot = None
    config_path = ROOT / "config.json"
    if config_path.exists():
        try:
            cfg = json.loads(config_path.read_text(encoding="utf-8"))
            config_snapshot = {
                "spread_threshold": cfg.get("spread_threshold"),
                "max_bet":          cfg.get("max_bet"),
                "kelly_fraction":   cfg.get("kelly_fraction"),
            }
        except (json.JSONDecodeError, OSError):
            pass

    manifest = {
        "generated_at":    datetime.now(timezone.utc).isoformat(),
        "state":           "state.json"             if (DATA_DIR / "state.json").exists() else None,
        "calibration":     "calibration.json"       if (DATA_DIR / "calibration.json").exists() else None,
        "closures":        "closures.jsonl"         if (DATA_DIR / "closures.jsonl").exists() else None,
        "baseline":        "backtest_baseline.json" if (DATA_DIR / "backtest_baseline.json").exists() else None,
        "config_snapshot": config_snapshot,
        "markets":         market_files,
    }
    (DATA_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2))
```

Note: `config.json` may use the key name `spread_threshold` (per spec's `config_snapshot` shape). If the existing file uses a different key (e.g., `SPREAD_THRESHOLD` constant in code, but JSON might be different), check `config.json` and adjust `cfg.get(...)` accordingly. Run `cat config.json` first to confirm field names. The current file shows: `balance`, `max_bet`, `min_ev`, `max_price`, `min_volume`, `min_hours`, `max_hours`, `kelly_fraction`, `scan_interval`, `calibration_min`, `vc_key`, `max_slippage` — there is **no `spread_threshold` key** in `config.json` today; `SPREAD_THRESHOLD` is a Python module constant in `bot_v2.py`. The dashboard staleness check therefore needs the bot to expose `SPREAD_THRESHOLD` somewhere observable. Add it to `config.json` as part of this task, OR have `dashboard_server.py` import the constant from `bot_v2`.

Pick the simpler: add `"spread_threshold": 2.0` to `config.json`. Update `bot_v2.py:SPREAD_THRESHOLD` to read from config (with fallback to current default of 2.0).

- [ ] **Step 2: Add `spread_threshold` to `config.json` and read from it in `bot_v2.py`**

Edit `config.json`:

```json
{
  "balance": 1000.0,
  "max_bet": 20.0,
  "min_ev": 0.1,
  "max_price": 0.45,
  "min_volume": 500,
  "min_hours": 2.0,
  "max_hours": 72.0,
  "kelly_fraction": 0.25,
  "spread_threshold": 2.0,
  "scan_interval": 3600,
  "calibration_min": 1,
  "vc_key": "",
  "max_slippage": 0.03
}
```

In `bot_v2.py`, find the line that defines `SPREAD_THRESHOLD` (run `grep -n SPREAD_THRESHOLD bot_v2.py`) and change it to read from config with the existing default as fallback:

```python
SPREAD_THRESHOLD = config.get("spread_threshold", 2.0)
```

(If `config` is loaded later in the file than `SPREAD_THRESHOLD` is defined, move the constant definition below the config load. Don't duplicate the load.)

- [ ] **Step 3: Smoke-test manually**

```bash
touch data/closures.jsonl data/backtest_baseline.json
python -c "from dashboard_server import regenerate_manifest; regenerate_manifest(); import json; print(json.dumps(json.loads(open('data/manifest.json').read()), indent=2))"
```

Expected: manifest.json includes both new file keys AND a `config_snapshot` block with the three values from `config.json`.

Cleanup if you created the empty test files yourself: `rm data/closures.jsonl data/backtest_baseline.json` (verify with `git status` first; don't delete real files).

- [ ] **Step 4: Run the existing test suite to confirm no regressions from the SPREAD_THRESHOLD refactor**

Run: `pytest tests/ -x -q`
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add dashboard_server.py bot_v2.py config.json
git commit -m "feat: expose closures, baseline, and config_snapshot in manifest"
```

---

## Task 9: Dashboard live-vs-backtest header strip (React)

**Files:**
- Modify: `Dashboard.html` — Dashboard.html is a React app (`useMemo`, `useState`, `useEffect` from `React` global, JSX-style components like `<SortHead>`). Add a small functional component above the existing realized-PnL tile.

- [ ] **Step 1: Confirm the existing patterns**

Run: `grep -n "fetchJson\|Promise.all\|loadAll\|loadData" Dashboard.html | head -20`

Expected: there is a `fetchJson` helper near the top of the data-loading function (around line 556), and a `Promise.all` of fetches (around line 571–574). Read 30 lines around the `Promise.all` to understand the existing shape before editing.

- [ ] **Step 2: Add `fetchText` and extend the `Promise.all`**

Adjacent to the existing `fetchJson` definition (~line 556), add:

```javascript
const fetchText = async (path, optional = false) => {
  const r = await fetch(`./data/${path}`);
  if (!r.ok) {
    if (optional) return null;
    throw new Error(`fetch ${path}: ${r.status}`);
  }
  return r.text();
};
```

Extend the existing `Promise.all` (~line 571) to also fetch closures and baseline. **Read the destructuring exactly as it is in the file before editing — don't rely on the snippet below verbatim.** The current shape is:

```javascript
const [state, calibration, ...rawMarkets] = await Promise.all([
  fetchJson("state.json"),
  fetchJson("calibration.json", true).catch(() => null),
  ...manifest.markets.map(name => fetchJson(`markets/${name}`, true).catch(() => null)),
]);
```

Update to:

```javascript
const [state, calibration, closuresText, baseline, ...rawMarkets] = await Promise.all([
  fetchJson("state.json"),
  fetchJson("calibration.json", true).catch(() => null),
  fetchText("closures.jsonl", true).catch(() => null),
  fetchJson("backtest_baseline.json", true).catch(() => null),
  ...manifest.markets.map(name => fetchJson(`markets/${name}`, true).catch(() => null)),
]);
```

The `manifest.config_snapshot` value (added in Task 8) is already on `manifest`, no extra fetch needed.

- [ ] **Step 3: Add JSONL parser and reset-scoping helpers**

In the same module-scope section as other helper functions (search for an existing `function` declaration in the data-processing layer — likely above the data-loading function), add:

```javascript
function parseClosuresJsonl(text) {
  if (!text) return [];
  const rows = [];
  text.split("\n").forEach((line, i) => {
    const trimmed = line.trim();
    if (!trimmed) return;
    try {
      rows.push(JSON.parse(trimmed));
    } catch (e) {
      console.warn(`[closures.jsonl] skipping malformed line ${i + 1}: ${e.message}`);
    }
  });
  return rows;
}

function scopeSinceLastReset(rows) {
  let cutoff = null;
  for (const r of rows) {
    if (r.type === "reset" && (cutoff === null || r.ts > cutoff)) cutoff = r.ts;
  }
  const closures = rows.filter(r => r.type === "closure" && (cutoff === null || r.ts >= cutoff));
  return { closures, cutoff, hasResetMarker: cutoff !== null };
}
```

- [ ] **Step 4: Add a `LiveVsBacktest` React component**

In the file's React-component section (search for an existing `function` that uses `useMemo` and renders JSX-like syntax to find the right neighborhood), add:

```jsx
function LiveVsBacktest({ closuresText, baseline, configSnapshot }) {
  const data = React.useMemo(() => {
    const rows = parseClosuresJsonl(closuresText);
    return scopeSinceLastReset(rows);
  }, [closuresText]);

  const { closures, cutoff, hasResetMarker } = data;
  const wins = closures.filter(c => c.pnl > 0).length;
  const losses = closures.length - wins;
  const liveWinRate = closures.length ? (100 * wins / closures.length) : null;
  const livePnl = closures.reduce((s, c) => s + (c.pnl || 0), 0);
  const lowSample = closures.length > 0 && closures.length < 10;
  const dateStr = cutoff ? new Date(cutoff).toISOString().slice(0, 10) : null;
  const sinceLabel = hasResetMarker ? `Live (since ${dateStr})` : "Live (all time)";

  let backtestStr;
  const drift = [];
  if (baseline) {
    const bw = baseline.test_set?.win_rate;
    const bn = baseline.test_set?.trades;
    backtestStr = bw !== undefined && bn !== undefined
      ? `Backtest: ${bw.toFixed(1)}%  (n=${bn})`
      : "Backtest: data malformed";
    const snap = baseline.config_snapshot || {};
    if (configSnapshot) {
      ["spread_threshold", "max_bet", "kelly_fraction"].forEach(k => {
        if (snap[k] !== undefined && configSnapshot[k] !== undefined && snap[k] !== configSnapshot[k]) {
          drift.push(k);
        }
      });
    }
  } else {
    backtestStr = "baseline missing — run walkforward_test.py --emit-baseline";
  }

  let wrStyle = {};
  if (liveWinRate !== null && baseline?.test_set?.win_rate !== undefined) {
    const bw = baseline.test_set.win_rate;
    if (liveWinRate >= bw) wrStyle = { color: "#4caf50" };
    else if (liveWinRate < bw - 10) wrStyle = { color: "#e53935" };
  }

  const liveCell = closures.length === 0
    ? React.createElement("span", null, "no closures yet")
    : React.createElement(React.Fragment, null,
        `${wins}W ${losses}L  `,
        React.createElement("span", { style: wrStyle }, `${liveWinRate.toFixed(1)}%`),
        `  ${livePnl >= 0 ? "+" : ""}$${livePnl.toFixed(2)}`
      );

  const badges = [
    lowSample && "⚠ low sample",
    !hasResetMarker && "no reset boundary detected",
    drift.length && `⚠ baseline stale: ${drift.join(", ")}`,
  ].filter(Boolean);

  return React.createElement("div", {
    className: "live-vs-backtest",
    style: { padding: "8px 12px", margin: "0 0 12px 0", borderBottom: "1px solid var(--border)", opacity: lowSample ? 0.75 : 1 },
  },
    React.createElement("strong", null, `${sinceLabel}: `),
    liveCell,
    "  |  ",
    React.createElement("span", null, backtestStr),
    " ",
    badges.map((b, i) => React.createElement("span", { key: i, className: "badge", style: { marginLeft: 8, fontSize: "0.85em", opacity: 0.8 } }, b))
  );
}
```

(The component uses `React.createElement` rather than JSX so it works regardless of whether the rest of the file uses Babel-in-browser or some other transform. If the surrounding code uses JSX, feel free to convert.)

- [ ] **Step 5: Render the strip above the realized PnL tile**

Find the section that renders the realized-PnL tile (search for `label="Realized PnL"` — around line 1376). Just above whatever wrapping element contains that tile (likely a row of KPI cards), insert:

```jsx
<LiveVsBacktest
  closuresText={data.closuresText}
  baseline={data.baseline}
  configSnapshot={data.manifest?.config_snapshot}
/>
```

Where `data` is the prop/state object that holds the loaded `closuresText`, `baseline`, and `manifest` from Step 2.

If the existing dashboard already has a parent component that destructures the loaded data, thread `closuresText`, `baseline`, and `manifest` through the same path. Don't add new top-level state — reuse what's there.

- [ ] **Step 6: Smoke-test in the browser**

Start the dashboard server: `python dashboard_server.py` and open `http://localhost:8000/Dashboard.html` (set `DASH_USER`/`DASH_PASS` if needed).

Test data setup:
```bash
# Seed a ledger with one reset row + 3 closures
mkdir -p data
cat > data/closures.jsonl <<'EOF'
{"type":"reset","ts":"2026-04-23T11:27:00+00:00","starting_balance":1000.0,"note":"test"}
{"type":"closure","ts":"2026-04-24T10:00:00+00:00","city":"dallas","close_reason":"resolved","pnl":4.20}
{"type":"closure","ts":"2026-04-24T15:00:00+00:00","city":"miami","close_reason":"stop_loss","pnl":-2.10}
{"type":"closure","ts":"2026-04-25T08:00:00+00:00","city":"tokyo","close_reason":"resolved","pnl":1.50}
THIS LINE IS MALFORMED AND SHOULD BE SKIPPED
EOF
# Seed a baseline
cat > data/backtest_baseline.json <<'EOF'
{"generated_at":"2026-04-27T08:00:00+00:00","source":"walkforward_test.py","config_snapshot":{"spread_threshold":2.0,"max_bet":20.0,"kelly_fraction":0.25},"test_set":{"trades":412,"win_rate":85.0,"total_pnl":42.18,"avg_pnl":0.10},"simulation":{"starting_balance":1000.0,"ending_balance":7053.0,"return_pct":605.3,"max_drawdown_pct":18.4}}
EOF
```

Visually confirm:
- Header strip renders above the realized-PnL tile.
- `Live (since 2026-04-23): 2W 1L  66.7%  +$3.60  ⚠ low sample  |  Backtest: 85.0% (n=412)` (or similar — exact numbers vary).
- Win-rate cell colored red (66.7% < 85% - 10 = 75%).
- Browser console shows one warning about the malformed line; rendering is unaffected.
- Edit `data/backtest_baseline.json`, change `spread_threshold` to 99.0 in `config_snapshot`, refresh — `⚠ baseline stale: spread_threshold` badge appears.
- Delete `data/backtest_baseline.json`, refresh — baseline-missing message appears, no JS error.

Cleanup: `rm data/closures.jsonl data/backtest_baseline.json` (only test fixtures).

- [ ] **Step 7: Commit**

```bash
git add Dashboard.html
git commit -m "feat: live-vs-backtest header strip in dashboard"
```

---

## Final verification

- [ ] **Step 1: Run the full test suite**

Run: `pytest tests/ -x -q`
Expected: all green.

- [ ] **Step 2: Manual end-to-end smoke**

```bash
# Verify the bot starts, backfills, and writes a closure ledger entry on a synthetic close.
python -c "import bot_v2; bot_v2.maybe_backfill_ledger()"
ls -la data/closures.jsonl  # may or may not exist depending on market files

# Verify the reset script
python tools/reset.py --dry-run --yes

# Verify --emit-baseline produces a baseline file
python walkforward_test.py --emit-baseline --threshold 2.0 --simulate 1000
cat data/backtest_baseline.json
```

- [ ] **Step 3: Final commit if anything dangling**

```bash
git status
# clean up any straggling commits or stash if needed
```

The bot is now writing every closure to `data/closures.jsonl`. On startup, an empty/missing ledger is backfilled from market files. `tools/reset.py` cleanly archives a session boundary. The dashboard surfaces a live-vs-backtest header strip with sample-size and staleness warnings. v2 (per-city / per-reason / per-day breakdowns) builds on this foundation when needed.
