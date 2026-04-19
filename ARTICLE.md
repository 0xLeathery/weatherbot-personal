# I followed a viral 2.5M-view guide for building a "self-learning" Polymarket weather bot. Here's what they didn't tell you.

A guide making the rounds promises you can build a self-learning Polymarket weather trading bot in 30 minutes by stringing together [Hermes Agent](https://x.com/NousResearch) and an open-source weatherbot. It's wrapped in stories about bots that turned $300 into $219K, $1.2K into $7.3M, and a 99.3% win rate. The guide is presented as the recipe to build something similar.

I built it. The bot itself is fine — actually pretty interesting. The framing is misleading enough to be worth correcting publicly, because real people are about to wire `approve(max uint256)` USDC transactions to contracts they didn't audit, in service of a workflow that doesn't need them.

**TL;DR:** The "self-learning" is a 50-line classical calibration loop inside `bot_v2.py`. It works without Hermes. The bot has zero on-chain code. The prompts in the guide that have you create a wallet, fund USDC.e, and approve three Polymarket contracts target functionality that doesn't exist in the repo the guide points you at.

Code, deployed instance, and live dashboard:
- Repo: https://github.com/0xLeathery/weatherbot-personal
- Live paper-trading dashboard: https://weatherbot-production-c11b.up.railway.app

---

## What the guide actually delivers

Stripping out the framing, here's what the article walks you through:

1. Provision a Hetzner VPS
2. Install Hermes Agent (the open-source AI agent from Nous Research)
3. Connect Hermes to Telegram so you can talk to it from your phone
4. Send Hermes a series of prompts that:
   - Clone `alteregoeth-ai/weatherbot` and set up a Python venv
   - Generate a new Polygon wallet using `eth_account`
   - Fund it with USDC.e + POL for gas
   - Sign on-chain `approve(max uint256)` transactions for three Polymarket contracts plus a `setApprovalForAll` on the conditional tokens contract
   - Configure the bot with a Visual Crossing key
   - Run it

After that you're told: *"witness the magic of a self-learning weather trading agent happening right in front of you."*

It's clean copy. It implies the magic is in Hermes. It isn't.

---

## What's actually doing the learning

The `alteregoeth-ai/weatherbot` repo contains `bot_v2.py`. Read it (it's a single file). The pipeline:

1. For each of 20 cities, fetch temperature forecasts from [Open-Meteo](https://open-meteo.com) — it serves both ECMWF (European model, global) and HRRR (NOAA high-resolution, US-only). No API key required.
2. For markets within ~6 hours of resolution, fetch live [METAR](https://aviationweather.gov) observations from the city's airport station (KORD for Chicago, LTAC for Ankara, EGLC for London, etc.). Also no key.
3. Pull the matching Polymarket "highest temperature in {city} on {date}" market from `gamma-api.polymarket.com`. Compute Expected Value (EV) using the forecast as `p` and the current ask as the entry price.
4. If EV exceeds a threshold (10% by default), open a position sized via fractional Kelly Criterion (25% Kelly by default).
5. Monitor every 10 minutes. Apply stop-loss (−20%), trailing stop to breakeven at +20%, and time-based take-profits.
6. When a market resolves, look up the **actual** observed temperature using [Visual Crossing](https://www.visualcrossing.com)'s historical API, compare against the forecast that was made, and update `data/calibration.json` with a per-`(city, source)` σ that reflects how accurate that forecast model has been for that city. Once a `(city, source)` pair has 30+ resolutions, the bot uses the learned σ instead of the hardcoded defaults (2.0°F / 1.2°C).

That's the learning loop. It's about 50 lines. The bot is "self-learning" in exactly the same sense that a Bayesian σ estimator is self-learning. Which is to say: it is, but call it what it is.

**Hermes' contribution to that loop is zero.**

What Hermes *does* do is make it easier to operate the bot via natural language ("clone the repo, write the env file, send me a Telegram alert when it opens a position"). And after enough operations on a similar task, Hermes writes a "skill" file that captures the procedure so it gets faster at *running deployments next time*. That's meta-learning about your workflow patterns, not about weather. Useful! Not the trading edge.

---

## The bot has no on-chain code

This is the part that genuinely concerned me.

Prompts 2–4 of the guide instruct Hermes to:

- Generate a Polygon private key with `eth_account`
- Save it to `weatherbot/.env`
- Send `approve(max uint256)` transactions for USDC.e to the CTF Exchange, Neg Risk Exchange, and Router
- Send `setApprovalForAll` to the Conditional Tokens contract for those three spenders

`approve(max uint256)` means "let this contract spend any amount of my USDC.e, forever, without further approval." This is a normal pattern for DEX integrations. It's also the largest possible blast radius if any of those four contracts is ever upgraded badly, exploited, or your private key leaks.

I grep'd `bot_v2.py` for `ClobClient`, `web3`, `Web3`, `eth_account`, `private_key`, `place_order`, `post_order`, `signing`, `.env`. The bot uses none of them. The only network calls it makes are read-only HTTP `GET`s to Open-Meteo, Aviation Weather, Polymarket Gamma (a *read* API), and Visual Crossing.

The bot's idea of "trading" is updating a Python `balance` variable and writing it to `data/state.json`. The "trades" you see in the dashboard are fully simulated against real-time Polymarket prices — but no on-chain transaction is ever signed.

So the wallet creation, the funding, the four `approve` transactions — they're targeting functionality that doesn't exist in the bot the guide points you at. They will not make the bot start placing real orders. They will only sit there, waiting to do nothing useful.

(Could the bot be modified to actually trade? Sure. But that's a different bot — one that would need an audit before you sign approvals to it.)

---

## Other small things that don't survive verification

- The guide tells you to run `python3 bot_v3.py scan`. There is no `bot_v3.py` in the repo — only `bot_v1.py` and `bot_v2.py`. The actual command is `python bot_v2.py run`. The README in the repo references `weatherbet.py` as the "current" bot, but that filename also doesn't exist; `bot_v2.py` is what's there.
- The guide invents a `mode: live` / `mode: paper` config field. The actual `config.json` has no such field. The bot is paper-only by design (its own docstring says so).
- The headline P&L claims (ColdMath $300 → $219K, RN1 $1.2K → $7.3M) are linked via `?via=following` referral parameters. Those wallets exist; their actual P&L distributions are not the headline numbers.

These are quibbles individually. Together with the wallet/approval section, the pattern is: copy the article structure, embellish the math, point at a real repo that doesn't quite do what you said it does.

---

## What I built instead

Same bot, no Hermes, no wallet, no Hetzner, $0 risk:

- Cloned the repo
- Wrote a Procfile, requirements.txt, runtime.txt, and an env-var-driven entrypoint
- Deployed to Railway with a 1GB persistent volume mounted at `/app/data`
- Added a Visual Crossing API key so the calibration loop can run
- Built a single-file React + Babel dashboard (`Dashboard.html`) that reads bot state from JSON and renders KPIs, equity curve, open/resolved positions, per-city PnL, source accuracy, calibration drift, EV scatter, and a drawdown chart
- Bundled a tiny Python HTTP server alongside the bot in the same container so the dashboard is reachable on a public URL

Total elapsed: a few hours. Total cost: under $5/month on Railway, $0 on the Visual Crossing free tier. Total real-money risk: $0.

Everything is in the repo. The dashboard is live (link above). The 21 paper trades the bot has open at time of writing will resolve over the next 24-48h; come back then to see how it actually performs.

---

## What's actually worth your time in this stack

Not because the bot is going to make you $1.2K → $7.3M. Almost certainly not. But because the questions it asks are genuinely interesting:

1. **Are weather prediction-market prices systematically mispriced relative to free public-model forecasts?** The bot's whole thesis. The honest way to test this is to paper-trade for a few weeks and look at realized PnL net of fees and slippage.
2. **Does fractional Kelly with EV gating actually work better than fixed sizing?** Easy backtest once you have enough resolved trades. The bot doesn't ship one but the data is all there.
3. **How much does using airport-station coordinates instead of city-center coordinates matter?** The README claims 3-8°F difference on markets with 1°F buckets. That's testable.
4. **Does forecast calibration actually improve win rate, or is the cold-start σ already close enough?** Compare hit rate per `(city, source)` before vs after the bot has tuned σ.

These are the experiments the bot is set up for. None of them require a wallet, USDC, or Hermes.

---

## The actually-useful checklist if you want to try this

1. Don't `approve(max uint256)` to anything you didn't audit, ever. Especially not on a wallet you funded with money you'd miss.
2. If a guide tells you to do something on-chain in service of "AI" doing the trading, read the actual repo and confirm the on-chain code exists.
3. Paper-trade for at least a few weeks before risking real capital on any of these strategies. The bot's `state.json` is the source of truth — don't trust the headline numbers in any thread, including this one.
4. If you want to skip the VPS + Hermes layer entirely: clone the repo above, run `python bot_v2.py run`, point your browser at `Dashboard.html`. You're done.

---

The bot is real. The approach is real. The marketing wrapping it is not. Both can be true.

Code: https://github.com/0xLeathery/weatherbot-personal
Live dashboard: https://weatherbot-production-c11b.up.railway.app
