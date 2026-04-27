# Weatherbot Reliability Fix-ups Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship four narrowly-scoped reliability fixes (process supervision, closure-ledger dedup contract, startup state reconciler, and a failing-test fix) without changing existing behavior beyond what each fix targets.

**Architecture:** Single feature branch off `main`, four logically separate commits (one per fix A/B/C/D). All helpers inline into existing files — no new modules — per CLAUDE.md "no abstractions for single-use code". Frontend dedup goes inline into Dashboard.html next to the existing closures parser. Backend dedup + reconciler go inline into `bot_v2.py` next to `record_closure`.

**Tech Stack:** Python 3.11, pytest, in-browser React (no build step), bash for the entrypoint script. Repo at `/Users/annon/projects/weather/weatherbot`.

**Spec:** `docs/superpowers/specs/2026-04-28-reliability-fixups-design.md`

---

## File Structure

| File | Change | Owner |
|------|--------|-------|
| `entrypoint.sh` | Modify (Task 2) | Fix A |
| `bot_v2.py` | Modify: add `_load_closures`, `_dedup_closures`, `reconcile_state_from_ledger`; delete `maybe_backfill_realized_pnl`; swap startup caller pattern | Fix B + C |
| `Dashboard.html` | Modify: add `dedupClosures` next to `parseClosuresJsonl` (~line 565); call from `LiveVsBacktest` before win-rate/pnl computation (~line 1147) | Fix B |
| `tests/test_closure_dedup.py` | Create | Fix B + C |
| `tests/test_scan_and_update.py` | Modify `test_all_filters_pass_together` to override HRRR mock so its temps fall in the 70-75°F bucket | Fix D |

`walkforward_test.py` is **not** modified — it does not read `closures.jsonl` (verified during spec review).

---

## Task 0: Branch setup

HEAD is currently detached. All work goes on a new feature branch.

**Files:** none (git only)

- [ ] **Step 1: Confirm clean working tree**

```bash
git status
```

Expected: `clean — nothing to commit`. The two spec commits (`c8484b7`, `3d8417c`) should be the latest in `git log --oneline -3`.

- [ ] **Step 2: Create the feature branch from main**

```bash
git checkout -b reliability-fixups main
```

Expected: `Switched to a new branch 'reliability-fixups'`. The detached-HEAD spec commits are *not* on this branch — they need to be cherry-picked or you need to base off the detached HEAD instead.

If `main` doesn't already contain the spec commits (`c8484b7`, `3d8417c`), branch off the detached HEAD instead:

```bash
git checkout -b reliability-fixups
```

- [ ] **Step 3: Verify branch state**

```bash
git log --oneline -5
git branch --show-current
```

Expected: branch is `reliability-fixups` and HEAD includes the two spec commits at the top.

---

## Task 1: Fix D — `test_all_filters_pass_together` (smallest fix, gets baseline green)

We do D first so subsequent task verifications run against a fully-green suite. The failing test creates a market in the 70-75°F bucket but the HRRR mock returns `[76, 78, 80]`, which makes the spread strategy bet on the 75-80°F bucket — a market the test never created. Fix: override the HRRR helper for this one test so its temps fall in 70-75°F.

**Files:**
- Modify: `tests/test_scan_and_update.py:763-781`

- [ ] **Step 1: Confirm the existing failure**

```bash
.venv/bin/pytest tests/test_scan_and_update.py::TestFilterInteraction::test_all_filters_pass_together -v
```

Expected: 1 failed. Last assertion error: `assert saved["position"] is not None` fails because position is `None`.

- [ ] **Step 2: Add the targeted HRRR mock override**

Replace the body of `test_all_filters_pass_together` (currently lines 763-781) with:

