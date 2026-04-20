"""Tests for tools/repair_ledger.py."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.repair_ledger import main, repair_state


def _state(balance, starting, peak, **extra):
    return {"balance": balance, "starting_balance": starting, "peak_balance": peak, **extra}


def _mkt(status="closed", pos_pnl=None, pos_status="closed", resolved_outcome=None, cost=0, pnl=None):
    return {
        "status": status,
        "position": None if pos_status is None else {"status": pos_status, "pnl": pos_pnl, "cost": cost},
        "pnl": pnl,
        "resolved_outcome": resolved_outcome,
    }


# --- repair_state unit tests ---

def test_repair_state_sets_balance():
    state = _state(1000.0, 1000.0, 1000.0)
    assert repair_state(state, 850.0)["balance"] == 850.0


def test_repair_state_peak_never_decreases():
    state = _state(1000.0, 1000.0, 1200.0)
    assert repair_state(state, 850.0)["peak_balance"] == 1200.0


def test_repair_state_peak_rises_when_balance_higher():
    state = _state(500.0, 1000.0, 500.0)
    assert repair_state(state, 600.0)["peak_balance"] == 600.0


def test_repair_state_does_not_mutate_original():
    state = _state(1000.0, 1000.0, 1000.0)
    repair_state(state, 850.0)
    assert state["balance"] == 1000.0


# --- main() integration tests ---

def _setup(tmp_path, state_dict, markets):
    markets_dir = tmp_path / "markets"
    markets_dir.mkdir(parents=True)
    (tmp_path / "state.json").write_text(json.dumps(state_dict))
    for i, m in enumerate(markets):
        (markets_dir / f"m{i}.json").write_text(json.dumps(m))
    return tmp_path


def test_main_repairs_drift(tmp_path):
    # balance = 1000 but should be 1000 - 150 = 850 (open reservation)
    data = _setup(tmp_path, _state(1000.0, 1000.0, 1000.0, wins=0, losses=1),
                  [_mkt(status="open", pos_status="open", cost=150)])
    rc = main(["--data", str(data)])
    assert rc == 0
    repaired = json.loads((data / "state.json").read_text())
    assert repaired["balance"] == pytest.approx(850.0)
    assert repaired["wins"] == 0      # untouched
    assert repaired["losses"] == 1    # untouched


def test_main_dry_run_does_not_write(tmp_path):
    data = _setup(tmp_path, _state(1000.0, 1000.0, 1000.0),
                  [_mkt(status="open", pos_status="open", cost=150)])
    rc = main(["--data", str(data), "--dry-run"])
    assert rc == 0
    assert json.loads((data / "state.json").read_text())["balance"] == 1000.0


def test_main_noop_when_already_correct(tmp_path):
    # balance = 1000 + 5 - 150 = 855
    data = _setup(tmp_path, _state(855.0, 1000.0, 1000.0),
                  [_mkt(pos_pnl=5.0),
                   _mkt(status="open", pos_status="open", cost=150)])
    rc = main(["--data", str(data)])
    assert rc == 0
    assert json.loads((data / "state.json").read_text())["balance"] == pytest.approx(855.0)


def test_main_peak_balance_preserved(tmp_path):
    # Prior peak was 1200; new repaired balance is 850 — peak must stay 1200
    data = _setup(tmp_path, _state(1000.0, 1000.0, 1200.0),
                  [_mkt(status="open", pos_status="open", cost=150)])
    main(["--data", str(data)])
    assert json.loads((data / "state.json").read_text())["peak_balance"] == 1200.0


def test_main_open_cost_preserved_in_balance(tmp_path):
    # balance = starting + realized - open_costs = 1000 + 10 - 200 = 810
    data = _setup(tmp_path, _state(500.0, 1000.0, 500.0),
                  [_mkt(pos_pnl=10.0),
                   _mkt(status="open", pos_status="open", cost=200)])
    main(["--data", str(data)])
    repaired = json.loads((data / "state.json").read_text())
    assert repaired["balance"] == pytest.approx(810.0)
