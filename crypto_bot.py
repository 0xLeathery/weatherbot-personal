#!/usr/bin/env python3
"""
Crypto prediction bot for Polymarket.
Paper-trades crypto price predictions using candle-based signals.
Mirrors bot_v2.py architecture: state management, position tracking, Kelly betting.
"""

import re
import sys
import json
import math
import time
import requests
from datetime import datetime, timezone, timedelta
from pathlib import Path
import subprocess
import signal

# =============================================================================
# PATHS & CONFIG
# =============================================================================

SCRIPT_DIR = Path(__file__).parent
CONFIG_FILE = SCRIPT_DIR / "crypto_config.json"
STATE_FILE = SCRIPT_DIR / "data" / "crypto" / "state.json"
POSITIONS_DIR = SCRIPT_DIR / "data" / "crypto" / "positions"
MANIFEST_FILE = SCRIPT_DIR / "data" / "crypto" / "manifest.json"

(SCRIPT_DIR / "data" / "crypto").mkdir(parents=True, exist_ok=True)
POSITIONS_DIR.mkdir(parents=True, exist_ok=True)

def load_config():
    if CONFIG_FILE.exists():
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    return {
        "balance": 1000.0,
        "max_bet": 20.0,
        "min_ev": 0.08,
        "kelly_fraction": 0.25,
        "max_price": 0.80,
        "min_volume": 1000,
        "scan_interval": 900,
        "candle_interval": "15m",
        "candle_limit": 96,
        "assets": ["BTC", "ETH", "BNB", "SOL", "XRP"]
    }

CONFIG = load_config()
BALANCE = CONFIG["balance"]
MAX_BET = CONFIG["max_bet"]
MIN_EV = CONFIG["min_ev"]
KELLY_FRACTION = CONFIG["kelly_fraction"]
MAX_PRICE = CONFIG["max_price"]
MIN_VOLUME = CONFIG["min_volume"]
SCAN_INTERVAL = CONFIG["scan_interval"]
CANDLE_INTERVAL = CONFIG["candle_interval"]
CANDLE_LIMIT = CONFIG["candle_limit"]
ASSETS = CONFIG["assets"]

# =============================================================================
# MATH (copied from bot_v2.py)
# =============================================================================

def norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

def calc_ev(p, price):
    if price <= 0 or price >= 1:
        return 0.0
    return round(p * (1.0 / price - 1.0) - (1.0 - p), 4)

def calc_kelly(p, price):
    if price <= 0 or price >= 1:
        return 0.0
    b = 1.0 / price - 1.0
    f = (p * b - (1.0 - p)) / b
    return round(min(max(0.0, f) * KELLY_FRACTION, 1.0), 4)

def bet_size(kelly, balance):
    raw = kelly * balance
    return round(min(raw, MAX_BET), 2)

# =============================================================================
# PROBABILITY MODEL
# =============================================================================

def compute_realized_vol(candles):
    """Annualized realized volatility from 15-min candle close prices."""
    if not candles or len(candles) < 2:
        return 0.0
    closes = [float(c[4]) for c in candles]
    returns = [math.log(closes[i] / closes[i-1]) for i in range(1, len(closes))]
    if not returns:
        return 0.0
    variance = sum(r**2 for r in returns) / len(returns)
    vol_per_period = math.sqrt(variance)
    periods_per_year = 365.25 * 24 * 60 / 15
    return vol_per_period * math.sqrt(periods_per_year)

def prob_above(current, target, vol, hours):
    """Log-normal probability that price closes above target in given hours.

    Uses: P(S_T > K) = Φ(d2) where
    d2 = (ln(S/K)) / (σ * sqrt(T))
    (ignoring drift, assuming r=0)
    """
    if current <= 0 or target <= 0 or vol <= 0 or hours <= 0:
        return 0.5
    S = current
    K = target
    T = hours / (365.25 * 24)
    if T <= 0:
        T = 1 / (365.25 * 24)
    d2 = math.log(S / K) / (vol * math.sqrt(T))
    return round(norm_cdf(d2), 4)

# =============================================================================
# CRYPTO DATA FETCHING
# =============================================================================

def fetch_candles(symbol, limit=None):
    """Fetch 15-min candles from Binance public REST API.

    Returns list of [open_time, open, high, low, close, volume, ...].
    Most recent candle last.
    """
    if limit is None:
        limit = CANDLE_LIMIT
    try:
        url = f"https://api.binance.com/api/v3/klines?symbol={symbol}USDT&interval=15m&limit={limit}"
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        candles = resp.json()
        return candles if candles else []
    except Exception as e:
        print(f"Error fetching {symbol} candles: {e}")
        return []

