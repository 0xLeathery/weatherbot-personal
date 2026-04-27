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

python3 dashboard_server.py &
python3 bot_v2.py run >> bot_v2.log 2>&1 &

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
- Dashboard stdout/stderr inherit as before — no new log file. Out of scope for the supervision fix.

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

Two changes (no new modules — both consumers are single-use, so we inline per CLAUDE.md "no abstractions for single-use code"):

1. **Inline a Python helper in `bot_v2.py`**, placed next to `record_closure` (~line 552). Used only by the startup reconciler in Section C; `walkforward_test.py` does not read the ledger and so does not need it.

   ```python
   def _load_closures():
       """Read closures.jsonl as a list of dicts. Skip blank lines and
       JSON parse errors with a warning."""
       if not LEDGER_FILE.exists():
           return []
       rows = []
       for i, line in enumerate(LEDGER_FILE.read_text(encoding="utf-8").splitlines(), 1):
           line = line.strip()
           if not line:
               continue
           try:
               rows.append(json.loads(line))
           except json.JSONDecodeError as e:
               print(f"[closures] skipping malformed line {i}: {e}")
       return rows

   def _dedup_closures(rows):
       """Filter to type == 'closure', dedup by (market_id, close_reason)
       keeping the first occurrence. Rows without market_id pass through
       unchanged (legacy data). Non-closure types (e.g. reset markers)
       are filtered out."""
       seen = set()
       out = []
       for r in rows:
           if r.get("type") != "closure":
               continue
           mid = r.get("market_id")
           if mid is None:
               out.append(r)
               continue
           key = (mid, r.get("close_reason"))
           if key in seen:
               continue
           seen.add(key)
           out.append(r)
       return out
   ```

2. **Inline JS dedup in `Dashboard.html`** next to the existing `closures.jsonl` parser at line 551. Six lines of logic — no new file in `web/`.

   ```js
   // Dedup duplicate closure rows from at-least-once ledger writes.
   // Key: (market_id, close_reason); keep first. Rows missing market_id
   // pass through. Non-closure types (e.g. reset markers) drop out.
   function dedupClosures(rows) {
     const seen = new Set();
     return rows.filter(r => {
       if (r?.type !== "closure") return false;
       if (r.market_id == null) return true;
       const key = `${r.market_id}|${r.close_reason}`;
       if (seen.has(key)) return false;
       seen.add(key);
       return true;
     });
   }
   ```

   `LiveVsBacktest` calls `dedupClosures(rawClosures)` before computing live win-rate.

