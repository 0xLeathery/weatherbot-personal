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
