#!/usr/bin/env python3
"""Dashboard HTTP server for weatherbot.

Serves Dashboard.html and the data/ directory over HTTP with optional HTTP
basic auth, and regenerates data/manifest.json every 60s in a background
thread so the in-browser dashboard can enumerate market files.

Designed to run alongside bot_v2.py inside the same Railway container,
sharing /app/data as the canonical state directory.

Env:
  PORT       — listen port (Railway sets this; default 8000)
  DASH_USER  — basic auth username (if set with DASH_PASS, auth required)
  DASH_PASS  — basic auth password
"""
from __future__ import annotations

import base64
import json
import os
import sys
import threading
import time
from datetime import datetime, timezone
from functools import partial
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"

USER = os.environ.get("DASH_USER", "")
PASS = os.environ.get("DASH_PASS", "")
PORT = int(os.environ.get("PORT", "8000"))

# Only paths matching one of these prefixes are served. Everything else 404s.
ALLOWED_PREFIXES = ("/Dashboard.html", "/data/", "/web/", "/favicon.ico")
MANIFEST_INTERVAL = 60  # seconds


def regenerate_manifest() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    markets_dir = DATA_DIR / "markets"
    markets_dir.mkdir(exist_ok=True)
    market_files = sorted(p.name for p in markets_dir.glob("*.json"))
    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "state": "state.json" if (DATA_DIR / "state.json").exists() else None,
        "calibration": "calibration.json" if (DATA_DIR / "calibration.json").exists() else None,
        "markets": market_files,
    }
    (DATA_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2))


def regenerate_crypto_manifest() -> None:
    crypto_dir = DATA_DIR / "crypto"
    crypto_dir.mkdir(exist_ok=True)
    positions_dir = crypto_dir / "positions"
    positions_dir.mkdir(exist_ok=True)
    position_files = sorted(p.name for p in positions_dir.glob("*.json"))
    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "state": "state.json" if (crypto_dir / "state.json").exists() else None,
        "positions": position_files,
    }
    (crypto_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))


def manifest_loop() -> None:
    while True:
        try:
            regenerate_manifest()
            regenerate_crypto_manifest()
        except Exception as e:  # pragma: no cover
            print(f"[dash] manifest regen failed: {e}", file=sys.stderr, flush=True)
        time.sleep(MANIFEST_INTERVAL)


class Handler(SimpleHTTPRequestHandler):
    # Disable directory listings; we never want raw browse access.
    def list_directory(self, path):
        self.send_error(404, "not found")
        return None

    def do_GET(self):
        if not self._authed():
            return self._challenge()
        if self.path in ("/", ""):
            self.path = "/Dashboard.html"
        if not any(self.path.split("?", 1)[0].startswith(p) for p in ALLOWED_PREFIXES):
            self.send_error(404, "not found")
            return
        super().do_GET()

    def do_HEAD(self):
        if not self._authed():
            return self._challenge()
        super().do_HEAD()

    def _authed(self) -> bool:
        if not USER or not PASS:
            return True  # auth disabled
        h = self.headers.get("Authorization", "")
        if not h.startswith("Basic "):
            return False
        try:
            raw = base64.b64decode(h[6:]).decode("utf-8", "ignore")
            u, _, p = raw.partition(":")
            return u == USER and p == PASS
        except Exception:
            return False

    def _challenge(self) -> None:
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="weatherbot"')
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        body = b"authentication required\n"
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def log_message(self, fmt, *args):
        sys.stderr.write(f"[dash] {self.address_string()} {fmt % args}\n")


def main() -> None:
    if not (USER and PASS):
        print("[dash] WARN: DASH_USER/DASH_PASS not both set — server is OPEN", flush=True)
    else:
        print(f"[dash] basic auth enabled for user={USER!r}", flush=True)

    threading.Thread(target=manifest_loop, daemon=True).start()
    handler = partial(Handler, directory=str(ROOT))
    print(f"[dash] listening on 0.0.0.0:{PORT}", flush=True)
    HTTPServer(("0.0.0.0", PORT), handler).serve_forever()


if __name__ == "__main__":
    main()