```python
    def test_all_filters_pass_together(self, tmp_path, monkeypatch):
        """Volume, spread, price, EV, and size all pass — position opens."""
        import sys
        from unittest.mock import MagicMock

        monkeypatch.setattr("bot_v2.MARKETS_DIR", tmp_path / "markets")
        monkeypatch.setattr("bot_v2.STATE_FILE", tmp_path / "state.json")
        (tmp_path / "markets").mkdir()
        (tmp_path / "state.json").write_text(json.dumps(_make_state()))

        # Override HRRR helper for THIS test only. Default _mock_open_meteo_hrrr
        # returns [76, 78, 80] which puts HRRR's bucket at 75-80°F. Spread
        # strategy bets on HRRR's bucket — but this test only creates a 70-75°F
        # market, so the bot never finds a target. Realign HRRR with the bucket
        # under test.
        def _hrrr_in_70_75():
            dates = [(datetime.now(timezone.utc) + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(3)]
            m = MagicMock()
            m.json.return_value = {"daily": {"time": dates, "temperature_2m_max": [72, 73, 74]}}
            return m
        monkeypatch.setattr(sys.modules[__name__], "_mock_open_meteo_hrrr", _hrrr_in_70_75)

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        event = _make_polymarket_event("dallas", today, markets=[
            {"id": "mkt_1", "question": f"Will the high be between 70-75°F on {today}?", "outcomePrices": "[0.30, 0.32]", "volume": 5000},
        ])
        _setup_api_mocks(monkeypatch, event, best_ask=0.32, best_bid=0.30, only_city="dallas")

        scan_and_update()

        saved = json.loads((tmp_path / "markets" / f"dallas_{today}.json").read_text())
        assert saved["position"] is not None
        assert saved["position"]["status"] == "open"
```

Why `sys.modules[__name__]`: `_setup_api_mocks` looks up `_mock_open_meteo_hrrr` by name at call time. Patching the current test module's attribute is the most robust way regardless of whether pytest imports as `test_scan_and_update` or `tests.test_scan_and_update`.

- [ ] **Step 3: Verify the single test now passes**

```bash
.venv/bin/pytest tests/test_scan_and_update.py::TestFilterInteraction::test_all_filters_pass_together -v
```

Expected: `1 passed`.

- [ ] **Step 4: Verify no regressions across the suite**

```bash
.venv/bin/pytest -q
```

Expected: `264 passed` (was 263 passed, 1 failed).

- [ ] **Step 5: Commit**

```bash
git add tests/test_scan_and_update.py
git commit -m "fix(test): align HRRR mock with 70-75°F bucket in test_all_filters_pass_together

The default _mock_open_meteo_hrrr returns [76, 78, 80] so the spread
strategy bets on the 75-80°F bucket. That test only creates a 70-75°F
market, so no position opens. Override the helper for this one test so
HRRR's bucket aligns with the market under test."
```

Expected: `git log --oneline -1` shows the new commit.

---

## Task 2: Fix A — `entrypoint.sh` fail-fast supervision

Replace the broken `wait $BOT_PID $CRYPTO_PID` pattern with `wait -n` so any child death exits the container with non-zero status. Drop `crypto_bot.py` from the script (its file stays on disk; reviving as its own service is a separate task).

**Files:**
- Modify: `entrypoint.sh`

- [ ] **Step 1: Read the current entrypoint to confirm anchor lines**

```bash
.venv/bin/python -c "print(open('entrypoint.sh').read())"
```

Expected: lines 39-53 contain the dashboard background launch, the `trap`, the `bot_v2.py` and `crypto_bot.py` background launches, and the `wait $BOT_PID $CRYPTO_PID` line.

- [ ] **Step 2: Replace lines 39 onward with the fail-fast supervisor**

Edit `entrypoint.sh`. Keep everything from the start of the file through `mkdir -p data data/markets` unchanged. Replace the section that currently reads:

```bash
# Spawn the dashboard server in the background. It serves Dashboard.html and
# data/ over HTTP basic auth and keeps data/manifest.json fresh. If the bot
# (foreground) exits, the container exits and the dashboard goes with it,
# which is fine — Railway restarts the container.
python3 dashboard_server.py &
DASH_PID=$!
trap 'kill $DASH_PID 2>/dev/null || true' EXIT

python3 bot_v2.py run >> bot_v2.log 2>&1 &
BOT_PID=$!

python3 crypto_bot.py run >> crypto_bot.log 2>&1 &
CRYPTO_PID=$!

wait $BOT_PID $CRYPTO_PID
```

with:

```bash
# Run dashboard + bot under fail-fast supervision: if either child dies,
# kill the other and exit non-zero so Railway restarts the whole container.
# Without this, a silent bot crash leaves the dashboard happily serving
# stale data while no trading happens.
#
# crypto_bot.py is intentionally not launched here. To revive it, either
# add a third `python3 crypto_bot.py run &` line below, or run it as a
# separate Railway service with its own container.
set -m

python3 dashboard_server.py &
python3 bot_v2.py run >> bot_v2.log 2>&1 &

wait -n
exit_code=$?
kill 0 2>/dev/null || true
echo "[entrypoint] one of the children exited with code $exit_code; aborting" >&2
exit "$exit_code"
```

