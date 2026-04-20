#!/usr/bin/env python3
"""One-shot backfill of actual_temp for all past markets missing it.

For every market in data/markets/ where actual_temp is None and status != 'open',
calls the Visual Crossing API to fetch the observed daily high, then sets actual_temp.
Does NOT touch position data, pnl, resolved_outcome, or balance.

Usage:
    python3 tools/backfill_resolution.py --dry-run     # preview only
    python3 tools/backfill_resolution.py               # writes in-place
    python3 tools/backfill_resolution.py --data /tmp/snap
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

# Reuse get_actual_temp from bot_v2
sys.path.insert(0, str(Path(__file__).parent.parent))
from bot_v2 import get_actual_temp


def _load_markets(markets_dir: Path):
    for f in sorted(markets_dir.glob("*.json")):
        try:
            yield f, json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            pass


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    data = Path(args.data)
    markets_dir = data / "markets"

    candidates = [
        (path, mkt)
        for path, mkt in _load_markets(markets_dir)
        if mkt.get("actual_temp") is None and mkt.get("status", "open") != "open"
    ]

    print(f"markets needing actual_temp: {len(candidates)}")
    if not candidates:
        print("nothing to backfill")
        return 0

    filled = 0
    failed = 0
    for path, mkt in candidates:
        city   = mkt["city"]
        date   = mkt["date"]
        temp   = get_actual_temp(city, date)
        status = f"{city} {date}"
        if temp is None:
            print(f"  [MISS]  {status}")
            failed += 1
        else:
            print(f"  [{'DRY' if args.dry_run else 'SET '}]  {status} → {temp}")
            if not args.dry_run:
                mkt["actual_temp"] = temp
                tmp = path.with_suffix(".tmp")
                tmp.write_text(json.dumps(mkt, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
                tmp.replace(path)
            filled += 1
        time.sleep(0.3)

    print(f"\nfilled: {filled}  failed: {failed}  {'(dry-run — no writes)' if args.dry_run else ''}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
