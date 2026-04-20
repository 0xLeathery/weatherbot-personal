from pathlib import Path
import json
import pytest
from tools.verify_ledger import check_ledger, LedgerCheck


def _m(status="closed", pnl=None, pos_pnl=None, pos_status="closed", resolved_outcome=None, cost=0):
    return {
        "status": status,
        "position": None if pos_status is None else {"status": pos_status, "pnl": pos_pnl, "cost": cost},
        "pnl": pnl,
        "resolved_outcome": resolved_outcome,
    }


def test_ledger_ok_when_balance_matches_reservation_invariant():
    # Invariant: balance = starting + realized - open_costs
    # = 1000 + 5 + 2 - 100 = 907
    state = {"balance": 907.0, "starting_balance": 1000.0, "peak_balance": 1007.0}
    markets = [
        _m(pos_pnl=5.0),            # closed stop-loss: +5
        _m(pnl=2.0, resolved_outcome="win"),  # resolved: +2
        _m(status="open", pos_status="open", pos_pnl=None, cost=100),  # open — reserves $100
    ]
    result = check_ledger(state, markets)
    assert isinstance(result, LedgerCheck)
    assert result.ok is True
    assert result.drift == 0.0
    assert result.balance_on_disk == 907.0
    assert result.balance_from_markets == 907.0
    assert result.open_count == 1
    assert result.open_cost == 100.0


def test_ledger_flags_drift_when_open_costs_ignored():
    # Ledger repaired with 'balance = starting + realized' while positions were
    # still open — the bot's reservation accounting will now diverge.
    state = {"balance": 1000.0, "starting_balance": 1000.0, "peak_balance": 1000.0}
    markets = [
        _m(status="open", pos_status="open", pos_pnl=None, cost=150),
    ]
    result = check_ledger(state, markets)
    assert result.ok is False
    assert result.balance_from_markets == 850.0
    assert result.drift == pytest.approx(150.0)
    assert result.open_cost == 150.0


def test_ledger_drift_surfaces_as_non_ok():
    state = {"balance": 2000.0, "starting_balance": 1000.0, "peak_balance": 2000.0}
    markets = [
        _m(pos_pnl=-5.0),
        _m(pnl=-3.0, resolved_outcome="loss"),
    ]
    # expected balance = 1000 + (-5) + (-3) = 992
    result = check_ledger(state, markets)
    assert result.ok is False
    assert result.balance_from_markets == 992.0
    assert result.drift == pytest.approx(1008.0)


def test_ledger_ignores_no_position_closures():
    """Markets with m.status='closed' but m.position=None have no cash impact."""
    state = {"balance": 1000.0, "starting_balance": 1000.0, "peak_balance": 1000.0}
    markets = [
        {"status": "closed", "position": None, "pnl": None, "resolved_outcome": None},
        {"status": "closed", "position": None, "pnl": None, "resolved_outcome": None},
    ]
    result = check_ledger(state, markets)
    assert result.ok is True
    assert result.balance_from_markets == 1000.0


def test_ledger_prefers_market_pnl_for_resolved_else_position_pnl():
    state = {"balance": 1010.0, "starting_balance": 1000.0, "peak_balance": 1010.0}
    markets = [
        # stop-loss path: position.pnl is authoritative
        {"status": "closed", "position": {"status": "closed", "pnl": 4.0, "cost": 50}, "pnl": None, "resolved_outcome": None},
        # resolution path: m.pnl is authoritative
        {"status": "closed", "position": {"status": "closed", "pnl": None, "cost": 50}, "pnl": 6.0, "resolved_outcome": "win"},
    ]
    result = check_ledger(state, markets)
    assert result.ok is True
    assert result.balance_from_markets == 1010.0
