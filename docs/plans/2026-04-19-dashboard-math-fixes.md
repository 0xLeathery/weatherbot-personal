# Dashboard Math Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix every incorrect or misleading number on `Dashboard.html` (unrealized PnL, win rate, total trades, balance vs equity) under strict TDD — every production change is preceded by a test that fails for the right reason, then passes.

**Architecture:** Two-sided fix.

Bot (`bot_v2.py`) records the price of the bot's own bucket in every market snapshot (currently only `top_bucket` / `top_price` are stored — the top / most-probable bucket, not the bot's). Bot also increments `state.wins` / `state.losses` on stop-loss closures (currently only the auto-resolution branch does). A one-shot `tools/backfill_state.py` retro-fixes the existing `state.json`.

Dashboard math is extracted from inline React into pure helpers in `web/dashboard_math.js` so it can be exercised with real fixtures by Node's built-in test runner (`node:test`). `Dashboard.html` loads the helper file as a plain `<script>` before the Babel block, so the helpers are globals the React code consumes. The KPI strip is resplit into Cash / Equity with unambiguous labels.

**Tech Stack:**
- Python 3.11 + pytest (bot tests)
- Node's built-in `node:test` + `node:assert/strict` (dashboard math tests — no npm deps)
- Vanilla React via CDN + Babel standalone (dashboard runtime, unchanged)
- JSON files as bot↔dashboard interface

**Evidence underlying each fix** (from audit on 2026-04-19):
- 15 / 15 open positions have `last.top_bucket ≠ position bucket`. Dashboard "Unrealized" column is meaningless for every row.
- `state.wins = 0`, `state.losses = 0`, but `data/markets/` contains 4 stop-loss closures. Win-rate KPI stuck at "—" while source/horizon cards compute `pnl>0 / n` = 0 % independently.
- `state.balance = $680.93`, equity curve ends at `$980.93`. Gap = `$300` of reserved cost. Both are "right" for different models; the UI labels neither.

---

## File Structure

**New test infrastructure:**
- Create: `tests/__init__.py` (empty)
- Create: `tests/conftest.py` — fixture loader for market/state JSON stubs
- Create: `tests/test_snapshot.py` — Task 2, Task 3
- Create: `tests/test_stop_loss_state.py` — Task 4
- Create: `tests/test_backfill_state.py` — Task 6
- Create: `tests/dashboard_math.test.mjs` — Tasks 8, 9, 10, 11, 12
- Create: `pytest.ini` — set test paths, no plugins
- Create: `requirements-dev.txt` — `pytest>=7` only

**New dashboard math module:**
- Create: `web/dashboard_math.js` — pure helper functions exported as `window.DashboardMath`
- Modify: `Dashboard.html` — add `<script src="web/dashboard_math.js"></script>` before the Babel block; replace inline math with `DashboardMath.*` calls

**Production code changes:**
- Modify: `bot_v2.py:531-537` — snapshot writer now includes `position_price`
- Modify: `bot_v2.py:548-568` — stop-loss branch increments `state.wins` / `state.losses`
- Create: `tools/backfill_state.py` — one-shot backfill from existing `data/markets/`
- Modify: `Dashboard.html` — KPI strip split into Cash + Equity; Open table shows `·stale` marker; Equity curve card tagged "realized only"

---

## Task 1: Bootstrap the test infrastructure

No production code here — just the minimum harness so RED steps actually run.

**Files:**
- Create: `requirements-dev.txt`
- Create: `pytest.ini`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`

- [ ] **Step 1: Create `requirements-dev.txt`**

```
pytest>=7
```

- [ ] **Step 2: Create `pytest.ini`**

```
[pytest]
testpaths = tests
python_files = test_*.py
```

- [ ] **Step 3: Create `tests/__init__.py`**

Empty file.

- [ ] **Step 4: Create `tests/conftest.py`**

```python
"""Shared fixtures for bot tests."""
from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture
def tmp_data_dir(tmp_path: Path) -> Path:
    """A scratch data/ tree with markets/ and an empty state.json."""
    d = tmp_path / "data"
    (d / "markets").mkdir(parents=True)
    (d / "state.json").write_text(json.dumps({
        "balance": 1000.0,
        "starting_balance": 1000.0,
        "total_trades": 0,
        "wins": 0,
        "losses": 0,
        "peak_balance": 1000.0,
    }, indent=2))
    return d


def _write_market(dir_: Path, name: str, market: dict) -> Path:
    p = dir_ / "markets" / f"{name}.json"
    p.write_text(json.dumps(market, indent=2))
    return p


@pytest.fixture
def write_market(tmp_data_dir: Path):
    def _writer(name: str, market: dict) -> Path:
        return _write_market(tmp_data_dir, name, market)
    return _writer
