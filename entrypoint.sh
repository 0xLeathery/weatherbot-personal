#!/usr/bin/env bash
set -euo pipefail

# Generate config.json from env vars at container start so secrets / tunables
# stay out of git. Only runs if env vars are set; otherwise falls back to the
# committed config.json.
if [ -n "${BALANCE:-}${MAX_BET:-}${MIN_EV:-}${VC_KEY+x}" ]; then
  python3 - <<PY
import json, os, pathlib
cfg_path = pathlib.Path("config.json")
cfg = json.loads(cfg_path.read_text()) if cfg_path.exists() else {}
overrides = {
    "balance":         float(os.environ["BALANCE"])         if "BALANCE"         in os.environ else None,
    "max_bet":         float(os.environ["MAX_BET"])         if "MAX_BET"         in os.environ else None,
    "min_ev":          float(os.environ["MIN_EV"])          if "MIN_EV"          in os.environ else None,
    "max_price":       float(os.environ["MAX_PRICE"])       if "MAX_PRICE"       in os.environ else None,
    "min_volume":      int(os.environ["MIN_VOLUME"])        if "MIN_VOLUME"      in os.environ else None,
    "min_hours":       float(os.environ["MIN_HOURS"])       if "MIN_HOURS"       in os.environ else None,
    "max_hours":       float(os.environ["MAX_HOURS"])       if "MAX_HOURS"       in os.environ else None,
    "kelly_fraction":  float(os.environ["KELLY_FRACTION"])  if "KELLY_FRACTION"  in os.environ else None,
    "max_slippage":    float(os.environ["MAX_SLIPPAGE"])    if "MAX_SLIPPAGE"    in os.environ else None,
    "scan_interval":   int(os.environ["SCAN_INTERVAL"])     if "SCAN_INTERVAL"   in os.environ else None,
    "calibration_min": int(os.environ["CALIBRATION_MIN"])   if "CALIBRATION_MIN" in os.environ else None,
    "vc_key":          os.environ.get("VC_KEY"),
}
for k, v in overrides.items():
    if v is not None:
        cfg[k] = v
cfg_path.write_text(json.dumps(cfg, indent=2))
print("[entrypoint] wrote config.json:", json.dumps(cfg))
PY
fi

# data/ is a Railway-mounted volume in production; ensure it exists locally too
mkdir -p data data/markets

exec python bot_v2.py run
