# X thread (companion to ARTICLE.md)

12 tweets. Numbered. Rough character counts noted. Replace `[link]` with the X long-form article URL once posted, or with the GitHub repo URL.

---

**1/** A guide making the rounds claims you can build a "self-learning AI" Polymarket weather trading bot in 30 mins by gluing Hermes Agent to an open-source weatherbot. Headline P&L: $300 → $219K, $1.2K → $7.3M.

I built it. The bot is real. The framing is misleading. Here's the audit. 🧵

---

**2/** First thing I did: actually read `bot_v2.py` instead of the prompts.

The "self-learning" they keep crediting to Hermes is a ~50-line classical calibration loop INSIDE the bot itself. It updates per-city, per-forecast-source σ from real post-resolution temperatures.

It runs without Hermes.

---

**3/** Hermes' actual job in the stack is operational scaffolding — a natural-language wrapper that lets you say "clone the repo, set up the venv, send me a Telegram alert" instead of typing the commands.

Useful! But it's not the trading edge. It's the deploy assistant.

---

**4/** Bigger thing the guide doesn't tell you: **the bot has zero on-chain code.**

I grep'd it. No `ClobClient`, no `web3`, no `eth_account`, no `place_order`, no key signing, no `.env` parsing. It only makes read-only HTTP GETs to Open-Meteo, METAR, Polymarket Gamma (read-only), and Visual Crossing.

---

**5/** Yet the guide instructs you to:
• Generate a Polygon private key
• Fund it with USDC.e
• Send `approve(max uint256)` to 3 Polymarket contracts + `setApprovalForAll` on the conditional tokens contract

These approvals target functionality that doesn't exist in the repo they point you at. They will not enable trading. They will sit there.

---

**6/** Smaller things the guide gets wrong:

• Tells you `python3 bot_v3.py scan` — there is no `bot_v3.py`. Actual command: `python bot_v2.py run`.
• Invents a `mode: live | paper` config field. Doesn't exist. The bot is paper-only by design — its own docstring says so.
• Headline P&L claims have referral params on every link.

---

**7/** What the bot ACTUALLY does, summarized:

Fetch ECMWF + HRRR forecasts → fetch live METAR near resolution → match a Polymarket "highest temp" bucket → if EV ≥ 10%, open a position sized via fractional Kelly → monitor with stop-loss + trailing stop + take-profit.

That's solid. Not magic.

---

**8/** And the actual learning loop:

When a market resolves, fetch the actual observed temperature from Visual Crossing. Compare to forecast. Update `data/calibration.json` with new σ for `(city, source)`. After 30+ resolutions per pair, the bot uses learned σ instead of hardcoded defaults (2.0°F / 1.2°C).

Bayesian. Classical. Works.

---

**9/** I deployed it without any of the Hermes/VPS/wallet stuff:

• Forked the repo
• Wrote a Procfile + entrypoint + env-var-driven config
• Deployed to Railway (~$2/mo) with a 1GB volume for state
• Built a single-file React dashboard on top
• Put the dashboard on a public URL

Total elapsed: a few hours. Total real-money risk: $0.

---

**10/** Live dashboard (paper trades, real Polymarket prices, no auth, click around): https://weatherbot-production-c11b.up.railway.app

KPI strip, equity curve, open positions, resolved trades w/ EV calibration, per-city PnL, drawdown, source accuracy, σ drift table.

---

**11/** Repo (MIT, fork freely): https://github.com/0xLeathery/weatherbot-personal

Includes the bot (credit upstream: alteregoeth-ai/weatherbot), Railway deploy artifacts, the dashboard server, and the dashboard itself. README has the run-it-locally one-liner.

---

**12/** Net take:

The bot's idea is interesting. The Open-Meteo + METAR + Visual Crossing combo is well-chosen. EV-gated Kelly sizing is sound. The questions it sets up — are these markets actually mispriced? — are worth answering.

You don't need an "AI agent" to ask them. Full write-up: [link]

---

## Posting notes

- Pin tweet 10 (dashboard link) as a quote-reply to amplify
- The repo link in tweet 11 + dashboard link in tweet 10 are the two pieces of "proof" — make sure both work before posting
- Screenshot tweet candidates: dashboard KPI strip, dashboard equity curve once a few resolutions land (24-48h after first scan)
- Don't engage with bad-faith replies. Engage with technical critiques.
- If accused of being negative on Hermes: be clear that you think Hermes is a real product with real value, just not for THIS workflow as described