```

- [ ] **Step 5: Verify pytest discovers but finds no tests**

Run: `python3 -m pip install -r requirements-dev.txt && python3 -m pytest -q`
Expected: `no tests ran in 0.0Xs` (or `collected 0 items`). No errors.

- [ ] **Step 6: Verify Node's test runner is callable**

Run: `node --test --version-check 2>&1 | head -1 ; node --version`
Expected: Node >= 18 (any recent version has `node:test` built-in).

- [ ] **Step 7: Commit**

```bash
git add requirements-dev.txt pytest.ini tests/__init__.py tests/conftest.py
git commit -m "test: bootstrap pytest harness + shared market fixture"
```

---

## Task 2: Extract `compute_position_price` helper — RED

**Files:**
- Create: `tests/test_snapshot.py`

- [ ] **Step 1: Write the failing test**

```python
"""Tests for bot_v2 snapshot helpers."""
from __future__ import annotations

import pytest

from bot_v2 import compute_position_price


def test_returns_price_of_matching_market_id():
    outcomes = [
        {"market_id": "TOP", "price": 0.5, "range": [70, 71]},
        {"market_id": "BOT", "price": 0.12, "range": [68, 69]},
    ]
    position = {"market_id": "BOT"}
    assert compute_position_price(outcomes, position) == 0.12


def test_returns_none_when_no_position():
    outcomes = [{"market_id": "X", "price": 0.3}]
    assert compute_position_price(outcomes, None) is None


def test_returns_none_when_market_id_absent():
    outcomes = [{"market_id": "X", "price": 0.3}]
    assert compute_position_price(outcomes, {"market_id": "Y"}) is None


def test_returns_none_when_outcomes_empty():
    assert compute_position_price([], {"market_id": "X"}) is None
```

- [ ] **Step 2: Run the test and confirm it fails for the right reason**

Run: `python3 -m pytest tests/test_snapshot.py -q`
Expected: `ImportError: cannot import name 'compute_position_price' from 'bot_v2'` — feature missing, not a typo.

## Task 3: Implement `compute_position_price` and wire it into the snapshot writer — GREEN

**Files:**
- Modify: `bot_v2.py` (add helper near the top of snapshot-writing region; update `market_snap` construction)

- [ ] **Step 1: Add the helper function**

Add immediately above `def take_forecast_snapshot` (around `bot_v2.py:414`):

```python
def compute_position_price(outcomes, position):
    """Return the price of the bucket this position holds, or None.

    The snapshot writer uses this so the dashboard can compute unrealized
    PnL against the bot's actual bucket (not the top/most-probable bucket,
    which is what top_price records).
    """
    if not position:
        return None
    mid = position.get("market_id")
    if not mid:
        return None
    for o in outcomes or []:
        if o.get("market_id") == mid:
            return o.get("price")
    return None
```

- [ ] **Step 2: Run the Task-2 tests and confirm they pass**

Run: `python3 -m pytest tests/test_snapshot.py -q`
Expected: `4 passed`.

- [ ] **Step 3: Wire the helper into the `market_snap` dict**

Locate (around `bot_v2.py:531-537`):

```python
            # Market price snapshot
            top = max(outcomes, key=lambda x: x["price"]) if outcomes else None
            market_snap = {
                "ts":       snap.get("ts"),
                "top_bucket": f"{top['range'][0]}-{top['range'][1]}{unit_sym}" if top else None,
                "top_price":  top["price"] if top else None,
            }
            mkt["market_snapshots"].append(market_snap)
```

Replace with:

```python
            # Market price snapshot
            top = max(outcomes, key=lambda x: x["price"]) if outcomes else None
            market_snap = {
                "ts":             snap.get("ts"),
                "top_bucket":     f"{top['range'][0]}-{top['range'][1]}{unit_sym}" if top else None,
                "top_price":      top["price"] if top else None,
                "position_price": compute_position_price(outcomes, mkt.get("position")),
            }
            mkt["market_snapshots"].append(market_snap)
```

- [ ] **Step 4: Syntax-check and re-run the whole suite**

Run: `python3 -m py_compile bot_v2.py && python3 -m pytest -q`
Expected: `4 passed`.

- [ ] **Step 5: Commit**

```bash
git add tests/test_snapshot.py bot_v2.py
git commit -m "bot: record position_price in snapshots (TDD)

Snapshots previously only stored the top (most-probable) bucket's price.
The dashboard's unrealized-PnL column treated top_price as the bot's
current bucket price, which is wrong whenever the bot isn't holding
the top bucket — i.e., 15 of 15 current open positions.

