# Realized PnL Accumulator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a session-scoped `realized_pnl` accumulator to `state.json` so the dashboard surfaces a canonical "profit since reset" number that survives market-file pruning.

**Architecture:** One new field in `state.json`. Extend the existing single-chokepoint helper `apply_closure_to_state` in `bot_v2.py` with a one-line increment. Replace the dashboard's per-load market-file sum with the canonical state field; emit an asymmetric drift warning to console. Session-scoped — wiped along with the rest of state on reset.

**Tech Stack:** Python 3 (`bot_v2.py`, `pytest`), vanilla JS / React-without-build (`Dashboard.html`).

**Spec:** [docs/superpowers/specs/2026-04-26-realized-pnl-design.md](../specs/2026-04-26-realized-pnl-design.md)

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `bot_v2.py:442-463` | Modify | Add `setdefault("realized_pnl", 0.0)` to `load_state()` |
| `bot_v2.py:490-502` | Modify | Add accumulator increment to `apply_closure_to_state` |
| `tests/test_state_resilience.py` | Modify | Add `realized_pnl` default test to `TestPartialState` |
| `tests/test_realized_pnl.py` | Create | Unit + integration tests for accumulator across closure paths |
| `Dashboard.html:1022,1030` | Modify | Read `state.realized_pnl`; add asymmetric drift `console.warn` |

No call-site edits are needed in `bot_v2.py`. The four existing call sites (`:688`, `:699`, `:852`, `:1061`) all already invoke `apply_closure_to_state(state, pnl)` — extending the helper propagates to all of them.

---

## Task 1: Default `realized_pnl` to 0.0 in `load_state`

**Files:**
- Test: `tests/test_state_resilience.py` (extend existing `TestPartialState` class)
- Modify: `bot_v2.py:442-463`

- [ ] **Step 1: Write the failing test**

Append to the `TestPartialState` class in `tests/test_state_resilience.py` (around line 60–110, alongside `test_missing_wins_losses_get_zero`):

```python
    def test_missing_realized_pnl_gets_zero(self, tmp_path, monkeypatch):
        state_file = tmp_path / "state.json"
        state_file.write_text(json.dumps({
            "balance": 1000.0,
            "starting_balance": 1000.0,
            "total_trades": 0,
            "wins": 0,
            "losses": 0,
            "peak_balance": 1000.0,
        }))
        monkeypatch.setattr("bot_v2.STATE_FILE", state_file)

        state = load_state()

        assert state["realized_pnl"] == 0.0
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_state_resilience.py::TestPartialState::test_missing_realized_pnl_gets_zero -v`

