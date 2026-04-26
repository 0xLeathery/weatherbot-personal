# Realized PnL Tracking — Design

**Date:** 2026-04-26
**Scope:** `bot_v2.py` (weather/Kalshi bot) only. `crypto_bot.py` is abandoned and out of scope.
**Status:** Design approved (accumulator-only), pending spec review.

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

- **Canonical realized PnL number** in `state.json`, updated on every closure.
- **Session-scoped semantics** — wiped on reset along with `state.json` and market files. No "lifetime" accumulator.
- **Dashboard reads the canonical number**, with a self-consistency check against the per-market sum.

## Non-Goals

- Lifetime PnL across resets (explicitly rejected — "profit for this session").
- Append-only ledger / per-trade audit log. Considered and rejected on YAGNI grounds: every concrete consumer (equity curve migration, per-period slicing, per-city breakdowns) is itself out of scope for this iteration. Add the ledger when there's a real consumer asking for it.
- Crypto bot parity (crypto bot is abandoned).
- Backfill from existing market files (not needed — bot was reset to zero on Apr 23 with no trades since).
- Migrating the dashboard equity curve away from market-file scan (follow-up).

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

`load_state()` adds `data.setdefault("realized_pnl", 0.0)` so the existing post-reset state file silently gains the field.

---

## Closure Path

Single chokepoint exists already: `apply_closure_to_state` at `bot_v2.py:490`. Currently:

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

Add two lines — no signature change:

```python
def apply_closure_to_state(state, pnl):
    if pnl is None:
        return
    if pnl > 0:
        state["wins"] = state.get("wins", 0) + 1
    else:
        state["losses"] = state.get("losses", 0) + 1
    state["total_trades"] = state.get("total_trades", 0) + 1
    state["realized_pnl"] = round(state.get("realized_pnl", 0.0) + pnl, 2)
```

That's the entire bot-side change. Every existing call site (verified by grep at `bot_v2.py:507`, `:688`, `:699`, `:852`, plus the take-profit path around `:1041` to confirm during implementation) already passes the correct `pnl` value, so no call-site edits.

### Rounding

`round(... + pnl, 2)` on every increment keeps the accumulator bounded against float drift. Since per-trade PnL is already rounded to 2 decimals at the call sites (`bot_v2.py:521`, `:681`, `:840`), the accumulator stays exact.

### Save ordering

`apply_closure_to_state` mutates the dict; the existing `save_state(state)` call after the close path persists it. No new I/O ordering concerns — same persistence pattern as the existing `wins` / `losses` counters.

---

## Dashboard

`dashboard_server.py:48` already publishes `state.json` via `data/manifest.json`. The dashboard fetches `state.json` directly — once `realized_pnl` is in the file, it's available with no server change.

`Dashboard.html:1030` currently:

```js
const totalPnL = resolved.reduce((s, p) => s + (p.pnl || 0), 0);
```

Replace with:

```js
const totalPnL = state.realized_pnl ?? 0;
const recomputed = resolved.reduce((s, p) => s + (p.pnl || 0), 0);
if (Math.abs(totalPnL - recomputed) > 0.01) {
  console.warn(`realized_pnl drift: state=${totalPnL} recomputed=${recomputed}`);
}
```

The `?? 0` fallback handles the brief window where a stale `state.json` from before this change is loaded. The drift warning is console-only — never UI. State-derived value is the truth.

The equity curve at `Dashboard.html:587` continues to derive from market files. Migrating it is out of scope.

### Drift expectations

The two values can legitimately diverge in one situation: a market file is deleted while its closure remains counted in `state.realized_pnl`. That's recovery behaviour we want — accumulator survives pruning. The console warning surfaces it without breaking the UI.

---

## Reset

The Apr 23 reset deleted `state.json` and all market files manually. With this change, the reset procedure is unchanged: `state.json` deletion clears `realized_pnl` along with `balance` / `wins` / `losses`. No new files to clean up.

If a reset script exists in `tools/`, no change needed. If reset is documented anywhere (e.g. `BOT_README.md`), no change needed — the new field is wiped by the existing procedure.

---

## Tests

One new test, in the existing `tests/` style:

**`test_realized_pnl_accumulator`** — open three positions, close them three different ways across three markets (stop_loss path, resolved_win path, forecast_changed path) by exercising the actual closure functions in `bot_v2.py`, not by calling `apply_closure_to_state` directly. Assert:

- `state["realized_pnl"]` equals the sum of the three `pnl` values, rounded to 2 decimals
- `state["realized_pnl"]` equals `sum(mkt["pnl"] for mkt in markets)` — the dashboard's recomputed value matches
- `state["balance"]` is consistent with `starting_balance + state["realized_pnl"] - sum(open_position_costs)` (sanity check that no path forgot to update both balance and accumulator)

A separate "no closures means zero" case is implicit in `load_state()` returning `0.0` when the field is absent — covered by reading any existing test that calls `load_state()` on a fresh state.

---

## Out of scope (follow-ups)

- Append-only ledger of every closure event
- Migrating the dashboard equity curve from market-file scan to a ledger
- Per-period reporting (daily / weekly PnL)
- Per-city or per-reason breakdowns
- Lifetime accumulator across resets (explicitly rejected)
- Crypto bot parity (crypto bot is abandoned)