compute_position_price() is extracted and unit-tested; the snapshot
writer now calls it so every snapshot has position_price alongside
top_price."
```

---

## Task 4: `apply_closure_to_state` helper for wins/losses — RED

**Files:**
- Create: `tests/test_stop_loss_state.py`

- [ ] **Step 1: Write the failing test**

```python
"""Tests for state-counter updates when a position closes."""
from __future__ import annotations

from bot_v2 import apply_closure_to_state


def _blank_state():
    return {"wins": 0, "losses": 0}


def test_profit_increments_wins():
    s = _blank_state()
    apply_closure_to_state(s, pnl=5.0)
    assert s == {"wins": 1, "losses": 0}


def test_loss_increments_losses():
    s = _blank_state()
    apply_closure_to_state(s, pnl=-3.0)
    assert s == {"wins": 0, "losses": 1}


def test_zero_pnl_counts_as_loss():
    # Stop-loss / trailing-stop at break-even is a flat trade; by bot
    # convention we count it as a loss so the KPI denominator matches
    # the "total resolved" count.
    s = _blank_state()
    apply_closure_to_state(s, pnl=0.0)
    assert s == {"wins": 0, "losses": 1}


def test_sequential_closures_accumulate():
    s = _blank_state()
    for pnl in (10.0, -2.0, 4.0, -5.0):
        apply_closure_to_state(s, pnl=pnl)
    assert s == {"wins": 2, "losses": 2}
```

- [ ] **Step 2: Run the test and confirm it fails**

Run: `python3 -m pytest tests/test_stop_loss_state.py -q`
Expected: `ImportError: cannot import name 'apply_closure_to_state' from 'bot_v2'`.

## Task 5: Implement `apply_closure_to_state` and call it from the stop-loss branch — GREEN

**Files:**
- Modify: `bot_v2.py`

- [ ] **Step 1: Add the helper** near `compute_position_price`:

```python
def apply_closure_to_state(state, pnl):
    """Increment wins/losses counters for a closed position.

    Used by both the stop-loss branch and the auto-resolution branch so
    every realized closure ends up in state.wins or state.losses.
    """
    if pnl is None:
        return
    if pnl > 0:
        state["wins"] = state.get("wins", 0) + 1
    else:
        state["losses"] = state.get("losses", 0) + 1
```

- [ ] **Step 2: Run the new tests to confirm green**

Run: `python3 -m pytest tests/test_stop_loss_state.py -q`
Expected: `4 passed`.

- [ ] **Step 3: Replace the stop-loss closure block to call the helper**

Locate (around `bot_v2.py:548-568`):

```python
                    # Check stop
                    if current_price <= stop:
                        pnl = round((current_price - entry) * pos["shares"], 2)
                        balance += pos["cost"] + pnl
                        pos["closed_at"]    = snap.get("ts")
                        pos["close_reason"] = "stop_loss" if current_price < entry else "trailing_stop"
                        pos["exit_price"]   = current_price
                        pos["pnl"]          = pnl
                        pos["status"]       = "closed"
                        closed += 1
```

Replace with:

```python
                    # Check stop
                    if current_price <= stop:
                        pnl = round((current_price - entry) * pos["shares"], 2)
                        balance += pos["cost"] + pnl
                        pos["closed_at"]    = snap.get("ts")
                        pos["close_reason"] = "stop_loss" if current_price < entry else "trailing_stop"
                        pos["exit_price"]   = current_price
                        pos["pnl"]          = pnl
                        pos["status"]       = "closed"
                        apply_closure_to_state(state, pnl)
                        closed += 1
```

- [ ] **Step 4: Also switch the auto-resolution branch to the helper (DRY)**

Locate (around `bot_v2.py:729-733`):

```python
        if won:
            state["wins"] += 1
        else:
            state["losses"] += 1
```

Replace with:

```python
        apply_closure_to_state(state, pnl)
```

- [ ] **Step 5: Run the full suite**

Run: `python3 -m pytest -q`
Expected: `8 passed`.

- [ ] **Step 6: Commit**

```bash
git add tests/test_stop_loss_state.py bot_v2.py
git commit -m "bot: count stop-loss closures in wins/losses (TDD)

apply_closure_to_state() is extracted and unit-tested; both the
stop-loss branch and the auto-resolution branch now call it, so every
closure updates wins/losses regardless of how it closed.

Zero-PnL closures count as losses (KPI denominator == total resolved).

Existing state.json still has wins=0/losses=0 from past stop-outs; the
next task backfills it."
```

---

## Task 6: `tools/backfill_state.py` — RED

**Files:**
- Create: `tests/test_backfill_state.py`

- [ ] **Step 1: Write the failing test**

```python
"""Tests for the one-shot state.json backfill utility."""
from __future__ import annotations

import json

from tools.backfill_state import recompute_state_counters


def _closed(pnl):
    return {"position": {"status": "closed", "pnl": pnl}, "resolved_outcome": None}


