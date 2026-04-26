#!/usr/bin/env python3
"""Reset the bot's local state and append a session boundary to the ledger.

Replaces the manual 'rm data/state.json && rm data/markets/*.json' procedure.
The reset marker is written to data/closures.jsonl BEFORE any deletions so
that the ledger always preserves the session boundary, even if step 4 or 5
fails.

Usage:
  python tools/reset.py [--yes] [--dry-run] [--starting-balance N] [--note STR]

Flags:
  --yes                Skip the interactive confirmation prompt.
  --dry-run            Print the planned actions and exit without changes.
  --starting-balance   Override the marker's starting_balance.
  --note               Free-form note to store on the marker.

starting_balance precedence:
  --starting-balance > data/state.json["starting_balance"] > config.json["balance"]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


def resolve_starting_balance(cwd: Path, override: float | None) -> float:
    if override is not None:
        return override
    state_path = cwd / "data" / "state.json"
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            sb = state.get("starting_balance")
            if isinstance(sb, (int, float)):
                return float(sb)
        except (json.JSONDecodeError, OSError):
            pass
    config_path = cwd / "config.json"
    if config_path.exists():
        try:
            cfg = json.loads(config_path.read_text(encoding="utf-8"))
            bal = cfg.get("balance")
            if isinstance(bal, (int, float)):
                return float(bal)
        except (json.JSONDecodeError, OSError):
            pass
    raise SystemExit(
        "starting_balance could not be resolved. Pass --starting-balance N."
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Reset bot state with ledger marker.")
    parser.add_argument("--yes", action="store_true", help="Skip confirmation prompt.")
    parser.add_argument("--dry-run", action="store_true", help="Print planned actions, don't write.")
    parser.add_argument("--starting-balance", type=float, default=None,
                        help="Override marker starting_balance (else read from state.json or config.json).")
    parser.add_argument("--note", type=str, default="", help="Free-form note on the marker.")
    args = parser.parse_args()

    cwd = Path.cwd()
    data = cwd / "data"
    markets = data / "markets"
    state = data / "state.json"
    ledger = data / "closures.jsonl"

    starting_balance = resolve_starting_balance(cwd, args.starting_balance)

    market_files = sorted(markets.glob("*.json")) if markets.exists() else []

    print(f"Reset plan:")
    print(f"  ledger marker    → {ledger} (starting_balance={starting_balance}, note={args.note!r})")
    print(f"  delete state     → {state} ({'exists' if state.exists() else 'not present'})")
    print(f"  delete markets   → {len(market_files)} file(s) under {markets}")

    if args.dry_run:
        print("--dry-run set; exiting without changes.")
        return 0

    if not args.yes:
        resp = input("Proceed? [y/N] ").strip().lower()
        if resp not in ("y", "yes"):
            print("aborted.")
            return 1

    data.mkdir(exist_ok=True)
    marker = {
        "type":             "reset",
        "ts":               datetime.now(timezone.utc).isoformat(),
        "starting_balance": starting_balance,
        "note":             args.note,
    }
    with open(ledger, "a", encoding="utf-8") as f:
        f.write(json.dumps(marker, ensure_ascii=False) + "\n")
    print(f"  ✓ wrote reset marker to {ledger}")

    if state.exists():
        state.unlink()
        print(f"  ✓ deleted {state}")

    for f in market_files:
        f.unlink()
    if market_files:
        print(f"  ✓ deleted {len(market_files)} market file(s)")

    print("done. Restart the bot.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
