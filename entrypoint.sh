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
# Redact secrets before logging so keys don't leak to Railway log history
redacted = {**cfg, "vc_key": ("<set>" if cfg.get("vc_key") else "<empty>")}
print("[entrypoint] wrote config.json:", json.dumps(redacted))
PY
fi

# data/ is a Railway-mounted volume in production; ensure it exists locally too
mkdir -p data data/markets

# Run dashboard + bot under fail-fast supervision: if either child dies,
# kill the other and exit non-zero so Railway restarts the whole container.
# Without this, a silent bot crash leaves the dashboard happily serving
# stale data while no trading happens.
#
# crypto_bot.py is intentionally not launched here. To revive it, either
# add a third `python3 crypto_bot.py run &` line below, or run it as a
# separate Railway service with its own container.
set -m

python3 dashboard_server.py &
python3 bot_v2.py run >> bot_v2.log 2>&1 &

wait -n
exit_code=$?
# Each backgrounded child is in its own process group under `set -m`, so
# kill 0 (parent's pgrp) wouldn't reach them. Signal each job explicitly,
# then drain so we don't leave a zombie behind.
for j in $(jobs -p); do kill "$j" 2>/dev/null || true; done
wait 2>/dev/null || true
echo "[entrypoint] one of the children exited with code $exit_code; aborting" >&2
exit "$exit_code"
