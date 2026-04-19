# weatherbot-personal

Personal fork of [`alteregoeth-ai/weatherbot`](https://github.com/alteregoeth-ai/weatherbot) — a Polymarket weather paper-trading bot — packaged for one-command Railway deploy with a real-time dashboard.

**Live dashboard:** https://weatherbot-production-c11b.up.railway.app

The bot is **paper-trade only**. There is no on-chain code, no wallet, no real money at stake. See the article (`ARTICLE.md`) for why that surprised me, given how the original viral guide framed it.

---

## What's here

| File | Purpose |
|------|---------|
| `bot_v2.py`, `bot_v1.py` | The actual trading logic (unchanged from `alteregoeth-ai/weatherbot`). See `BOT_README.md`. |
| `config.json` | Paper-trade defaults: $1000 starting balance, $20 max bet, 10% min EV. |
| `Dashboard.html` | Single-file React + Babel dashboard. Reads bot data via a manifest. Trader-terminal aesthetic, hand-rolled SVG charts, dark/light themes, Tweaks panel. |
| `dashboard_server.py` | Tiny Python HTTP server that serves `Dashboard.html` + `data/`, regenerates `manifest.json` every 60s. Optional HTTP basic auth via `DASH_USER`/`DASH_PASS`. |
| `entrypoint.sh` | Container start script — generates `config.json` from env vars, spawns dashboard in background, execs the bot. |
| `Procfile`, `requirements.txt`, `runtime.txt`, `.railwayignore` | Railway / Nixpacks build artifacts. |
| `tools/gen_manifest.py` | Writes `data/manifest.json` so the dashboard can enumerate market files in the browser. |
| `tools/sync.sh` | Pulls `/app/data` from the live Railway service into local `./data/` for offline dashboard use. |
| `DASHBOARD_PROMPT.md` | Self-contained prompt I used to generate the dashboard via Claude Design. |
| `ARTICLE.md` | "What the viral guide left out" — analysis of the original article vs what I actually found. |
| `BOT_README.md` | The original bot's README, preserved. |

---

## Run it locally (paper trading, 2 minutes)

```bash
git clone https://github.com/0xLeathery/weatherbot-personal
cd weatherbot-personal

python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# scan-and-loop, hourly. Ctrl-C to stop.
python bot_v2.py run

# in another terminal: open the dashboard
python3 tools/gen_manifest.py
python3 -m http.server 8000
# → http://localhost:8000/Dashboard.html
```

State persists in `data/`. Run `python bot_v2.py status` or `report` for CLI summaries.

A free [Visual Crossing](https://www.visualcrossing.com/sign-up) API key in `config.json` enables the calibration loop (per-`(city, source)` σ tuning from real post-resolution temperatures). Without it, the bot still trades fine using Open-Meteo + METAR forecasts but can't grade itself.

---

## Deploy to Railway

```bash
# from the project root, with the railway CLI authed
railway init -n weatherbot
railway add --service weatherbot \
  --variables "BALANCE=1000" --variables "MAX_BET=20" --variables "MIN_EV=0.10" \
  --variables "MAX_PRICE=0.45" --variables "MIN_VOLUME=500" \
  --variables "KELLY_FRACTION=0.25" --variables "MAX_SLIPPAGE=0.03" \
  --variables "SCAN_INTERVAL=3600"
# add a 1GB volume mounted at /app/data via the Railway dashboard
# (CLI volume create has a known crash bug as of railway 4.40.0)
railway up
railway domain  # provisions a *.up.railway.app URL
```

Set `VC_KEY=your-visualcrossing-key` to enable the learning loop. Set `DASH_USER` and `DASH_PASS` to gate the dashboard with HTTP basic auth (omit both for fully public).

---

## What the original viral guide got wrong

Short version (full version in `ARTICLE.md`):

1. **Hermes Agent isn't doing the trading "self-learning."** The forecast calibration loop (`data/calibration.json`, ~50 lines in `bot_v2.py`) is the actual learning signal. It runs without Hermes.
2. **There is no on-chain code in this bot.** The article's prompts to create a Polygon wallet, fund USDC.e, and approve Polymarket contracts with `max uint256` target functionality that doesn't exist. The bot only places paper bets that update an in-memory balance written to a JSON file.
3. **The article's `python3 bot_v3.py scan` command refers to a file that doesn't exist** in the repo it points you at. The actual entry point is `python bot_v2.py run`.
4. **There is no `mode: live` / `mode: paper` config field.** That was invented.

None of which makes the bot bad — the EV + Kelly + multi-source forecast logic is interesting and well-implemented. It just doesn't need a "self-learning AI agent" wrapper to do what it does.

---

## License

MIT, inherited from upstream.