def find_crypto_markets(asset):
    """Find active Polymarket crypto price prediction markets.

    Search Gamma API for markets with 'crypto' tag and asset keyword.
    Return list of market dicts with minimal fields needed.
    """
    try:
        url = "https://gamma-api.polymarket.com/events"
        params = {
            "tag_slug": "crypto",
            "keyword": asset,
            "active": "true",
            "closed": "false",
            "limit": 100
        }
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        events = resp.json()
        if not isinstance(events, list):
            events = events.get("data", [])

        markets = []
        for event in events:
            if not isinstance(event, dict):
                continue
            mkt = {
                "id": event.get("id"),
                "question": event.get("question", ""),
                "resolution_date": event.get("end_date_iso", ""),
                "created_at": event.get("created_at", ""),
            }
            if mkt["id"] and mkt["question"] and mkt["resolution_date"]:
                markets.append(mkt)
        return markets
    except Exception as e:
        print(f"Error finding crypto markets: {e}")
        return []

def parse_market_question(question):
    """Extract target price and side from market question.

    Examples:
    'Will BTC be above $95,000 by April 25?' -> (95000, 'above')
    'ETH price below $2,500 on May 1?' -> (2500, 'below')
    """
    # Look for dollar amounts like $95,000 or $95000
    match = re.search(r'\$[\d,]+', question)
    if match:
        price_str = match.group().replace('$', '').replace(',', '')
        try:
            price = float(price_str)
            side = "above" if "above" in question.lower() else "below"
            return price, side
        except ValueError:
            pass
    return None, None

def get_market_price(market_id):
    """Get YES price of a market from Polymarket CLOB API."""
    try:
        url = f"https://clob.polymarket.com/markets/{market_id}"
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        mkt = resp.json()
        return float(mkt.get("market_price", 0.5))
    except Exception as e:
        print(f"Error fetching market price {market_id}: {e}")
        return 0.5

def hours_to_resolution(resolution_date_iso):
    """Hours from now until resolution datetime."""
    try:
        if resolution_date_iso.endswith('Z'):
            res_dt = datetime.fromisoformat(resolution_date_iso.replace('Z', '+00:00'))
        else:
            res_dt = datetime.fromisoformat(resolution_date_iso)
        now = datetime.now(timezone.utc)
        return max(1, (res_dt - now).total_seconds() / 3600)
    except Exception as e:
        print(f"Error parsing resolution date {resolution_date_iso}: {e}")
        return 24

# =============================================================================
# POSITION STORAGE
# =============================================================================

def position_path(market_id):
    return POSITIONS_DIR / f"{market_id}.json"

def load_position(market_id):
    path = position_path(market_id)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return None

def save_position(position):
    path = position_path(position["market_id"])
    path.write_text(json.dumps(position, indent=2, ensure_ascii=False), encoding="utf-8")

def load_all_positions():
    """Load all position files from positions/ directory."""
    positions = []
    for path in POSITIONS_DIR.glob("*.json"):
        try:
            pos = json.loads(path.read_text(encoding="utf-8"))
            positions.append(pos)
        except Exception as e:
            print(f"Error loading position {path}: {e}")
    return positions

def new_position(symbol, market, target_price, side, entry_price, hours):
    """Create a new position record."""
    now = datetime.now(timezone.utc).isoformat() + "Z"
    return {
        "symbol": symbol,
        "market_id": market["id"],
        "question": market["question"],
        "side": side,
        "target_price": target_price,
        "resolution_date": market["resolution_date"],
        "hours_to_resolution": hours,
        "entry_price": entry_price,
        "shares": 0,
        "cost": 0.0,
        "opened_at": now,
        "status": "open",
        "pnl": None,
        "candle_snapshots": [],
        "price_snapshots": []
    }

# =============================================================================
# STATE (balance and counters)
# =============================================================================

def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {
        "balance": BALANCE,
        "starting_balance": BALANCE,
        "total_trades": 0,
        "wins": 0,
        "losses": 0,
        "peak_balance": BALANCE,
    }

def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")

def apply_closure_to_state(state, pnl):
    """Increment wins/losses for a closed position."""
    if pnl is None:
        return
    if pnl > 0:
        state["wins"] = state.get("wins", 0) + 1
    else:
        state["losses"] = state.get("losses", 0) + 1

# =============================================================================
# SCAN & UPDATE
# =============================================================================

