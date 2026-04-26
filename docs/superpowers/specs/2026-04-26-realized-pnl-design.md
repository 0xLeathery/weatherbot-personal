# Realized PnL Tracking — Design

**Date:** 2026-04-26
**Scope:** `bot_v2.py` (weather/Kalshi bot) only. `crypto_bot.py` is abandoned and out of scope.
**Status:** Design approved, pending spec review.

---

## Problem

The bot has no first-class realized PnL number. PnL is recorded per-market on each `mkt["pnl"]` field, and the dashboard sums them on the fly:

```js
const totalPnL = resolved.reduce((s, p) => s + (p.pnl || 0), 0);
```

This has three failure modes:

1. **Pruning loses history.** Deleting market files (as happened during the Apr 23 reset) permanently destroys realized PnL — only `state.wins` / `state.losses` counts survive, not dollars.
2. **No canonical truth.** "How profitable are we?" requires scanning every market file. Two callers can disagree if one of them filters differently.
3. **Closed-but-not-resolved trades may be missed.** The dashboard tile filters on `status === "resolved"`, which can exclude positions closed early via stop-loss / take-profit / forecast-change.

The user wants a number that reflects "profit for this session" — locked-in PnL since the last reset, independent of unrealized fluctuation on open positions.

## Goals

- **Canonical realized PnL number** in `state.json`, updated atomically with every closure.
- **Append-only ledger** of every closure event, sufficient to replay/audit and to drive the equity curve.
- **Session-scoped semantics** — both reset together with `state.json` and market files. No "lifetime" accumulator.
- **Dashboard reads the canonical number**, with a self-consistency check against the per-market sum.

## Non-Goals

- Lifetime PnL across resets (explicitly rejected by user — "profit for this session").
- Crypto bot parity.
- Backfill from existing market files (not needed — bot was reset to zero on Apr 23 with no trades since).
- Migrating the dashboard equity curve to the ledger (follow-up work).

---

## Data Model

### `state.json` — one new field

```json
{
  "balance": 1000.0,
  "starting_balance": 1000.0,
  "total_trades": 0,
  "wins": 0,
  "losses": 0,
  "peak_balance": 1000.0,
  "realized_pnl": 0.0
}
```

`realized_pnl` is signed (losses subtract, wins add), rounded to 2 decimals on every increment. It is the sum of `pnl` over all closures since the last reset.

`load_state()` adds `data.setdefault("realized_pnl", 0.0)` so existing state files (post-Apr-23 reset has no closures) silently gain the field.

### `data/realized_ledger.jsonl` — append-only

One JSON object per line, written at the moment of closure:

```json
{"ts": "2026-04-26T11:09:00Z", "market_id": "kxhighny-26apr26-72", "city": "newyork", "date": "2026-04-26", "reason": "stop_loss", "cost": 18.50, "pnl": -7.40, "balance_after": 992.60, "realized_pnl_after": -7.40}
```

| Field | Type | Notes |
|---|---|---|
| `ts` | ISO-8601 UTC | Time of closure (not market resolution date) |
| `market_id` | string | Joins back to the market file |
| `city` | string | Slug, matches `mkt["city"]` |
| `date` | string | The market's date, matches `mkt["date"]` |
| `reason` | string | Closed vocabulary — see below |
| `cost` | float | The position's original cost (so ROI = pnl/cost) |
| `pnl` | float | Signed, rounded to 2 decimals |
| `balance_after` | float | `state.balance` after this closure was applied |
| `realized_pnl_after` | float | `state.realized_pnl` after this closure was applied |

`balance_after` and `realized_pnl_after` are written for self-consistency: replaying the file should reproduce both values exactly.

**Reason vocabulary** (closed set, validated on write):
- `resolved_win` — market resolved in our favour at expiry
- `resolved_loss` — market resolved against us at expiry
- `stop_loss` — early close because price crossed stop threshold
- `take_profit` — early close because price crossed take-profit threshold
- `trailing_stop` — early close because trailing stop triggered
- `forecast_changed` — early close because the forecast moved out of our bucket

Unknown reasons raise `ValueError` — adding a new closure path requires adding it here first.

---

## Closure Path

Single chokepoint: `apply_closure_to_state` at `bot_v2.py:490`. Currently:

```python
def apply_closure_to_state(state, pnl):
    if pnl is None:
        return
    if pnl > 0:
        state["wins"] = state.get("wins", 0) + 1
    else:
        state["losses"] = state.get("losses", 0) + 1
    state["total_trades"] = state.get("total_trades", 0) + 1
```

New signature:

