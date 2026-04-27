# Weatherbot Reliability Fix-ups — Design Spec

**Date:** 2026-04-28
**Status:** Draft (awaiting user review)
**Scope:** Four targeted reliability fixes (A, B, C, D) + an operational appendix (E).

---

## 1. Goal

Tighten the reliability of the production bot so that:

1. Process crashes are visible to Railway (no silent partial failures).
2. The closure ledger and `state.realized_pnl` cannot drift permanently — startup self-heals.
3. Dashboard PnL math is correct even after a crash-induced duplicate ledger row.
4. The full test suite is green.

The user reported "we have no realised PnL" while looking at local state. The realized-PnL accumulator code itself is correct; the immediate symptom is that local state is empty (reset 2026-04-23, no markets, dashboard never re-rendered baseline). This spec addresses the underlying reliability gaps that surfaced during the audit so that prod data is trustworthy when it does arrive.

---

## 2. Non-goals

- Re-architecting the closure write path. The "ledger row before state save" ordering is preserved — we add a dedup-on-read contract instead of flipping write order.
- Rewriting `crypto_bot.py` or moving it to its own Railway service. We simply unwire it from `entrypoint.sh` here.
- Backfilling production state. Anything operational (regenerating `backtest_baseline.json`, deleting stale local manifest) is captured in Section E as a checklist for the user, not part of the implementation.
- Changing the spread-strategy date-window or HRRR-availability behavior. The failing test is fixed by aligning the test fixture, not by changing the bot.

---

## 3. Issues addressed

| ID | Title                              | Risk if unfixed                                                       |
|----|-------------------------------------|------------------------------------------------------------------------|
| A  | Process supervision in entrypoint.sh | Silent partial failure: one bot dies, the other keeps trading unnoticed |
| B  | Closure / state crash atomicity      | Duplicate ledger rows after SIGKILL → dashboard double-counts PnL      |
| C  | One-shot realized_pnl backfill       | After manual ledger repair, `state.realized_pnl` stays stale forever   |
| D  | `test_all_filters_pass_together` red | Pre-existing failure masks regressions; the test name lies             |
| E  | Operational checklist                | No `backtest_baseline.json` in prod → LiveVsBacktest strip is empty    |

---

## 4. Design

### A. `entrypoint.sh` — fail-fast supervision

**Problem.** Today `entrypoint.sh` does:

```bash
python3 dashboard_server.py &
DASH_PID=$!
trap 'kill $DASH_PID 2>/dev/null || true' EXIT

python3 bot_v2.py    run >> bot_v2.log 2>&1 &  BOT_PID=$!
python3 crypto_bot.py run >> crypto_bot.log 2>&1 & CRYPTO_PID=$!

wait $BOT_PID $CRYPTO_PID
```

`wait $BOT_PID $CRYPTO_PID` returns when **both** terminate, not when either does. If `bot_v2.py` crashes, `crypto_bot.py` (and `dashboard_server.py`) keep running until they too die. Railway sees a healthy container the entire time. The dashboard happily serves stale data for hours.

**Fix.** Replace with a `wait -n` pattern, drop `crypto_bot.py` from the script, and exit non-zero on any child death so Railway restarts the container cleanly:

```bash
set -m

mkdir -p data data/markets

python3 dashboard_server.py >> dashboard.log 2>&1 &
python3 bot_v2.py run       >> bot_v2.log     2>&1 &

# Wait for ANY child to exit. Capture its exit code, kill the rest, exit non-zero
# so Railway notices and restarts the whole container.
wait -n
exit_code=$?
kill 0 2>/dev/null || true
echo "[entrypoint] one of the children exited with code $exit_code; aborting" >&2
exit "$exit_code"
```

Notes:
- `set -m` enables job control so `kill 0` reaches the child group.
- The existing config-from-env block at the top of the script stays unchanged.
- `crypto_bot.py` is removed from the script. The file remains on disk; reviving it later as its own Railway service is a separate task.
- `dashboard.log` is added (currently the dashboard inherits stdout/stderr; redirecting it lets us correlate dashboard crashes via the log file).

**Verification.** Manual smoke test under `bash`:
1. Start the script with both children stubbed to a `sleep 60`.
2. `kill` one of them.
3. Confirm the script exits non-zero within ~1s and the surviving stub is gone.

---

### B. Closure ledger dedup-on-read + recovery test

**Problem.** At every closure site (4 in `bot_v2.py`: lines 808, 820, 974, 1184), the order is:

```python
record_closure(mkt, pos)         # append row to closures.jsonl
apply_closure_to_state(state, p) # mutate in-memory state.realized_pnl, wins/losses
# ... eventually ...
save_state(state)                # flush state.json
```

If the process is SIGKILLed between `record_closure` and `save_state`, the ledger has the row but `state.json` is unchanged. On restart:

1. `load_state()` reads the pre-crash state from disk.
2. The bot scans market files; the crashed-on market still shows position `status="open"` (its on-disk update was tied to the same `save_state` call that never ran).
3. The closure path fires again — appends a **second** row to `closures.jsonl`, mutates state correctly this time, saves.

Net effect: `state.realized_pnl` is correct (one increment), but `closures.jsonl` has two rows for the same closure. Anything reading the ledger directly (Dashboard's `LiveVsBacktest` strip, walkforward analysis) double-counts.

**Fix.** Codify the contract: **`closures.jsonl` is at-least-once; consumers MUST dedup by `(market_id, close_reason)`, keeping the first row.** Rows with a missing `market_id` (legacy / partially-filled data) are passed through unchanged — they are not deduplicated against each other, since the dedup key would collapse them spuriously.

Three changes:

1. **Add a small Python helper** in a new module `closures_io.py`:

   ```python
   def load_closures(path):
       """Read closures.jsonl, parse each line, skip blanks/parse errors,
       return list of dicts."""
       ...

   def dedup_closures(rows):
       """Filter to type == 'closure', dedup by (market_id, close_reason)
       keeping the first occurrence. Reset markers and other types are
       dropped (callers that want them should iterate raw)."""
       ...
   ```

   Used by the startup reconciler (Section C) and by walkforward.

2. **Add a JS helper** in `web/closures.js` (matches the existing module pattern alongside `web/market_transform.js`):

   ```js
   export function dedupClosures(rows) {
     const seen = new Set();
     const out = [];
     for (const r of rows) {
       if (r?.type !== "closure") continue;
       const key = `${r.market_id}|${r.close_reason}`;
       if (seen.has(key)) continue;
       seen.add(key);
       out.push(r);
     }
     return out;
   }
   ```

   `Dashboard.html`'s `LiveVsBacktest` component imports and calls this before computing live win-rate.

3. **Add tests** in `tests/test_closure_dedup.py`:
   - Unit test for `dedup_closures`: input with two rows sharing `(market_id, close_reason)` → output has one row, the first.
   - Unit test for legacy rows: input with two rows lacking `market_id` → both are returned (no spurious collapse).
   - Unit test for non-closure rows: a `type: "reset"` marker is filtered out by `dedup_closures` (callers wanting markers iterate raw).
   - Integration test simulating the crash: write two duplicate rows to a temp `closures.jsonl`, run the startup path (Section C's reconciler), assert `state.realized_pnl` reflects exactly one increment per closure.

**Verification.** New unit tests cover both the dedup helper and the recovery scenario. Existing tests untouched.

---

### C. Replace one-shot realized_pnl backfill with a startup reconciler

**Problem.** `maybe_backfill_realized_pnl` (`bot_v2.py:566`) bails immediately if `state.realized_pnl != 0`. It was designed as a one-shot migration helper. After manual ledger repair (`tools/repair_ledger.py`) or any future closure-ledger change, the field can be stale and the function will never re-run.

**Fix.** Replace with `reconcile_realized_pnl(state)` that runs unconditionally at startup:

```python
def reconcile_realized_pnl(state):
    """Recompute state.realized_pnl from deduped closures.jsonl. If it
    differs from the persisted value by > $0.01, log the drift and overwrite.
    Idempotent. Safe to run on every startup."""
    rows = load_closures(LEDGER_FILE)
    deduped = dedup_closures(rows)
    expected = round(sum((r.get("pnl") or 0.0) for r in deduped), 2)
    actual   = round(state.get("realized_pnl", 0.0), 2)
    if abs(expected - actual) > 0.01:
        print(f"[reconcile] realized_pnl {actual} → {expected} "
              f"(drift detected across {len(deduped)} closures)")
        state["realized_pnl"] = expected
        save_state(state)
```

Called once at startup (`bot_v2.py:1213`) right after `maybe_backfill_ledger()`. The old `maybe_backfill_realized_pnl` function is deleted; its single caller is replaced.

The ordering at startup becomes:
1. `load_state()` — reads from disk
2. `maybe_backfill_ledger()` — if `closures.jsonl` is empty, reconstruct from market files
3. `reconcile_realized_pnl(state)` — recompute from the now-populated ledger and correct state

This also subsumes the original migration scenario (state has `realized_pnl=0` but wins/losses recorded): the ledger is backfilled in step 2, and step 3 sums it.

**Verification.**
- Unit test: state with stale realized_pnl + ledger with known sum → after reconcile, state matches ledger.
- Unit test: idempotent — second call leaves state unchanged.
- Unit test: drift < $0.01 → no write (avoid spurious overwrites from float noise).

---

### D. Fix `test_all_filters_pass_together`

**Root cause** (per memory observations 4347 and 3547): the spread strategy bets on **HRRR's bucket**, not ECMWF's. The test creates a single market for the **70–75°F** bucket. The HRRR mock (`_mock_open_meteo_hrrr`) returns temps `[76, 78, 80]` after the prior bucket-collision fix. HRRR's bucket is therefore 75–80°F. The bot scans, can't find a market matching HRRR's bucket, and never opens a position. Test asserts a position exists → fail.

**Fix.** Smaller blast radius: change the test's market bucket to align with HRRR's mock — i.e. set the market to **75–80°F** with `outcomePrices` that still pass the EV gate, or override the HRRR mock in this single test to return temps inside 70–75°F.

Preferred: override the HRRR mock locally in the test using `monkeypatch.setattr` to return `[72, 73, 74]` for the `_mock_open_meteo_hrrr` helper. This keeps the test's intent (70–75°F bucket position opens cleanly) and isolates the fix to the test file. No source changes.

**Verification.** Run `pytest tests/test_scan_and_update.py::TestFilterInteraction::test_all_filters_pass_together` and the full suite — both green.

---

### E. Operational appendix (no code, user runs these)

These are listed in the spec for completeness and tracked in the implementation plan as a final checklist task, but they don't ship in the code change:

1. **Generate `data/backtest_baseline.json` in production.**
   ```bash
   python walkforward_test.py --emit-baseline --threshold 2.0
   ```
   Copy the output into Railway's `data/` volume. The dashboard's LiveVsBacktest strip is null until this exists.

2. **Locally:** delete the stale `data/manifest.json` (last gen 2026-04-26), or just start `dashboard_server.py` once to refresh it.

3. **Locally:** delete `bot.log` (Apr 19 vintage; `entrypoint.sh` writes to `bot_v2.log`).

4. **Note in commit message** that `crypto_bot.py` is no longer launched by `entrypoint.sh`. Reviving it later requires either re-adding the line or creating a separate Railway service.

---

## 5. Success criteria

- `entrypoint.sh` exits non-zero within 2s of either child dying (verified manually).
- `closures.jsonl` containing duplicate rows produces correct dashboard output (verified by unit test in `web/` and Python).
- `state.realized_pnl` matches `sum(pnl over deduped closures.jsonl)` after startup, even when the persisted value was stale (verified by unit test).
- `pytest` reports **264 passed, 0 failed** (currently 263 passed, 1 failed).
- Total diff is roughly:
  - `entrypoint.sh`: ~15 lines net
  - `closures_io.py` (new): ~40 lines
  - `web/closures.js` (new): ~20 lines
  - `bot_v2.py`: -25/+15 (delete `maybe_backfill_realized_pnl`, add `reconcile_realized_pnl`, swap caller)
  - `Dashboard.html`: ~5 lines (import + call dedup before win-rate calc)
  - `walkforward_test.py`: ~5 lines if it reads the ledger directly (verify; may not need changes)
  - `tests/test_closure_recovery.py` (new): ~80 lines
  - `tests/test_scan_and_update.py`: ~5 lines (HRRR mock override in the failing test)

---

## 6. Risk and rollout

- **No production data is touched.** All changes are code-level; existing `state.json` and `closures.jsonl` files are read with the new dedup contract, which leaves correct data correct.
- **Reconciler may rewrite `state.realized_pnl`** the first time it runs in prod if the current value drifts. This is the intended behavior — and exactly the symptom the user reported.
- **Backwards compatibility:** dedup keeps the *first* occurrence, so older rows win over duplicates appended on restart. This matches the timestamp ordering you'd expect (the first row's `ts` is closer to the actual closure event).
- **Rollout:** single PR. Merge to main; Railway auto-deploys. Watch first restart's logs for `[reconcile] realized_pnl …` lines; if they appear, the drift was real and is now corrected.

---

## 7. Open questions

None at draft time. Section E items are intentionally outside the code change.