def scan_and_update():
    """Main scan cycle: fetch candles, find markets, evaluate EV, enter/close positions."""
    print(f"\n[{datetime.now(timezone.utc).isoformat()}] Scanning crypto markets...")
    state = load_state()
    balance = state["balance"]

    for asset in ASSETS:
        print(f"\n--- {asset} ---")

        candles = fetch_candles(asset)
        if not candles:
            print(f"No candles for {asset}")
            continue

        current_price = float(candles[-1][4])
        print(f"Current price: ${current_price:,.2f}")

        vol = compute_realized_vol(candles)
        print(f"Realized vol (annual): {vol:.4f}")

        markets = find_crypto_markets(asset)
        print(f"Found {len(markets)} potential markets")

        for market in markets:
            target_price, side = parse_market_question(market["question"])
            if not target_price:
                continue

            hours = hours_to_resolution(market["resolution_date"])
            if hours < 1:
                continue

            market_price = get_market_price(market["id"])
            if market_price < 0.01 or market_price > MAX_PRICE:
                continue

            if side == "above":
                prob = prob_above(current_price, target_price, vol, hours)
            else:
                prob = 1.0 - prob_above(current_price, target_price, vol, hours)

            ev = calc_ev(prob, market_price)
            if ev < MIN_EV:
                continue

            kelly = calc_kelly(prob, market_price)
            size = bet_size(kelly, balance)

            if size < 0.50:
                continue

            shares = size / market_price

            pos = new_position(asset, market, target_price, side, market_price, hours)
            pos["shares"] = round(shares, 2)
            pos["cost"] = round(size, 2)
            pos["realized_vol"] = round(vol, 4)
            pos["prob_model"] = prob
            pos["ev"] = ev

            save_position(pos)
            balance -= size
            state["total_trades"] += 1

            print(f"  OPEN: {market['question'][:60]}")
            print(f"    Price: {market_price:.4f} | Prob: {prob:.2%} | EV: {ev:.4f}")
            print(f"    Size: ${size:.2f} ({shares:.2f} shares)")

    state["balance"] = round(balance, 2)
    state["peak_balance"] = max(state["peak_balance"], balance)
    save_state(state)
    print(f"\nBalance: ${balance:.2f}")

def regen_manifest():
    """Regenerate manifest of all position files (called by server every 60s)."""
    positions = []
    for path in POSITIONS_DIR.glob("*.json"):
        try:
            pos = json.loads(path.read_text(encoding="utf-8"))
            positions.append({
                "symbol": pos["symbol"],
                "market_id": pos["market_id"],
                "status": pos["status"]
            })
        except Exception:
            pass

    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat() + "Z",
        "position_count": len(positions),
        "positions": positions
    }
    MANIFEST_FILE.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

def run_loop():
    """Main loop: scan every SCAN_INTERVAL, regenerate manifest every 60s."""
    print(f"Starting crypto bot loop (scan every {SCAN_INTERVAL}s)...")

    manifest_tick = 0
    try:
        while True:
            try:
                scan_and_update()
            except Exception as e:
                print(f"Scan error: {e}")

            manifest_tick = (manifest_tick + 1) % max(1, 60 // (SCAN_INTERVAL // 60))
            if manifest_tick == 0:
                regen_manifest()

            time.sleep(SCAN_INTERVAL)
    except KeyboardInterrupt:
        print("\nShutting down gracefully...")
        regen_manifest()

def print_status():
    """Print current state and open positions."""
    state = load_state()
    print(f"\n{'='*60}")
    print(f"Balance: ${state['balance']:.2f} (peak: ${state['peak_balance']:.2f})")
    print(f"Trades: {state['total_trades']} | Wins: {state['wins']} | Losses: {state['losses']}")

    positions = load_all_positions()
    open_positions = [p for p in positions if p["status"] == "open"]

    if open_positions:
        print(f"\nOpen positions ({len(open_positions)}):")
        for pos in open_positions:
            print(f"  {pos['symbol']}: {pos['question'][:50]}")
            print(f"    Entry: {pos['entry_price']:.4f} | Size: ${pos['cost']:.2f}")
    else:
        print("\nNo open positions")

# =============================================================================
# CLI
# =============================================================================

if __name__ == "__main__":
    if len(sys.argv) > 1:
        cmd = sys.argv[1]
        if cmd == "run":
            run_loop()
        elif cmd == "scan":
            scan_and_update()
            regen_manifest()
        elif cmd == "status":
            print_status()
        elif cmd == "manifest":
            regen_manifest()
        else:
            print(f"Unknown command: {cmd}")
            print("Usage: crypto_bot.py [run|scan|status|manifest]")
    else:
        print("Usage: crypto_bot.py [run|scan|status|manifest]")