- [ ] **Step 3: Smoke-test the supervisor locally**

The actual `dashboard_server.py` and `bot_v2.py` would touch real network and state. Test the supervision pattern in isolation with stubs:

```bash
cat > /tmp/entry_smoke.sh <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
set -m

(echo "child A starting"; sleep 30; echo "child A clean exit") &
(echo "child B starting"; sleep 5;  exit 7) &

wait -n
exit_code=$?
kill 0 2>/dev/null || true
echo "[entrypoint] exit_code=$exit_code"
exit "$exit_code"
EOF
chmod +x /tmp/entry_smoke.sh
time /tmp/entry_smoke.sh ; echo "outer rc=$?"
```

Expected: prints both "child X starting" lines, after ~5s prints `[entrypoint] exit_code=7` and outer `rc=7`. Total runtime ~5 seconds (NOT ~30 — proves we don't wait for the still-living child).

- [ ] **Step 4: Verify shell syntax with `bash -n`**

```bash
bash -n entrypoint.sh
echo "syntax check exit: $?"
```

Expected: `syntax check exit: 0`.

- [ ] **Step 5: Run the full test suite (sanity)**

```bash
.venv/bin/pytest -q
```

Expected: `264 passed`. (entrypoint.sh isn't covered by Python tests, but we verify no other change leaked in.)

- [ ] **Step 6: Commit**

```bash
git add entrypoint.sh
git commit -m "fix(entrypoint): fail-fast supervision so partial bot crashes restart the container

wait \$BOT_PID \$CRYPTO_PID returns when both children exit, not when
either does. If bot_v2.py crashed, crypto_bot.py and dashboard_server.py
kept running and Railway saw a healthy container while no trading
happened. Switch to set -m + wait -n + kill 0 so any child death
propagates a non-zero exit and Railway restarts cleanly.

crypto_bot.py is no longer launched by this script. Revive by adding the
line back or running it as its own Railway service."
```

---

## Task 3: Fix B — closure ledger dedup helpers + Dashboard inline dedup + tests

Codifies the contract: `closures.jsonl` is at-least-once; consumers MUST dedup by `(market_id, close_reason)` keeping the first row. Rows with `market_id is None` pass through; non-closure types (e.g. reset markers) are filtered.

**Files:**
- Modify: `bot_v2.py` — add `_load_closures` and `_dedup_closures` helpers right after `record_closure` (currently ends at ~line 551)
- Modify: `Dashboard.html` — add `dedupClosures` next to existing `parseClosuresJsonl` (~line 565), call it from `LiveVsBacktest` (~line 1142)
- Create: `tests/test_closure_dedup.py`

- [ ] **Step 1: Write failing tests for the helpers**

Create `tests/test_closure_dedup.py`:

```python
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
```

- [ ] **Step 2: Run the new tests to verify they fail**

```bash
.venv/bin/pytest tests/test_closure_dedup.py -v
```

Expected: every test in `TestDedupClosures` and `TestLoadClosures` fails with `AttributeError: module 'bot_v2' has no attribute '_dedup_closures'` (or `_load_closures`).

- [ ] **Step 3: Add the helpers to `bot_v2.py`**

Open `bot_v2.py`. Find `record_closure` (currently around lines 544-551). Immediately after the closing line of `record_closure` (the line that writes `f.write(json.dumps(row, ensure_ascii=False) + "\n")` then closes the function), insert two new functions before the next `def` (`_closure_pnl` at ~line 554):

```python
def _load_closures():
    """Read closures.jsonl as a list of dicts. Skip blank lines.
    Skip lines that fail JSON parse with a one-line warning."""
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
    """Apply the closures.jsonl at-least-once dedup contract.

    - Filter out non-closure types (reset markers etc.).
    - Dedup by (market_id, close_reason), keeping the first occurrence.
    - Rows with market_id is None pass through unchanged so legacy data
      isn't spuriously collapsed.
    """
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

- [ ] **Step 4: Run the dedup tests, verify pass**

```bash
.venv/bin/pytest tests/test_closure_dedup.py -v
```

Expected: all 8 tests pass.

- [ ] **Step 5: Run the full Python test suite to verify no regression**

```bash
.venv/bin/pytest -q
```

Expected: `272 passed` (264 previous + 8 new).

- [ ] **Step 6: Add the inline `dedupClosures` helper to Dashboard.html**

Open `Dashboard.html`. Find `parseClosuresJsonl` (currently at ~line 552-565). Immediately after the closing `}` of `parseClosuresJsonl` and before `function scopeSinceLastReset(rows)` (~line 567), insert:

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

- [ ] **Step 7: Wire dedup into `LiveVsBacktest`**

In `Dashboard.html`, find `LiveVsBacktest` (currently at ~line 1140). Modify the `useMemo` block at lines 1141-1144 to dedup before scoping:

Replace:

```js
  const data = useMemo(() => {
    const rows = parseClosuresJsonl(closuresText);
    return scopeSinceLastReset(rows);
  }, [closuresText]);
```

with:

```js
  const data = useMemo(() => {
    const rows = parseClosuresJsonl(closuresText);
    // dedup BEFORE scopeSinceLastReset so duplicate rows can never inflate
    // post-reset win/loss/PnL counts.
    const scoped = scopeSinceLastReset(rows);
    return { ...scoped, closures: dedupClosures(scoped.closures) };
  }, [closuresText]);
```

Note: `scopeSinceLastReset` already filters to `type === "closure"`. Calling `dedupClosures` on its output is still correct — `dedupClosures` re-filters by type (defensive), keys by `(market_id, close_reason)`, and is idempotent on already-deduped input.

- [ ] **Step 8: Smoke-test the dashboard change**

The dashboard runs in-browser via Babel; there's no JS test runner. Manual smoke:

```bash
.venv/bin/python dashboard_server.py &
DASH_PID=$!
sleep 1
curl -s http://localhost:8000/Dashboard.html | grep -c "dedupClosures"
kill $DASH_PID 2>/dev/null || true
```

Expected: `2` (one definition, one call). Server output shows no errors.

- [ ] **Step 9: Run the full test suite once more**

```bash
.venv/bin/pytest -q
```

Expected: `272 passed`.

- [ ] **Step 10: Commit**

```bash
git add bot_v2.py Dashboard.html tests/test_closure_dedup.py
git commit -m "feat(closures): dedup-on-read contract for at-least-once ledger writes

closures.jsonl can contain duplicate rows when a crash happens between
record_closure and save_state. On restart, the still-open position is
closed again, producing a second ledger row. State.realized_pnl ends up
correct (one increment), but anything reading the ledger directly was
double-counting.

Codify the contract: consumers dedup by (market_id, close_reason) and
keep the first row. Rows with market_id is None pass through (legacy
data). Non-closure types are filtered.

Implemented inline in bot_v2.py (next to record_closure) and
Dashboard.html (next to parseClosuresJsonl) — no new modules per
CLAUDE.md no-abstractions-for-single-use rule. Helpers used only by
the upcoming startup reconciler and LiveVsBacktest."
```

---

## Task 4: Fix C — startup reconciler replacing one-shot backfill

Replace `maybe_backfill_realized_pnl` (one-shot, bails when `realized_pnl != 0`) with `reconcile_state_from_ledger` (runs every startup, fixes drift in `realized_pnl` AND `wins`/`losses`). Caller pattern changes: state is now hoisted into the main scope and explicitly mutated by the reconciler.

**Files:**
- Modify: `bot_v2.py`
- Modify: `tests/test_closure_dedup.py` (add `TestReconcile` class)

- [ ] **Step 1: Write failing reconciler tests**

Append to `tests/test_closure_dedup.py`:

```python
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
```

- [ ] **Step 2: Run new tests to verify they fail**

```bash
.venv/bin/pytest tests/test_closure_dedup.py::TestReconcileStateFromLedger -v
```

Expected: all 6 tests fail with `AttributeError: module 'bot_v2' has no attribute 'reconcile_state_from_ledger'`.

- [ ] **Step 3: Add `reconcile_state_from_ledger` to `bot_v2.py`**

Open `bot_v2.py`. Find `maybe_backfill_realized_pnl` (currently at ~line 566-591). Replace its entire body — both the function and its docstring — with:

```python
def reconcile_state_from_ledger(state):
    """Recompute realized_pnl, wins, losses from the deduped closure ledger.

    Runs unconditionally at every startup. If the persisted state differs
    from what the ledger says (realized_pnl by > $0.01, counters by any
    amount), log the drift and overwrite. Idempotent: a second call on
    already-reconciled state is a no-op (no save_state, no log line).

    Replaces the older one-shot maybe_backfill_realized_pnl helper, which
    bailed once realized_pnl was non-zero and so couldn't recover from
    drift introduced by manual ledger repair or partial-crash duplicate
    rows.
    """
    deduped = _dedup_closures(_load_closures())

    expected_pnl    = round(sum((r.get("pnl") or 0.0) for r in deduped), 2)
    expected_wins   = sum(1 for r in deduped if (r.get("pnl") or 0.0) > 0)
    expected_losses = sum(1 for r in deduped
                          if r.get("pnl") is not None and (r.get("pnl") or 0.0) <= 0)

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

- [ ] **Step 4: Update the startup caller**

Find the startup section in `bot_v2.py` at ~lines 1211-1214 (look for the comment `# One-shot self-heal:` immediately followed by `maybe_backfill_realized_pnl(load_state())`). Replace those four lines with:

```python
    # Self-healing startup: backfill the ledger from market files if it's
    # empty (post-migration scenario), then reconcile state.realized_pnl /
    # wins / losses against the ledger. Both are idempotent.
    state = load_state()
    maybe_backfill_ledger()
    reconcile_state_from_ledger(state)
```

Note: `state` is now a local variable in the `run()` function. The existing `while True:` loop calls `load_state()` again on each iteration (~line 1227, 1250), so this hoisting only affects the startup banner section — no other call site needs to change. Verify by:

```bash
grep -n "load_state\(\)" bot_v2.py | head -20
```

Expected: every loop-internal `load_state()` call still appears. The reconciler's effect is persisted to disk via `save_state(state)`, so subsequent `load_state()` reads the corrected values.

- [ ] **Step 5: Run reconciler tests, verify pass**

```bash
.venv/bin/pytest tests/test_closure_dedup.py::TestReconcileStateFromLedger -v
```

Expected: all 6 tests pass.

- [ ] **Step 6: Run the full test suite, verify no regression**

```bash
.venv/bin/pytest -q
```

Expected: `278 passed` (272 previous + 6 new).

- [ ] **Step 7: Confirm the deleted symbol no longer exists**

```bash
grep -n "maybe_backfill_realized_pnl" bot_v2.py tests/ 2>&1 || echo "OK — no references"
```

Expected: `OK — no references`. If anything matches, delete it (the only known caller was the startup line we replaced; check there's no leftover doc string elsewhere).

- [ ] **Step 8: Commit**

```bash
git add bot_v2.py tests/test_closure_dedup.py
git commit -m "feat(state): startup reconciler replaces one-shot realized_pnl backfill

maybe_backfill_realized_pnl bailed as soon as realized_pnl was non-zero,
so any post-migration drift (manual ledger repair, duplicate rows from
partial-crash recovery) became permanent. Replace with
reconcile_state_from_ledger which runs unconditionally at every startup
and reconciles realized_pnl AND wins/losses against the deduped ledger.

Caller pattern changes: state is hoisted into run()'s local scope so the
reconciler can mutate it explicitly. Subsequent load_state() calls in the
main loop pick up the persisted reconciled values from disk.

Drift threshold is \$0.01 for realized_pnl (float noise) and exact for the
integer counters. Idempotent — second call is a no-op."
```

---

## Task 5: Final integration verification

Cross-check that all four fixes coexist cleanly.

- [ ] **Step 1: Confirm the four-commit shape**

```bash
git log --oneline main..HEAD
```

Expected: exactly four commits in this order (newest first):
1. `feat(state): startup reconciler …`
2. `feat(closures): dedup-on-read contract …`
3. `fix(entrypoint): fail-fast supervision …`
4. `fix(test): align HRRR mock …`

(Spec commits — `c8484b7`, `3d8417c` — sit *under* these four; if HEAD was detached at branch creation, those are also on the branch.)

- [ ] **Step 2: Run the full test suite one final time**

```bash
.venv/bin/pytest -q
```

Expected: `278 passed, 0 failed`.

- [ ] **Step 3: Confirm no stray edits to `walkforward_test.py`, `dashboard_server.py`, `crypto_bot.py`, or `tools/reset.py`**

```bash
git diff --stat main..HEAD
```

Expected: only these files appear:
- `entrypoint.sh`
- `bot_v2.py`
- `Dashboard.html`
- `tests/test_scan_and_update.py`
- `tests/test_closure_dedup.py`

(plus the two spec docs if branch was based off detached HEAD).

- [ ] **Step 4: Spot-check the dashboard renders without console errors**

```bash
.venv/bin/python dashboard_server.py &
DASH_PID=$!
sleep 1
curl -sf http://localhost:8000/Dashboard.html > /dev/null && echo "dashboard 200 OK"
curl -sf http://localhost:8000/data/manifest.json > /dev/null && echo "manifest 200 OK"
kill $DASH_PID 2>/dev/null || true
```

Expected: both lines print `... 200 OK`. (A real visual check requires a browser. The dashboard's `dedupClosures` call is exercised only when `closures.jsonl` exists; if local data is empty, the rendering path is the same as before.)

- [ ] **Step 5: Open PR (or hand back to user)**

If pushing to a remote:

```bash
git push -u origin reliability-fixups
```

Then either run `gh pr create` with the spec link in the body, or hand control back to the user for them to review locally.

PR body template:

```markdown
## Summary
- **A** entrypoint.sh: fail-fast supervision so partial bot crashes restart the container
- **B** closures.jsonl: dedup-on-read contract (consumers dedup by (market_id, close_reason), keep first)
- **C** state: startup reconciler replaces one-shot realized_pnl backfill, also fixes wins/losses drift
- **D** test: align HRRR mock with 70-75°F bucket in test_all_filters_pass_together

Spec: `docs/superpowers/specs/2026-04-28-reliability-fixups-design.md`

## Heads-up
- crypto_bot.py is no longer launched by entrypoint.sh — this is intentional. Revive by adding the line back or running it as its own Railway service.
- The user's literal "no realized PnL" symptom is NOT backfilled by this change. Local state was reset 2026-04-23 with no closures since; if prod also shows 0, verify by reading Railway's data/closures.jsonl directly. This change prevents future drift; it does not invent historical state.

## Operational follow-ups (user runs these post-merge — see spec §E)
- [ ] Generate `data/backtest_baseline.json` in prod via `python walkforward_test.py --emit-baseline --threshold 2.0`
- [ ] Optional: delete stale local `data/manifest.json` and stale `bot.log` from repo root

## Test plan
- [x] `pytest -q` → 278 passed
- [ ] Smoke-test entrypoint.sh supervisor with stub children (Task 2 Step 3)
- [ ] Browser-load Dashboard.html and confirm no console errors after deploy
```

- [ ] **Step 6: Mark plan complete in TaskList**

Final task in the parent session's tracker.

---

## Self-Review

**Spec coverage check:**
- §4.A (entrypoint.sh) → Task 2 ✓
- §4.B (dedup contract, helpers, recovery test) → Task 3 + Task 4's TestReconcile.test_dedupes_duplicate_rows_before_summing ✓
- §4.C (reconcile_state_from_ledger replacing maybe_backfill_realized_pnl, including wins/losses) → Task 4 ✓
- §4.D (HRRR mock fix in test) → Task 1 ✓
- §4.E (operational checklist) → Task 5 Step 5 PR body, no code ✓
- §5 success criteria (264 → 278 passing, diff size) → Task 5 ✓
- §6 risk/rollout (HEAD-detached, four commits) → Task 0 + Task 5 Step 1 ✓

**Placeholder scan:** none found. Every code step shows the actual code; every command step shows the exact invocation and expected output.

**Type/name consistency:**
- `_load_closures` and `_dedup_closures` defined in Task 3 Step 3, used in Task 4 Step 3 — names match.
- `reconcile_state_from_ledger` defined in Task 4 Step 3, called in Task 4 Step 4 — match.
- `dedupClosures` defined in Task 3 Step 6, called in Task 3 Step 7 — match.
- `LEDGER_FILE` in `bot_v2.py` — already exists at line 56, referenced in `_load_closures` and `record_closure`.

No issues found.