```python
def apply_closure_to_state(state, mkt, pos, reason):
    """Apply a closure: update counters, accumulator, and append to ledger.

    Order: append ledger row first (POSIX O_APPEND is atomic), then update state.
    A crash between leaves an orphan ledger row that is recoverable by replay;
    the reverse order would silently lose the closure.
    """
```

Inputs: `mkt` and `pos` are the existing dicts; both already carry `city`, `date`, `market_id`, `cost`, and `pnl`. `reason` is one of the closed-set strings above.

**Precondition:** every existing call site already sets `pos["pnl"]` (and where applicable `mkt["pnl"]`) before invoking `apply_closure_to_state`. The new signature preserves that contract — the caller computes `pnl`, stores it on the position, then calls in. The function reads `pos["pnl"]` rather than taking it as a parameter, eliminating one source of drift.

### Call sites to update

The current code calls `apply_closure_to_state(state, pnl)` from at least these locations (verified by grep — implementation must do an exhaustive sweep, not trust this list):

- `bot_v2.py:507` — `close_position_at_resolution` (called by monitor loop on natural resolution)
- `bot_v2.py:688` — `monitor_positions` early-close (`stop_loss` / `take_profit` / `trailing_stop`)
- `bot_v2.py:699` — forecast-changed close
- `bot_v2.py:852` — `scan_and_update` final resolve (resolved_win / resolved_loss)
- `bot_v2.py:~1041` — additional take-profit path (needs verification)

Each call site supplies the appropriate `reason`. If an existing call site cannot determine the reason (e.g., `monitor_positions` may already track it in a `reason` local), pipe that through.

### Atomicity

Ledger is opened with `O_APPEND` (Python's `open(path, "a")` does this on POSIX). A single `f.write(line + "\n")` followed by `f.flush()` is sufficient — partial writes are vanishingly rare and would corrupt at most one line. No locking required (single-writer assumption holds — only the bot process writes).

State save (`save_state`) writes the full JSON and is the existing pattern; not changing it.

### Rounding

Both `state.realized_pnl` and `pnl` in the ledger are rounded to 2 decimals at write time. This guarantees:

```python
state["realized_pnl"] == round(sum(row["pnl"] for row in ledger), 2)
```

…holds exactly, modulo floating-point summation order over hundreds of trades. Acceptable; existing code uses floats throughout.

---

## Dashboard

`Dashboard.html:1030` currently:

```js
const totalPnL = resolved.reduce((s, p) => s + (p.pnl || 0), 0);
```

Replace with:

```js
const totalPnL = state.realized_pnl;
const recomputed = resolved.reduce((s, p) => s + (p.pnl || 0), 0);
if (Math.abs(totalPnL - recomputed) > 0.01) {
  console.warn(`realized_pnl drift: state=${totalPnL} recomputed=${recomputed}`);
}
```

The drift warning is console-only — never UI. State-derived value is the truth.

The equity curve at `Dashboard.html:587` continues to derive from market files for now. Migrating it to the ledger is a follow-up.

---

## Reset

The Apr 23 reset deleted `state.json` and all market files manually. This design adds one more file to delete: `data/realized_ledger.jsonl`.

Action: locate the reset path during implementation (likely manual, possibly a script in `tools/`). Update `BOT_README.md` to document the new file in the reset checklist. If a reset script exists, add the deletion there.

---

## Tests

Three new tests in the existing `tests/` style:

1. **`test_realized_pnl_accumulator`** — open a position, close it three different ways across three markets (stop_loss, resolved_win, forecast_changed). Assert:
   - `state["realized_pnl"]` equals the sum of the three `pnl` values, rounded
   - `state["realized_pnl"]` equals `sum(ledger.pnl)`
   - `state["balance"]` is consistent with `starting_balance + realized_pnl - sum(open_position_costs)`

2. **`test_unknown_reason_raises`** — calling `apply_closure_to_state(state, mkt, pos, reason="foo")` raises `ValueError`.

3. **`test_ledger_replay_reconstructs_state`** — write 100 closures in sequence. Truncate the ledger at a random byte offset (simulating crash mid-write). Assert that the truncated ledger has N or N-1 valid JSON lines (no partial garbage) and that replaying reconstructs `realized_pnl` exactly equal to `sum(replayed_pnl)`.

---

## Out of scope (follow-ups)

- Migrating the dashboard equity curve from market-file scan to the ledger
- Per-period reporting (daily / weekly PnL)
- Per-city or per-reason breakdowns derived from the ledger
- Lifetime accumulator across resets (explicitly rejected)
- Crypto bot parity (crypto bot is abandoned)