3. **Add tests** in `tests/test_closure_dedup.py`:
   - Unit test for `_dedup_closures`: input with two rows sharing `(market_id, close_reason)` → output has one row, the first.
   - Unit test for legacy rows: input with two rows lacking `market_id` → both are returned (no spurious collapse).
   - Unit test for non-closure rows: a `type: "reset"` marker is filtered out by `_dedup_closures` (callers wanting markers iterate raw).
   - Integration test simulating the crash: write two duplicate rows to a temp `closures.jsonl`, run the startup path (Section C's reconciler), assert `state.realized_pnl` reflects exactly one increment per closure.

**Verification.** New unit tests cover both the dedup helper and the recovery scenario. Existing tests untouched.

---

### C. Replace one-shot realized_pnl backfill with a startup reconciler

**Problem.** `maybe_backfill_realized_pnl` (`bot_v2.py:566`) bails immediately if `state.realized_pnl != 0`. It was designed as a one-shot migration helper. After manual ledger repair (`tools/repair_ledger.py`) or any future closure-ledger change, the field can be stale and the function will never re-run.

**Fix.** Replace with `reconcile_state_from_ledger(state)` that runs unconditionally at startup. Reconciles **all three** counters that derive from closures: `realized_pnl`, `wins`, `losses`. Reconciling only `realized_pnl` would leave wins/losses stale under the same recovery scenarios.

```python
def reconcile_state_from_ledger(state):
    """Recompute realized_pnl, wins, losses from deduped closures.jsonl.
    If any of the three differ from the persisted value (realized_pnl by
    > $0.01, counters by any amount), log the drift and overwrite. Idempotent.
    Safe to run on every startup."""
    deduped = _dedup_closures(_load_closures())

    expected_pnl    = round(sum((r.get("pnl") or 0.0) for r in deduped), 2)
    expected_wins   = sum(1 for r in deduped if (r.get("pnl") or 0.0) > 0)
    expected_losses = sum(1 for r in deduped if (r.get("pnl") or 0.0) <= 0 and r.get("pnl") is not None)

    actual_pnl    = round(state.get("realized_pnl", 0.0), 2)
    actual_wins   = state.get("wins", 0)
    actual_losses = state.get("losses", 0)

    drift = (
        abs(expected_pnl - actual_pnl) > 0.01
        or expected_wins   != actual_wins
        or expected_losses != actual_losses
    )
    if drift:
        print(f"[reconcile] realized_pnl {actual_pnl} → {expected_pnl}, "
              f"wins {actual_wins} → {expected_wins}, "
              f"losses {actual_losses} → {expected_losses} "
              f"({len(deduped)} deduped closures)")
        state["realized_pnl"] = expected_pnl
        state["wins"]         = expected_wins
        state["losses"]       = expected_losses
        save_state(state)
```

**Caller pattern change** (worth flagging — current code calls `maybe_backfill_realized_pnl(load_state())` and discards the returned/mutated state outside of the inner save). The new pattern explicitly hoists state into the main scope:

```python
# bot_v2.py:1213 (startup)
state = load_state()
maybe_backfill_ledger()
reconcile_state_from_ledger(state)
# state is now authoritative for the rest of startup.
```

Any code path that subsequently calls `load_state()` will pick up the reconciled values from disk anyway, since `reconcile_state_from_ledger` calls `save_state` on drift.

The old `maybe_backfill_realized_pnl` function is deleted; its single caller is replaced.

The ordering at startup becomes:
1. `load_state()` — reads from disk
2. `maybe_backfill_ledger()` — if `closures.jsonl` is empty, reconstruct from market files
3. `reconcile_state_from_ledger(state)` — recompute from the now-populated ledger and correct state

This also subsumes the original migration scenario (state has `realized_pnl=0` but wins/losses recorded): the ledger is backfilled in step 2, and step 3 sums it.

**Verification.**
- Unit test: state with stale realized_pnl + ledger with known sum → after reconcile, state matches ledger.
- Unit test: state with stale wins/losses + ledger with mixed-sign pnl → counters match deduped row counts.
- Unit test: idempotent — second call leaves state unchanged.
- Unit test: drift < $0.01 in pnl AND counters match → no write (avoid spurious overwrites from float noise).

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
  - `bot_v2.py`: -25/+60 (delete `maybe_backfill_realized_pnl`, add `_load_closures` + `_dedup_closures` + `reconcile_state_from_ledger`, swap caller pattern)
  - `Dashboard.html`: ~10 lines (inline `dedupClosures` next to existing closures parser at line 551, call from `LiveVsBacktest` before win-rate calc)
  - `tests/test_closure_dedup.py` (new): ~100 lines
  - `tests/test_scan_and_update.py`: ~5 lines (HRRR mock override in the failing test)
- `walkforward_test.py` is **not** modified — it does not read `closures.jsonl`.

---

## 6. Risk and rollout

- **No production data is touched.** All changes are code-level; existing `state.json` and `closures.jsonl` files are read with the new dedup contract, which leaves correct data correct.
- **The literal symptom (`realized_pnl=0` locally) is not "fixed" by this change.** Local state was reset 2026-04-23 with no closures since; there is nothing to reconcile against. If prod also shows `0`, that's because no positions have closed since the last reset — verify by reading Railway's `data/closures.jsonl` directly. This change *prevents future drift*; it does not backfill historical state.
- **Reconciler may rewrite `state.realized_pnl`** (and wins/losses) the first time it runs in prod if the current value drifts. The drift will be visible in startup logs as `[reconcile] …` lines.
- **Backwards compatibility:** dedup keeps the *first* occurrence, so older rows win over duplicates appended on restart. This matches the timestamp ordering you'd expect (the first row's `ts` is closer to the actual closure event).
- **Branching:** HEAD is currently detached. Plan must start with `git checkout -b <branch> main`.
- **PR shape:** single PR with **four logically separate commits** (one per fix A/B/C/D). Reviewers can read each fix independently. The fixes don't share code paths (A is shell, D is one test, B and C share the dedup helper) so commit-level isolation is straightforward and improves review.
- **Rollout:** Merge to main; Railway auto-deploys. Watch first restart's logs for `[reconcile] …` lines; if they appear, the drift was real and is now corrected.

---

## 7. Open questions

None at draft time. Section E items are intentionally outside the code change.
