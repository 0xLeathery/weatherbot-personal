#!/usr/bin/env python3
"""One-shot: recompute state.wins / state.losses from data/markets/.

Run after upgrading bot_v2.py so the dashboard's Win-rate KPI reflects
the full history rather than only closures recorded after the upgrade.

Usage:
    python3 tools/backfill_state.py            # writes data/state.json
    python3 tools/backfill_state.py --dry-run  # prints counts only
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def recompute_state_counters(markets):
    """Return (wins, losses) computed from an iterable of market dicts.

    A market counts toward the tally only if its position has closed
    (status != "open") and has a numeric pnl. Wins are pnl > 0; ties
    and losses are counted as losses.
    """
    wins = losses = 0
    for m in markets:
        pos = m.get("position") or {}
        if pos.get("status") == "open":
            continue
        pnl = m.get("pnl") if m.get("resolved_outcome") is not None else pos.get("pnl")
        if pnl is None:
            continue
        if pnl > 0:
            wins += 1
        else:
            losses += 1
    return wins, losses


def _load_markets(markets_dir: Path):
    for p in sorted(markets_dir.glob("*.json")):
        try:
            yield json.loads(p.read_text())
        except json.JSONDecodeError:
            continue


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data", help="data/ directory (default: ./data)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    data = Path(args.data)
    state_path = data / "state.json"
    state = json.loads(state_path.read_text())
    wins, losses = recompute_state_counters(_load_markets(data / "markets"))
    print(f"backfill: wins={wins} losses={losses} (was wins={state.get('wins',0)} losses={state.get('losses',0)})")
    if args.dry_run:
        return
    state["wins"] = wins
    state["losses"] = losses
    state_path.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