def _resolved(pnl, outcome):
    return {"position": {"status": "closed"}, "pnl": pnl, "resolved_outcome": outcome}


def _open():
    return {"position": {"status": "open"}, "resolved_outcome": None}


def test_mixed_closures_counted_correctly():
    markets = [
        _closed(-5.0),
        _closed(-3.0),
        _closed(12.0),
        _resolved(20.0, "win"),
        _resolved(-10.0, "loss"),
        _open(),        # ignored
    ]
    wins, losses = recompute_state_counters(markets)
    assert (wins, losses) == (2, 3)


def test_all_open_returns_zero():
    wins, losses = recompute_state_counters([_open(), _open()])
    assert (wins, losses) == (0, 0)


def test_none_pnl_ignored():
    # A malformed / mid-write market shouldn't crash the backfill.
    markets = [{"position": {"status": "closed", "pnl": None}, "resolved_outcome": None}]
    wins, losses = recompute_state_counters(markets)
    assert (wins, losses) == (0, 0)
```

- [ ] **Step 2: Run the test and confirm it fails**

Run: `python3 -m pytest tests/test_backfill_state.py -q`
Expected: `ModuleNotFoundError: No module named 'tools.backfill_state'`.

## Task 7: Implement `tools/backfill_state.py` — GREEN

**Files:**
- Create: `tools/backfill_state.py`
- Verify: `tools/__init__.py` exists; if not, create an empty one.

- [ ] **Step 1: Ensure `tools/` is a package**

Run: `test -f tools/__init__.py || touch tools/__init__.py`

- [ ] **Step 2: Write the module**

```python
#!/usr/bin/env python3
"""One-shot: recompute state.wins / state.losses from data/markets/.

Run after upgrading bot_v2.py so the dashboard's Win-rate KPI reflects
the full history rather than only closures recorded after the upgrade.

Usage:
    python3 tools/backfill_state.py            # writes data/state.json
    python3 tools/backfill_state.py --dry-run  # prints counts only
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def recompute_state_counters(markets):
    """Return (wins, losses) computed from an iterable of market dicts.

    A market counts toward the tally only if its position has closed
    (status != "open") and has a numeric pnl. Wins are pnl > 0; ties
    and losses are counted as losses.
    """
    wins = losses = 0
    for m in markets:
        pos = m.get("position") or {}
        if pos.get("status") == "open":
            continue
        pnl = m.get("pnl") if m.get("resolved_outcome") is not None else pos.get("pnl")
        if pnl is None:
            continue
        if pnl > 0:
            wins += 1
        else:
            losses += 1
    return wins, losses


def _load_markets(markets_dir: Path):
    for p in sorted(markets_dir.glob("*.json")):
        try:
            yield json.loads(p.read_text())
        except json.JSONDecodeError:
            continue


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data", help="data/ directory (default: ./data)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    data = Path(args.data)
    state_path = data / "state.json"
    state = json.loads(state_path.read_text())
    wins, losses = recompute_state_counters(_load_markets(data / "markets"))
    print(f"backfill: wins={wins} losses={losses} (was wins={state.get('wins',0)} losses={state.get('losses',0)})")
    if args.dry_run:
        return
    state["wins"] = wins
    state["losses"] = losses
    state_path.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Run the tests to confirm green**

Run: `python3 -m pytest tests/test_backfill_state.py -q`
Expected: `3 passed`.

- [ ] **Step 4: Execute the backfill against real data**

Run: `python3 tools/backfill_state.py`
Expected: `backfill: wins=0 losses=4 (was wins=0 losses=0)`. `data/state.json` is rewritten.

- [ ] **Step 5: Run the full suite to confirm no regressions**

Run: `python3 -m pytest -q`
Expected: `11 passed`.

- [ ] **Step 6: Commit**

```bash
git add tools/__init__.py tools/backfill_state.py tests/test_backfill_state.py data/state.json
git commit -m "tools: backfill state.wins/losses from existing markets (TDD)

recompute_state_counters() walks data/markets/ and tallies closures by
pnl sign (ties count as losses, matching apply_closure_to_state).
Running once against current data sets wins=0 losses=4 so the Win-rate
KPI shows 0.0 % instead of '—' on next page load."
```

---

## Task 8: Dashboard helper `pickCurrentPrice` — RED

**Files:**
- Create: `tests/dashboard_math.test.mjs`

- [ ] **Step 1: Write the failing test**

```js
// tests/dashboard_math.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";

import { pickCurrentPrice } from "../web/dashboard_math.js";

test("pickCurrentPrice returns the newest snapshot's position_price", () => {
  const market = {
    position: { entry_price: 0.2, shares: 100 },
    market_snapshots: [
      { ts: "t1", top_price: 0.4, position_price: 0.18 },
      { ts: "t2", top_price: 0.5, position_price: 0.22 },
    ],
  };
  const result = pickCurrentPrice(market);
  assert.deepEqual(result, { price: 0.22, stale: false });
});

test("pickCurrentPrice walks back past snapshots without position_price", () => {
  const market = {
    position: { entry_price: 0.2 },
    market_snapshots: [
      { ts: "t1", top_price: 0.4, position_price: 0.18 },
      { ts: "t2", top_price: 0.5, position_price: null },
    ],
  };
  const result = pickCurrentPrice(market);
  assert.deepEqual(result, { price: 0.18, stale: false });
});

test("pickCurrentPrice falls back to entry_price when no snapshot has it", () => {
  const market = {
    position: { entry_price: 0.2 },
    market_snapshots: [
      { ts: "t1", top_price: 0.4 },
      { ts: "t2", top_price: 0.5, position_price: null },
    ],
  };
  const result = pickCurrentPrice(market);
  assert.deepEqual(result, { price: 0.2, stale: true });
});

test("pickCurrentPrice falls back when market_snapshots is empty", () => {
  const market = { position: { entry_price: 0.2 }, market_snapshots: [] };
  assert.deepEqual(pickCurrentPrice(market), { price: 0.2, stale: true });
});
```

- [ ] **Step 2: Run and confirm it fails**

Run: `node --test tests/dashboard_math.test.mjs`
Expected: `Cannot find module '../web/dashboard_math.js'` — 4 failing tests.

## Task 9: Implement `pickCurrentPrice` — GREEN

**Files:**
- Create: `web/dashboard_math.js`

- [ ] **Step 1: Write the module with just enough to pass Task 8**

```js
// web/dashboard_math.js
// Pure math helpers for Dashboard.html. Loaded via plain <script> before
// the Babel block so the React code can call them as DashboardMath.*.
// Also importable from Node tests via `import { ... } from ".../dashboard_math.js"`.
//
// Keep this file dependency-free — it must run as-is in a browser <script>
// tag AND under `node --test`.

export function pickCurrentPrice(market) {
  const entry = market?.position?.entry_price;
  const snaps = market?.market_snapshots || [];
  for (let i = snaps.length - 1; i >= 0; i--) {
    const s = snaps[i];
    if (s && s.position_price != null) {
      return { price: s.position_price, stale: false };
    }
  }
  return { price: entry, stale: true };
}

// Expose as a global for the browser runtime too.
if (typeof window !== "undefined") {
  window.DashboardMath = Object.assign(window.DashboardMath || {}, { pickCurrentPrice });
}
```

- [ ] **Step 2: Run the tests to confirm green**

Run: `node --test tests/dashboard_math.test.mjs`
Expected: `4 pass`.

- [ ] **Step 3: Commit**

```bash
git add web/dashboard_math.js tests/dashboard_math.test.mjs
git commit -m "dashboard: pickCurrentPrice helper (TDD)

Pure function the React code will call instead of reading last.top_price
directly. Walks snapshots newest-first for position_price; falls back to
entry_price with a stale flag so the UI can mark the cell."
```

---

## Task 10: `computeReservedCost` and `computeEquityMark` — RED

**Files:**
- Modify: `tests/dashboard_math.test.mjs`

- [ ] **Step 1: Append failing tests**

```js
import {
  pickCurrentPrice,
  computeReservedCost,
  computeEquityMark,
} from "../web/dashboard_math.js";

test("computeReservedCost sums cost over open markets only", () => {
  const markets = [
    { status: "open",     position: { cost: 20 } },
    { status: "open",     position: { cost: 15 } },
    { status: "closed",   position: { cost: 25 } },
    { status: "resolved", position: { cost: 30 } },
  ];
  assert.equal(computeReservedCost(markets), 35);
});

test("computeReservedCost ignores positions with missing cost", () => {
  const markets = [
    { status: "open", position: {} },
    { status: "open", position: { cost: 10 } },
  ];
  assert.equal(computeReservedCost(markets), 10);
});

test("computeEquityMark adds cash, reserved, and unrealized", () => {
  assert.equal(computeEquityMark({ cash: 680.93, reserved: 300, unrealized: 0 }), 980.93);
  assert.equal(computeEquityMark({ cash: 680.93, reserved: 300, unrealized: -42.5 }), 938.43);
  assert.equal(computeEquityMark({ cash: 1000, reserved: 0, unrealized: 0 }), 1000);
});
```

- [ ] **Step 2: Run and confirm the new tests fail**

Run: `node --test tests/dashboard_math.test.mjs`
Expected: 3 new failing tests (`computeReservedCost is not a function`, etc). Existing 4 still pass.

## Task 11: Implement `computeReservedCost` + `computeEquityMark` — GREEN

**Files:**
- Modify: `web/dashboard_math.js`

- [ ] **Step 1: Add the helpers**

Above the `if (typeof window !== "undefined")` block, add:

```js
export function computeReservedCost(markets) {
  let total = 0;
  for (const m of markets || []) {
    if (m && m.status === "open") {
      total += m.position?.cost || 0;
    }
  }
  return total;
}

export function computeEquityMark({ cash, reserved, unrealized }) {
  return (cash || 0) + (reserved || 0) + (unrealized || 0);
}
```

And extend the global export:

```js
if (typeof window !== "undefined") {
  window.DashboardMath = Object.assign(window.DashboardMath || {}, {
    pickCurrentPrice,
    computeReservedCost,
    computeEquityMark,
  });
}
```

- [ ] **Step 2: Run the full dashboard-math suite**

Run: `node --test tests/dashboard_math.test.mjs`
Expected: `7 pass`.

- [ ] **Step 3: Commit**

```bash
git add web/dashboard_math.js tests/dashboard_math.test.mjs
git commit -m "dashboard: reserved-cost + equity-mark helpers (TDD)"
```

---

## Task 12: `deriveStateStats` — RED

**Files:**
- Modify: `tests/dashboard_math.test.mjs`

- [ ] **Step 1: Append failing tests**

```js
import {
  pickCurrentPrice,
  computeReservedCost,
  computeEquityMark,
  deriveStateStats,
} from "../web/dashboard_math.js";

test("deriveStateStats counts W/L from pnl sign and totals by status", () => {
  const markets = [
    { status: "open",     pnl: 0 },
    { status: "open",     pnl: 0 },
    { status: "closed",   pnl: -5 },
    { status: "closed",   pnl: -3 },
    { status: "resolved", pnl: 12 },
    { status: "resolved", pnl: -7 },
    { status: "resolved", pnl: 0 },   // tie -> loss
  ];
  const equity = [
    { balance: 1000 }, { balance: 1020 }, { balance: 990 }, { balance: 1055 },
  ];
  const stats = deriveStateStats({ markets, equity, startingBalance: 1000 });
  assert.equal(stats.wins, 1);
  assert.equal(stats.losses, 4);
  assert.equal(stats.openCount, 2);
  assert.equal(stats.resolvedCount, 5);
  assert.equal(stats.totalTrades, 7);
  assert.equal(stats.peakBalance, 1055);
});

test("deriveStateStats peak is at least starting balance when no trades", () => {
  const stats = deriveStateStats({ markets: [], equity: [], startingBalance: 1000 });
  assert.equal(stats.peakBalance, 1000);
  assert.equal(stats.totalTrades, 0);
  assert.equal(stats.wins, 0);
  assert.equal(stats.losses, 0);
});
```

- [ ] **Step 2: Run and confirm the new tests fail**

Run: `node --test tests/dashboard_math.test.mjs`
Expected: 2 new failures (`deriveStateStats is not a function`). Prior 7 still pass.

## Task 13: Implement `deriveStateStats` — GREEN

**Files:**
- Modify: `web/dashboard_math.js`

- [ ] **Step 1: Add the helper**

```js
export function deriveStateStats({ markets, equity, startingBalance }) {
  let wins = 0, losses = 0, openCount = 0, resolvedCount = 0;
  for (const m of markets || []) {
    if (m.status === "open") { openCount++; continue; }
    resolvedCount++;
    if (m.pnl > 0) wins++;
    else losses++;             // ties count as losses — matches backfill
  }
  let peak = startingBalance || 0;
  for (const e of equity || []) {
    if (e && typeof e.balance === "number" && e.balance > peak) peak = e.balance;
  }
  return {
    wins, losses,
    openCount, resolvedCount,
    totalTrades: openCount + resolvedCount,
    peakBalance: peak,
  };
}
```

Extend the global export object to include `deriveStateStats`.

- [ ] **Step 2: Run the full JS suite**

Run: `node --test tests/dashboard_math.test.mjs`
Expected: `9 pass`.

- [ ] **Step 3: Commit**

```bash
git add web/dashboard_math.js tests/dashboard_math.test.mjs
git commit -m "dashboard: deriveStateStats helper (TDD)"
```

---

## Task 14: Wire the helpers into `Dashboard.html`

Behavior is already covered by the Node tests. This task is the integration step that replaces inline math with helper calls. Because the helpers are pure and already green, the UI wiring is a mechanical change — no new test.

**Files:**
- Modify: `Dashboard.html`

- [ ] **Step 1: Load `web/dashboard_math.js` before the Babel block**

Find the first `<script type="text/babel">` tag and insert immediately before it:

```html
<script src="web/dashboard_math.js"></script>
```

- [ ] **Step 2: Replace the `openEnriched` derivation**

Locate (around L545-551):

```jsx
  const openEnriched = open.map((m) => {
    const last = m.market_snapshots[m.market_snapshots.length - 1];
    const currentPrice = last ? last.top_price : m.position.entry_price;
    const unrealized = (currentPrice - m.position.entry_price) * (m.position.shares || 0);
    const hoursLeft = Math.max(0, (new Date(m.event_end_date) - Date.now()) / 3600000);
    return { ...m, currentPrice, unrealized, hoursLeft };
  });
```

Replace with:

```jsx
  const openEnriched = open.map((m) => {
    const { price: currentPrice, stale: priceStale } = DashboardMath.pickCurrentPrice(m);
    const unrealized = (currentPrice - m.position.entry_price) * (m.position.shares || 0);
    const hoursLeft = Math.max(0, (new Date(m.event_end_date) - Date.now()) / 3600000);
    return { ...m, currentPrice, unrealized, hoursLeft, priceStale };
  });
```

- [ ] **Step 3: Add reserved + equity derivations**

Immediately after the `unrealizedTotal` line (around L553):

```jsx
  const reservedCost = DashboardMath.computeReservedCost(data.markets);
  const equityMark = DashboardMath.computeEquityMark({
    cash: data.state.balance,
    reserved: reservedCost,
    unrealized: unrealizedTotal,
  });
```

- [ ] **Step 4: Replace the stale `state`-normalisation block in `loadData`**

Locate:

```jsx
  const normState = {
    balance: state.balance ?? startingBalance,
    starting_balance: startingBalance,
    total_trades: state.total_trades ?? markets.length,
    wins: state.wins ?? 0,
    losses: state.losses ?? 0,
    peak_balance: state.peak_balance ?? startingBalance,
    last_updated: lastTickTs || new Date().toISOString(),
  };
```

Replace with:

```jsx
  const derived = DashboardMath.deriveStateStats({ markets, equity, startingBalance });
  const normState = {
    balance: state.balance ?? startingBalance,
    starting_balance: startingBalance,
    total_trades: derived.totalTrades,
    resolved_count: derived.resolvedCount,
    open_count: derived.openCount,
    wins: derived.wins,
    losses: derived.losses,
    peak_balance: derived.peakBalance,
    last_updated: lastTickTs || new Date().toISOString(),
  };
```

- [ ] **Step 5: Split the Balance KPI into Cash + Equity**

Locate the KPI strip (around L717-734). Replace the `Balance` tile:

```jsx
        <KpiTile label="Balance" value={fmtUSD(data.state.balance)} sub={`peak ${fmtUSD(data.state.peak_balance, 0)}`} />
```

with:

```jsx
        <KpiTile
          label="Cash"
          value={fmtUSD(data.state.balance)}
          sub={`${fmtUSD(reservedCost, 0)} reserved in ${open.length} open`}
        />
        <KpiTile
          label="Equity"
          value={fmtUSD(equityMark)}
          sub={`peak ${fmtUSD(data.state.peak_balance, 0)} · mark-to-market`}
          accent={equityMark >= data.state.starting_balance ? "var(--pos)" : "var(--neg)"}
        />
```

- [ ] **Step 6: Clarify the Total-trades and Win-rate KPI subs**

Replace:

```jsx
        <KpiTile label="Total trades" value={data.state.total_trades} sub={`${data.state.wins}W · ${data.state.losses}L`} />
        <KpiTile label="Win rate" value={data.state.wins + data.state.losses ? `${winRate.toFixed(1)}%` : "—"} sub={`of ${data.state.wins + data.state.losses} resolved`} />
```

with:

```jsx
        <KpiTile
          label="Total trades"
          value={data.state.total_trades}
          sub={`${data.state.resolved_count} resolved · ${data.state.open_count} open`}
        />
        <KpiTile
          label="Win rate"
          value={data.state.wins + data.state.losses ? `${winRate.toFixed(1)}%` : "—"}
          sub={`${data.state.wins}W · ${data.state.losses}L of ${data.state.wins + data.state.losses}`}
        />
```

- [ ] **Step 7: Add `·stale` marker in the Open-positions `Last` cell**

Locate the `Last` cell (around L778-782) and add the marker after the existing `<span>`. The cell becomes:

```jsx
                    <td className="mono num">
                      <span className={m.currentPrice >= m.position.entry_price ? "txt-pos" : "txt-neg"}>
                        {m.currentPrice.toFixed(3)}
                      </span>
                      {m.priceStale && (
                        <span className="muted" title="No snapshot yet records this bucket's price — falling back to entry."> ·stale</span>
                      )}
                    </td>
```

- [ ] **Step 8: Tag the Equity-curve card header "realized only"**

Locate:

```jsx
            <div className="card-title">Equity curve</div>
            <div className="card-meta mono">
              {data.equity.length} points · {fmtDate(data.equity[0]?.ts)} → {fmtDate(data.equity[data.equity.length - 1]?.ts)}
            </div>
```

Replace with:

```jsx
            <div className="card-title">Equity curve <span className="card-count mono">realized only</span></div>
            <div className="card-meta mono">
              {data.equity.length} points · {fmtDate(data.equity[0]?.ts)} → {fmtDate(data.equity[data.equity.length - 1]?.ts)} · open positions not marked
            </div>
```

- [ ] **Step 9: Run every test one more time**

Run: `python3 -m pytest -q && node --test tests/dashboard_math.test.mjs`
Expected: `11 passed` (pytest) and `9 pass` (node).

- [ ] **Step 10: Commit**

```bash
git add Dashboard.html
git commit -m "dashboard: consume DashboardMath helpers + split Cash/Equity KPIs

Replaces inline top_price / state-sourced math with calls to the
unit-tested helpers. KPI strip now reports Cash (reserved sub) and
Equity (mark-to-market) separately so the \$300-reserved gap between
state.balance and equity-curve-end is explicit, not hidden."
```

---

## Task 15: End-to-end verification

No production code — just a final walk-through against the real data + one live tick.

- [ ] **Step 1: Regenerate the manifest**

Run: `python3 tools/gen_manifest.py`
Expected: `data/manifest.json` rewritten.

- [ ] **Step 2: Serve and open**

Run: `python3 -m http.server 8000`
Open: `http://localhost:8000/Dashboard.html`

- [ ] **Step 3: Manual checklist (current data, no fresh tick yet)**

- [ ] `Total trades` = `19`, sub `4 resolved · 15 open`.
- [ ] `Win rate` = `0.0%`, sub `0W · 4L of 4`.
- [ ] `Cash` = `$680.93`, sub `$300 reserved in 15 open`.
- [ ] `Equity` = `$980.93`, sub `peak $1,000 · mark-to-market`.
- [ ] `Equity curve` header shows `realized only`; endpoint `$980.93`.
- [ ] Every open-position row shows `·stale` next to `Last`.

- [ ] **Step 4: Trigger one live bot tick**

Either wait for the Railway daemon or run locally if configured:

```bash
python3 bot_v2.py --once 2>&1 | tail -20
```

- [ ] **Step 5: Re-open the dashboard after the tick**

- [ ] At least some open rows lose `·stale`.
- [ ] Their `Last` price ≠ entry price.
- [ ] `Unrealized PnL` KPI is non-zero.
- [ ] `Equity` KPI = `Cash + $300 + Unrealized`.

- [ ] **Step 6: Push**

```bash
git push origin main
```

---

## Self-Review Checklist

1. **Spec coverage:** Every audit finding has at least one RED/GREEN pair:
   - Unrealized PnL wrong → Task 2/3 (bot) + 8/9 (helper) + 14 (wiring).
   - Win-rate KPI stuck → Task 4/5 (bot helper) + 6/7 (backfill) + 12/13 (helper) + 14 (wiring).
   - Balance vs Equity conflation → Task 10/11 + 12/13 (derived peak) + 14 (KPI split).
   - Total-trades mislabel → Task 12/13 + 14. ✓

2. **Placeholder scan:** No TBDs, no "handle edge cases", no "similar to Task N". Every RED step has real code; every GREEN step has the minimal implementation in full. ✓

3. **Type consistency:**
   - `compute_position_price(outcomes, position)` defined in Task 3, called in Task 3 and nowhere else.
   - `apply_closure_to_state(state, pnl)` defined in Task 5, called from two sites in Task 5.
   - `recompute_state_counters(markets)` defined in Task 7, tested in Task 6.
   - `pickCurrentPrice(market) → {price, stale}` — same shape in Task 8 test, Task 9 impl, Task 14 destructure.
   - `computeReservedCost(markets)`, `computeEquityMark({cash, reserved, unrealized})`, `deriveStateStats({markets, equity, startingBalance}) → {wins, losses, openCount, resolvedCount, totalTrades, peakBalance}` — property names match across Tasks 10-13 and the Task 14 wiring.
   - `priceStale`, `reservedCost`, `equityMark`, `derived.resolvedCount`, `derived.openCount`, `derived.peakBalance`, `state.resolved_count`, `state.open_count` all defined before use. ✓

4. **TDD discipline:** Every production-code task (3, 5, 7, 9, 11, 13) is preceded by a RED-only task (2, 4, 6, 8, 10, 12) whose final step is `Run ... Expected: <specific failure reason>`. Task 14 is pure integration of already-tested helpers and is explicitly justified (no new behavior, only wiring). Task 1 and Task 15 are harness / verification and contain no production code. ✓
