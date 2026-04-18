#!/usr/bin/env python3
"""Write data/manifest.json so Dashboard.html can enumerate market files in the
browser (no server-side directory listing needed).

Run after each `tools/sync.sh` (or any time data/ changes locally).
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    data_dir = root / "data"
    markets_dir = data_dir / "markets"

    if not markets_dir.is_dir():
        print(f"error: {markets_dir} does not exist — sync data first", file=sys.stderr)
        return 1

    market_files = sorted(p.name for p in markets_dir.glob("*.json"))

    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "state": "state.json" if (data_dir / "state.json").exists() else None,
        "calibration": "calibration.json" if (data_dir / "calibration.json").exists() else None,
        "markets": market_files,
    }

    out = data_dir / "manifest.json"
    out.write_text(json.dumps(manifest, indent=2))
    print(f"wrote {out} — {len(market_files)} market files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
