#!/usr/bin/env python3
"""Repair state.json balance using the reservation-accounting invariant.

    balance = starting_balance + Σ(realized_pnl) − Σ(open_position_costs)

Does NOT touch wins/losses/total_trades — use tools/backfill_state.py for
those counters. The two tools compose: run repair_ledger first, then
backfill_state if win-rate KPIs also need fixing.

Usage:
    python3 tools/repair_ledger.py --dry-run         # preview only
    python3 tools/repair_ledger.py                   # writes data/state.json
    python3 tools/repair_ledger.py --data /tmp/snap  # use alternate data dir
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    from tools.verify_ledger import check_ledger, _load_markets
except ImportError:
    from verify_ledger import check_ledger, _load_markets  # type: ignore[no-redef]


def repair_state(state: dict, balance_from_markets: float) -> dict:
    """Return a copy of state with balance (and peak_balance if needed) corrected."""
    new_state = dict(state)
    new_state["balance"] = balance_from_markets
    new_state["peak_balance"] = max(float(state.get("peak_balance", 0)), balance_from_markets)
    return new_state


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--state-out", default=None, help="write to this path instead of data/state.json")
    args = ap.parse_args(argv)

    data = Path(args.data)
    state_path = data / "state.json"
    out_path = Path(args.state_out) if args.state_out else state_path

    state = json.loads(state_path.read_text())
    markets = list(_load_markets(data / "markets"))
    check = check_ledger(state, markets)

    print(f"starting_balance:    {check.starting_balance:,.2f}")
    print(f"balance_on_disk:     {check.balance_on_disk:,.2f}")
    print(f"balance_from_mkts:   {check.balance_from_markets:,.2f}")
    print(f"drift:               {check.drift:,.2f}")
    print(f"open positions:      {check.open_count}  (reserved cost ${check.open_cost:,.2f})")

    if check.ok:
        print("ok: already clean — nothing to repair")
        return 0

    proposed_peak = max(float(state.get("peak_balance", 0)), check.balance_from_markets)
    print(f"\nproposed balance:    {check.balance_from_markets:,.2f}  (delta {check.balance_from_markets - check.balance_on_disk:+,.2f})")
    print(f"proposed peak:       {proposed_peak:,.2f}")

    if args.dry_run:
        print("\n[dry-run] no changes written")
        return 0

    new_state = repair_state(state, check.balance_from_markets)
    tmp = out_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(new_state, indent=2, ensure_ascii=False) + "\n")
    tmp.replace(out_path)

    state2 = json.loads(out_path.read_text())
    post = check_ledger(state2, markets)
    if post.ok:
        print("\nrepaired: drift is now 0.00")
        return 0
    print(f"\nWARNING: post-repair drift {post.drift:,.2f} — investigate")
    return 1


if __name__ == "__main__":
    sys.exit(main())
