#!/usr/bin/env python3
"""Reconcile state.json balance against the per-market realized-PnL ledger.

The invariant: state["balance"] == state["starting_balance"] + sum(realized_pnl).

Realized pnl is read the same way `tools/backfill_state.py` reads it:
  - If resolved_outcome is set, use market-level pnl.
  - Else if position.status == "closed", use position.pnl.
  - Else the market has no realized pnl (open, or no-position closure) and
    contributes zero.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class LedgerCheck:
    balance_on_disk: float
    balance_from_markets: float
    drift: float
    ok: bool
    starting_balance: float
    realized_count: int
    no_position_count: int
    open_count: int


def _realized_pnl(market: dict):
    """Return (pnl, kind) for a market, where kind in {'resolved','closed','none'}.

    pnl is None when the market contributed nothing to cash flow.
    """
    pos = market.get("position") or {}
    if market.get("resolved_outcome") is not None:
        return market.get("pnl"), "resolved"
    if pos.get("status") == "closed":
        return pos.get("pnl"), "closed"
    return None, "none"


def check_ledger(state: dict, markets) -> LedgerCheck:
    starting = float(state.get("starting_balance", 0))
    balance_on_disk = float(state.get("balance", 0))

    realized_sum = 0.0
    realized_count = 0
    no_position_count = 0
    open_count = 0
    for m in markets:
        pnl, kind = _realized_pnl(m)
        if kind == "none":
            if (m.get("position") or {}).get("status") == "open":
                open_count += 1
            else:
                no_position_count += 1
            continue
        if pnl is None:
            # Market marked closed/resolved but no pnl booked — counts as drift.
            continue
        realized_sum += float(pnl)
        realized_count += 1

    balance_from_markets = round(starting + realized_sum, 2)
    drift = round(balance_on_disk - balance_from_markets, 2)
    return LedgerCheck(
        balance_on_disk=balance_on_disk,
        balance_from_markets=balance_from_markets,
        drift=drift,
        ok=(drift == 0.0),
        starting_balance=starting,
        realized_count=realized_count,
        no_position_count=no_position_count,
        open_count=open_count,
    )


def _load_markets(markets_dir: Path):
    for p in sorted(markets_dir.glob("*.json")):
        try:
            yield json.loads(p.read_text())
        except json.JSONDecodeError:
            continue


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data")
    args = ap.parse_args(argv)
    data = Path(args.data)
    state = json.loads((data / "state.json").read_text())
    markets = list(_load_markets(data / "markets"))
    result = check_ledger(state, markets)
    print(f"starting_balance:    {result.starting_balance:,.2f}")
    print(f"balance_on_disk:     {result.balance_on_disk:,.2f}")
    print(f"balance_from_mkts:   {result.balance_from_markets:,.2f}")
    print(f"drift:               {result.drift:,.2f}")
    print(f"realized markets:    {result.realized_count}")
    print(f"no-position closes:  {result.no_position_count}")
    print(f"open positions:      {result.open_count}")
    print(f"ok:                  {result.ok}")
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