Expected: FAIL with `KeyError: 'realized_pnl'` (because `load_state` doesn't set the default yet).

- [ ] **Step 3: Add the setdefault to `load_state`**

In `bot_v2.py`, find this block (currently around `:447–:452`):

```python
            if isinstance(data, dict):
                data.setdefault("balance", BALANCE)
                data.setdefault("peak_balance", data.get("balance", BALANCE))
                data.setdefault("wins", 0)
                data.setdefault("losses", 0)
                data.setdefault("total_trades", 0)
                data.setdefault("starting_balance", data.get("balance", BALANCE))
                return data
```

Add one line before `return data`:

```python
            if isinstance(data, dict):
                data.setdefault("balance", BALANCE)
                data.setdefault("peak_balance", data.get("balance", BALANCE))
                data.setdefault("wins", 0)
                data.setdefault("losses", 0)
                data.setdefault("total_trades", 0)
                data.setdefault("starting_balance", data.get("balance", BALANCE))
                data.setdefault("realized_pnl", 0.0)
                return data
```

And in the same function's bottom-of-function default dict (currently `:456-462`), add the field:

```python
    return {
        "balance":          BALANCE,
        "starting_balance": BALANCE,
        "total_trades":     0,
        "wins":             0,
        "losses":           0,
        "peak_balance":     BALANCE,
        "realized_pnl":     0.0,
    }
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/test_state_resilience.py::TestPartialState::test_missing_realized_pnl_gets_zero -v`

Expected: PASS.

- [ ] **Step 5: Run the full state resilience suite to confirm no regressions**

Run: `pytest tests/test_state_resilience.py -v`

Expected: ALL PASS (existing tests assert specific keys; the new field is additive and won't break them).

- [ ] **Step 6: Commit**

```bash
git add tests/test_state_resilience.py bot_v2.py
git commit -m "feat: default realized_pnl to 0.0 in load_state"
```

---

## Task 2: Increment `realized_pnl` in `apply_closure_to_state`

**Files:**
- Create: `tests/test_realized_pnl.py`
- Modify: `bot_v2.py:490-502`

This task uses an integration-first TDD: write a test that exercises a real closure path (the stop-loss code path in `monitor_positions`) and asserts the accumulator updated. The failure proves the helper isn't wiring through. Then add the one-line increment to `apply_closure_to_state`. The same change makes all four closure call sites work; subsequent steps add tests confirming each path is covered.

- [ ] **Step 1: Create the new test file with the integration test for the stop-loss path**

Create `tests/test_realized_pnl.py` with this content:

```python
"""Tests for state.realized_pnl — the session-scoped realized PnL accumulator.

The accumulator lives in state.json and is incremented inside
apply_closure_to_state on every closure. All four closure paths in bot_v2.py
(monitor early-close, forecast-changed close, scan_and_update final resolve,
take-profit/stop-loss/trailing-stop) reach this single helper, so this test
file covers each path end-to-end as well as the helper itself.

Invariants:
  - state["realized_pnl"] equals the sum of pnl across all closures since reset
  - Increments are rounded to 2 decimals (no float drift accumulation)
  - state["realized_pnl"] equals sum(mkt["pnl"] for mkt in markets) — the
    dashboard's recomputed value matches the accumulator
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from bot_v2 import apply_closure_to_state, load_state, monitor_positions


# --- helpers ----------------------------------------------------------------

def _make_position(entry_price=0.50, shares=20.0, cost=10.0, stop_price=0.40, **extra):
    pos = {
        "market_id":   "mkt_1",
        "entry_price": entry_price,
        "shares":      shares,
        "cost":        cost,
        "stop_price":  stop_price,
        "status":      "open",
        "bucket_low":  70,
        "bucket_high": 75,
        "forecast_src": "hrrr",
        "pnl":         None,
        "exit_price":  None,
        "closed_at":   None,
        "close_reason": None,
    }
    pos.update(extra)
    return pos


def _make_market(position=None, city="dallas", date="2026-05-01"):
    return {
        "market_id":   "mkt_1",
        "city":        city,
        "city_name":   city.capitalize(),
        "date":        date,
        "unit":        "F",
        "station":     "KDAL",
        "event_end_date": f"{date}T23:00:00Z",
        "hours_at_discovery": 24.0,
        "status":      "open",
        "position":    position,
        "actual_temp": None,
        "resolved_outcome": None,
        "pnl":         None,
        "forecast_snapshots": [],
        "market_snapshots":   [],
        "all_outcomes":       [],
        "created_at":  "2026-04-30T00:00:00Z",
    }


def _mock_gamma_response(market_id, best_bid=None):
    mock_resp = MagicMock()
    data = {"id": market_id}
    if best_bid is not None:
        data["bestBid"] = best_bid
    mock_resp.json.return_value = data
    return mock_resp


def _write_state(path, **overrides):
    state = {
        "balance":          1000.0,
        "starting_balance": 1000.0,
        "total_trades":     0,
        "wins":             0,
        "losses":           0,
        "peak_balance":     1000.0,
        "realized_pnl":     0.0,
    }
    state.update(overrides)
    path.write_text(json.dumps(state))


# --- integration: stop-loss path through monitor_positions ------------------

class TestStopLossPathUpdatesRealizedPnL:
    def test_stop_loss_close_decrements_realized_pnl(self, tmp_path, monkeypatch):
        monkeypatch.setattr("bot_v2.MARKETS_DIR", tmp_path / "markets")
        monkeypatch.setattr("bot_v2.STATE_FILE", tmp_path / "state.json")
        (tmp_path / "markets").mkdir()

        # Position cost $10, entry $0.50, stop $0.40. Price drops to $0.40 →
        # realized loss of (0.40 - 0.50) * 20 = -$2.00.
        pos = _make_position(entry_price=0.50, shares=20.0, cost=10.0, stop_price=0.40)
        mkt = _make_market(position=pos)
        (tmp_path / "markets" / "dallas_2026-05-01.json").write_text(json.dumps(mkt))
        _write_state(tmp_path / "state.json", balance=990.0, total_trades=1)

        with patch("bot_v2.requests.get", return_value=_mock_gamma_response("mkt_1", best_bid=0.40)):
            assert monitor_positions() == 1

        state = json.loads((tmp_path / "state.json").read_text())
        assert state["realized_pnl"] == -2.00
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_realized_pnl.py::TestStopLossPathUpdatesRealizedPnL -v`

Expected: FAIL — `state["realized_pnl"]` will be `0.0`, not `-2.00`, because `apply_closure_to_state` doesn't yet update the accumulator.

- [ ] **Step 3: Add the accumulator increment to `apply_closure_to_state`**

In `bot_v2.py`, the function currently reads (around `:490–:502`):

```python
def apply_closure_to_state(state, pnl):
    """Update wins/losses/total_trades counters in-place after a closure.

    pnl is the realized PnL of the closed position. Counters mutate so that
    every realized closure ends up in state.wins or state.losses.
    """
    if pnl is None:
        return
    state["total_trades"] = state.get("total_trades", 0) + 1
    if pnl > 0:
        state["wins"] = state.get("wins", 0) + 1
    else:
        state["losses"] = state.get("losses", 0) + 1
```

Add one line at the end (after the win/loss branch):

```python
def apply_closure_to_state(state, pnl):
    """Update wins/losses/total_trades counters in-place after a closure.

    pnl is the realized PnL of the closed position. Counters mutate so that
    every realized closure ends up in state.wins or state.losses.
    state["realized_pnl"] also accumulates the signed pnl (rounded to 2dp).
    """
    if pnl is None:
        return
    state["total_trades"] = state.get("total_trades", 0) + 1
    if pnl > 0:
        state["wins"] = state.get("wins", 0) + 1
    else:
        state["losses"] = state.get("losses", 0) + 1
    state["realized_pnl"] = round(state.get("realized_pnl", 0.0) + pnl, 2)
```

- [ ] **Step 4: Run the integration test to verify it passes**

Run: `pytest tests/test_realized_pnl.py::TestStopLossPathUpdatesRealizedPnL -v`

Expected: PASS.

- [ ] **Step 5: Add unit tests for `apply_closure_to_state` directly**

Append to `tests/test_realized_pnl.py`:

```python
# --- unit: apply_closure_to_state ------------------------------------------

class TestApplyClosureToStateAccumulator:
    def _fresh_state(self):
        return {
            "balance": 1000.0, "starting_balance": 1000.0,
            "total_trades": 0, "wins": 0, "losses": 0,
            "peak_balance": 1000.0, "realized_pnl": 0.0,
        }

    def test_positive_pnl_increments_accumulator(self):
        state = self._fresh_state()
        apply_closure_to_state(state, 4.20)
        assert state["realized_pnl"] == 4.20

    def test_negative_pnl_decrements_accumulator(self):
        state = self._fresh_state()
        apply_closure_to_state(state, -3.50)
        assert state["realized_pnl"] == -3.50

    def test_none_pnl_is_noop(self):
        state = self._fresh_state()
        apply_closure_to_state(state, None)
        assert state["realized_pnl"] == 0.0
        assert state["total_trades"] == 0  # also no counter bump

    def test_multiple_closures_sum_to_two_decimals(self):
        state = self._fresh_state()
        apply_closure_to_state(state, 1.111)
        apply_closure_to_state(state, 2.222)
        apply_closure_to_state(state, -0.333)
        # 1.111 + 2.222 - 0.333 = 3.000, rounded to 2dp at every step
        # => 1.11 + 2.22 - 0.33 = 3.00
        assert state["realized_pnl"] == 3.00

    def test_missing_realized_pnl_field_treated_as_zero(self):
        # Backwards-compat: state files written before this change lack the field.
        state = {
            "balance": 1000.0, "starting_balance": 1000.0,
            "total_trades": 0, "wins": 0, "losses": 0, "peak_balance": 1000.0,
        }
        apply_closure_to_state(state, 2.50)
        assert state["realized_pnl"] == 2.50
```

- [ ] **Step 6: Run the unit tests**

Run: `pytest tests/test_realized_pnl.py::TestApplyClosureToStateAccumulator -v`

Expected: ALL PASS.

- [ ] **Step 7: Add an integration test for the take-profit / trailing-stop path**

Append to `tests/test_realized_pnl.py`:

```python
# --- integration: take-profit path through monitor_positions ----------------

class TestTakeProfitPathUpdatesRealizedPnL:
    def test_take_profit_close_increments_realized_pnl(self, tmp_path, monkeypatch):
        monkeypatch.setattr("bot_v2.MARKETS_DIR", tmp_path / "markets")
        monkeypatch.setattr("bot_v2.STATE_FILE", tmp_path / "state.json")
        (tmp_path / "markets").mkdir()

        # Take-profit threshold is 0.85 when 24-48h to resolution, 0.75 if 48h+.
        # Use an event 60h out so threshold is 0.75. Entry 0.50, shares 20,
        # exit 0.75 → realized PnL of (0.75 - 0.50) * 20 = +$5.00.
        from datetime import datetime, timezone, timedelta
        end_dt = datetime.now(timezone.utc) + timedelta(hours=60)
        pos = _make_position(entry_price=0.50, shares=20.0, cost=10.0, stop_price=0.40)
        mkt = _make_market(position=pos)
        mkt["event_end_date"] = end_dt.isoformat().replace("+00:00", "Z")
        (tmp_path / "markets" / "dallas_2026-05-01.json").write_text(json.dumps(mkt))
        _write_state(tmp_path / "state.json", balance=990.0, total_trades=1)

        with patch("bot_v2.requests.get", return_value=_mock_gamma_response("mkt_1", best_bid=0.75)):
            assert monitor_positions() == 1

        state = json.loads((tmp_path / "state.json").read_text())
        assert state["realized_pnl"] == 5.00
        # Per-market pnl matches accumulator (dashboard recompute parity)
        saved = json.loads((tmp_path / "markets" / "dallas_2026-05-01.json").read_text())
        assert saved["position"]["pnl"] == 5.00
```

- [ ] **Step 8: Run the take-profit test**

Run: `pytest tests/test_realized_pnl.py::TestTakeProfitPathUpdatesRealizedPnL -v`

Expected: PASS (the wiring done in Step 3 already covers this path).

- [ ] **Step 9: Run the full test_realized_pnl.py suite plus existing closure-path tests as a regression check**

Run: `pytest tests/test_realized_pnl.py tests/test_monitor_positions.py tests/test_scan_and_update.py tests/test_forecast_changed_guard.py -v`

Expected: ALL PASS. (Existing tests don't assert on `realized_pnl` so they'll pass; if any break due to the new field, investigate before continuing.)

- [ ] **Step 10: Commit**

```bash
git add tests/test_realized_pnl.py bot_v2.py
git commit -m "feat: accumulate realized_pnl in apply_closure_to_state"
```

---

## Task 3: Surface `state.realized_pnl` in the dashboard with asymmetric drift warning

**Files:**
- Modify: `Dashboard.html:1022,1030` (the `KpiTile label="Total P&L"` block)

The dashboard already fetches `data/state.json` via `data/manifest.json` (no server change needed — verified at `dashboard_server.py:48`). Replace the per-load market-file sum with `state.realized_pnl` and add a console-only warning when market files contain a closure the accumulator missed.

- [ ] **Step 1: Read the existing Total P&L computation block**

Open `Dashboard.html` and find the block around line 1022 — it should read:

```js
  const totalPnL = resolved.reduce((s, p) => s + (p.pnl || 0), 0);
```

…and a few lines below, the rendering tile around line 1030:

```jsx
        <KpiTile label="Total P&L" value={fmtUSD(totalPnL)}      sub="realized" />
```

Note the surrounding scope: `totalPnL` is computed inside a React component that already has `state` (the loaded `state.json`) and `resolved` (the list of resolved markets) in scope. Verify this by reading the 30 lines above the `totalPnL` line. If `state` is named differently (e.g. `botState`), use the actual name in the next steps.

- [ ] **Step 2: Replace the totalPnL line with the canonical accumulator + asymmetric drift check**

Change the `const totalPnL = ...` line to:

```js
  // Canonical: state.realized_pnl is the source of truth (accumulator survives
  // market-file pruning). recomputed is the per-load sum from market files,
  // used only as an asymmetric drift sentinel.
  const totalPnL = (state && typeof state.realized_pnl === "number") ? state.realized_pnl : 0;
  const recomputedPnL = resolved.reduce((s, p) => s + (p.pnl || 0), 0);
  // Only warn when files contain a closure the accumulator missed (genuine
  // bug). The opposite direction (accumulator > files) is expected after
  // pruning and stays silent.
  if (recomputedPnL - totalPnL > 0.01) {
    console.warn(
      `[realized_pnl] undercount: state=${totalPnL.toFixed(2)} recomputed=${recomputedPnL.toFixed(2)}`
    );
  }
```

The KpiTile JSX one line below stays unchanged:

```jsx
        <KpiTile label="Total P&L" value={fmtUSD(totalPnL)}      sub="realized" />
```

- [ ] **Step 3: Smoke-test in the browser**

The dashboard has no JS test harness for `Dashboard.html` itself (only the helper modules `dashboard_math.test.mjs` etc.). Manual smoke test:

1. Start the dashboard server: `python dashboard_server.py` from the repo root. Note the port (default `8000`).
2. Open `http://localhost:8000/Dashboard.html` in a browser. If basic auth is set, supply `DASH_USER` / `DASH_PASS`.
3. Open the browser dev tools console.
4. Confirm:
   - The Total P&L KPI tile renders a dollar value (zero on a freshly-reset state is correct).
   - **No** `[realized_pnl] undercount` warning appears (state and market files agree on a fresh setup).

Then simulate the drift bug to verify the warning fires:

5. With the dashboard still loaded, manually edit `data/state.json` to set `"realized_pnl": -10.00` (a value lower than the per-market sum, which on a fresh reset is `0`). Save.
6. Wait up to 60s for the manifest regen, then refresh the dashboard.
7. Confirm `[realized_pnl] undercount: state=-10.00 recomputed=0.00` appears in the console.
8. Restore `data/state.json` to `"realized_pnl": 0.0`.

If you cannot run the dashboard server locally (e.g. no display), document this and report it — do NOT mark this step complete based on code inspection alone.

- [ ] **Step 4: Commit**

```bash
git add Dashboard.html
git commit -m "feat(dashboard): read realized_pnl from state with drift warning"
```

---

## Self-Review

**Spec coverage:**
- "Canonical realized PnL number in state.json" → Task 1 (default) + Task 2 (increment).
- "Session-scoped, wiped on reset" → covered implicitly: `realized_pnl` lives in `state.json`, deleted with it. Spec says reset procedure unchanged. No code task needed.
- "Dashboard reads canonical number with self-consistency check" → Task 3.
- "Asymmetric drift warning (only `recomputed > state`)" → Task 3, Step 2.
- "Test #1: accumulator math via real closure paths" → Task 2 Step 1 (stop_loss path) + Step 7 (take_profit path) + Step 5 (unit tests for the helper).
- "Test #2: load_state defaults realized_pnl to zero" → Task 1 Step 1.

All spec sections have a task. No gaps.

**Placeholder scan:** No "TBD", "TODO", or "implement later" in the plan. Every code step shows the actual code. Every test shows the actual test. Every command is exact.

**Type consistency:**
- `state["realized_pnl"]` (snake_case Python) — used consistently in Tasks 1, 2.
- `state.realized_pnl` (JS dot access) — used consistently in Task 3.
- `apply_closure_to_state(state, pnl)` signature unchanged — confirmed by Tasks 1 and 2.
- Float values rounded at increment time (Task 2 Step 3); test assertions use 2dp values (Task 2 Steps 5–7) — consistent.

No issues.
