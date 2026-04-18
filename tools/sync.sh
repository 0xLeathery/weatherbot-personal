#!/usr/bin/env bash
# Pull /app/data from the live Railway weatherbot service into local ./data,
# then regenerate the manifest the dashboard reads.
#
# Notes
# - tar is built on the running container while the bot may be writing.
#   If you hit JSONDecodeError in the dashboard, just re-run sync.
# - Requires `railway` CLI authed and linked to the weatherbot project.
#
# Usage: bash tools/sync.sh

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

DATA="$ROOT/data"
mkdir -p "$DATA"

echo "[sync] pulling /app/data from Railway → ./data"
TMP=$(mktemp /tmp/wbot-data-XXXXXX.tgz)
trap 'rm -f "$TMP"' EXIT

# Stream tar from the container. railway ssh exec ends after the command finishes.
railway ssh -- "tar -czf - -C /app/data ." > "$TMP"

# Replace local data dir contents (preserves the data dir itself)
rm -rf "$DATA"/markets "$DATA"/state.json "$DATA"/calibration.json "$DATA"/manifest.json 2>/dev/null || true
tar -xzf "$TMP" -C "$DATA"

echo "[sync] regenerating manifest"
python3 "$ROOT/tools/gen_manifest.py"

echo "[sync] done — serve from $ROOT and open Dashboard.html:"
echo "         python3 -m http.server 8000"
echo "         open http://localhost:8000/Dashboard.html"
