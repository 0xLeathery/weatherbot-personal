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
