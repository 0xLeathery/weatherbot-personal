# Dashboard-builder prompt

Paste this into a fresh Claude Code session (or any coding agent) to get a reporting dashboard for the weatherbot.

---

## Prompt

I'm running a Polymarket weather paper-trading bot (Python, `bot_v2.py` from `alteregoeth-ai/weatherbot`). It writes its state to a `data/` directory:

- `data/state.json` — running totals: `{balance, starting_balance, total_trades, wins, losses, peak_balance}`
- `data/calibration.json` — per-`(city, source)` learned forecast σ. Keys look like `nyc_ecmwf`, `chicago_hrrr`. Values: `{sigma, n_samples, last_updated}`
- `data/markets/<slug>.json` — one file per market. Each contains: `city`, `city_name`, `date`, `unit` ("F"|"C"), `station`, `event_end_date`, `status` ("open"|"closed"|"resolved"), `position` (entry price, size, stop, take_profit), `actual_temp`, `resolved_outcome` ("win"|"loss"|null), `pnl`, `forecast_snapshots[]` (each: `ts, horizon, hours_left, ecmwf, hrrr, metar, best, best_source`), `market_snapshots[]` (each: `ts, top_bucket, top_price, all_outcomes`).

The bot runs on Railway with the `data/` directory mounted as a persistent volume. I have the Railway CLI authenticated locally and can `railway run cat data/state.json` to grab files, or pull via `railway volume`.

**Build me a single-page reporting dashboard.** Constraints:

1. **Single self-contained `dashboard.html`** — no build step, no npm install, no framework. Vanilla JS + a single `<script>` tag for Chart.js from a CDN is fine.
2. **Data source flexible:** the dashboard should accept a path/URL to a `data/` snapshot. Default to `./data/` (works locally if I rsync the volume contents next to the HTML). Optionally: a "Load snapshot" button that lets me drag-drop a `.tar.gz` of the data dir.
3. **Don't make a server.** This is a paper-trading dashboard, not a live trading UI. A static HTML I open with `file://` or serve with `python -m http.server` is the goal.

**Sections to include (in this order):**

1. **Headline numbers** — Current balance, return %, total trades, win rate, open positions count, unrealized PnL (sum of `(current_price - entry_price) * size` for open positions; pull current price from latest `market_snapshots[]` entry).
2. **Equity curve** — line chart of balance over time. Reconstruct by walking resolved markets sorted by `event_end_date` and applying `pnl` cumulatively starting from `starting_balance`.
3. **Open positions table** — sortable: city, bucket, entry price, latest price, unrealized PnL, hours-to-resolution, source.
4. **Resolved trades table** — last 50 closed/resolved: city, date, bucket, entry, exit/resolution outcome, realized PnL, EV-at-entry vs realized.
5. **Per-city performance** — bar chart of total PnL per city. Color positive green, negative red.
6. **Per-source accuracy** — for ECMWF, HRRR, METAR: count of trades, win rate, average realized EV. (A trade's "source" is the `best_source` from its first `forecast_snapshots` entry.)
7. **Calibration drift** — table of `(city, source) → sigma, n_samples`. Highlight rows where sigma has moved >25% from the defaults (`SIGMA_F=2.0`, `SIGMA_C=1.2`).
8. **EV calibration scatter** — predicted EV at entry (x) vs realized PnL/size (y) for resolved trades. Add a `y=x` reference line. Tells me whether the bot's EV estimates are honest.

**UX bar:**
- Auto-refresh every 60s if the data dir is local.
- Ugly is fine, readable is mandatory. Use system fonts, no Tailwind.
- Show "last updated" timestamp from the most recent `market_snapshots[].ts` across all market files.
- If `data/` is empty or unreadable, show a clear message with the expected path, not a JS error.

**Tech notes:**
- Market files are small (~5–50KB each); 100 of them = a few MB. `fetch()` them in parallel via a manifest. Generate the manifest with a tiny `ls data/markets > manifest.json` step or have the dashboard try a known directory listing trick (or just have me regenerate `manifest.json` whenever I sync data — document that).
- Don't try to query Polymarket's API directly from the dashboard — CORS will bite you. Trust the bot's snapshots.

**Deliverable:** `dashboard.html` (and optionally `manifest.json` regenerator script). Show me how to run it locally (`python3 -m http.server 8000` from the project root, then `localhost:8000/dashboard.html` should pick up `./data/`).

Use TDD if any non-trivial parsing logic, but for layout/styling don't over-engineer.

---

## Notes

- **Where to run this prompt:** anywhere with code-write capability. Claude Code in `/Users/annon/projects/weather/weatherbot/` is ideal — it can read the `data/` dir directly to verify shapes against real files.
- **Sync data from Railway when you want fresh numbers:** `railway run "tar -czf - -C /app/data ." | tar -xzf - -C ./data` from inside the project dir.
- **Iterate:** once the v1 works, ask the same agent for additions like p&l attribution by hour-of-day, or a "what would my PnL be at different `min_ev` thresholds" backtester reading the same JSON.
