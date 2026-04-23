#!/usr/bin/env python3
"""
Walk-forward backtest for weather forecasting strategy.

Tests strategy on held-out chronological data:
- Train period: used for calibration (if any)
- Test period: never seen during strategy development

Strategy: When ECMWF < HRRR by > 2°, go LONG at ECMWF price.
PnL = actual_temp - ecmwf (long wins if temp rises above ECMWF prediction)

Usage:
    python3 walkforward_test.py
    python3 walkforward_test.py --train-start 2025-12-20 --train-end 2026-01-31
    python3 walkforward_test.py --split 0.9
"""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from collections import defaultdict
import math


def load_markets(data_dir: Path = Path("data/markets")) -> dict:
    """Load all market JSON files."""
    markets = {}
    for f in data_dir.glob("*.json"):
        try:
            data = json.loads(f.read_text())
            if data.get("actual_temp") and data.get("forecast_snapshots"):
                markets[f.stem] = data
        except (json.JSONDecodeError, OSError):
            pass
    return markets


def group_by_date(markets: dict) -> dict:
    """Group markets by date."""
    by_date = defaultdict(list)
    for name, data in markets.items():
        parts = name.rsplit("_", 1)
        if len(parts) == 2:
            by_date[parts[1]].append((parts[0], data))
    return dict(sorted(by_date.items()))


def run_walkforward(
    by_date: dict,
    split: float = 0.8,
    spread_threshold: float = 2.0,
    use_calibration: bool = False,
    train_calib_sources: dict = None,
) -> dict:
    """
    Run walk-forward backtest.
    
    Args:
        by_date: Markets grouped by date
        split: Train/test split ratio (0.8 = 80% train)
        spread_threshold: Enter when HRRR - ECMWF > threshold
        use_calibration: Whether to use sigma for tiebreaker
        train_calib_sources: Pre-calculated sigma from train period
    
    Returns:
        Dict with train/test results
    """
    dates = list(by_date.keys())
    split_idx = int(len(dates) * split)
    train_dates = dates[:split_idx]
    test_dates = dates[split_idx:]
    
    results = {
        "train": {"trades": [], "stats": {}},
        "test": {"trades": [], "stats": {}},
    }
    
    # Calculate sigma from train period (always, for export)
    sigma_errors = defaultdict(list)
    for date in train_dates:
        for city, data in by_date.get(date, []):
            actual = data.get("actual_temp")
            if not actual:
                continue
            for fc in data.get("forecast_snapshots", []):
                for src in ["ecmwf", "hrrr"]:
                    pred = fc.get(src)
                    if pred:
                        sigma_errors[city + "_" + src].append(pred - actual)
    sigma = {
        k: sum(abs(e) for e in errors) / len(errors)  # MAE to match bot_v2.py
        for k, errors in sigma_errors.items()
        if len(errors) >= 3
    }
    if train_calib_sources:
        sigma = train_calib_sources
    
    # Run on both periods
    for period_name, period_dates in [("train", train_dates), ("test", test_dates)]:
        trades = []
        for date in period_dates:
            for city, data in by_date.get(date, []):
                snapshots = data.get("forecast_snapshots", [])
                if not snapshots:
                    continue
                
                # Entry: earliest snapshot (D+2)
                snapshots = sorted(snapshots, key=lambda x: x.get("hours_left", 0), reverse=True)
                entry = snapshots[0]
                ecmwf = entry.get("ecmwf")
                hrrr = entry.get("hrrr")
                
                if not ecmwf or not hrrr:
                    continue
                
                spread = hrrr - ecmwf  # HRRR - ECMWF
                
                # Entry condition: spread > threshold
                if spread > spread_threshold:
                    actual = data.get("actual_temp")
                    if not actual:
                        continue
                    
                    # LONG at ECMWF (bet temp rises to meet HRRR)
                    pnl = actual - ecmwf
                    
                    trades.append({
                        "date": date,
                        "city": city,
                        "ecmwf": ecmwf,
                        "hrrr": hrrr,
                        "actual": actual,
                        "pnl": pnl,
                        "win": pnl > 0,
                    })
        
        wins = sum(1 for t in trades if t["win"])
        total_pnl = sum(t["pnl"] for t in trades)
        
        results[period_name]["trades"] = trades
        results[period_name]["stats"] = {
            "trades": len(trades),
            "wins": wins,
            "win_rate": 100 * wins / len(trades) if trades else 0,
            "pnl": total_pnl,
            "avg_pnl": total_pnl / len(trades) if trades else 0,
        }
    
    results["sigma"] = sigma
    results["sigma_n"] = {k: len(v) for k, v in sigma_errors.items() if len(v) >= 3}
    return results


def simulate_equity(
    results: dict,
    starting_balance: float = 1000.0,
    kelly_fraction: float = 0.25,
    max_bet: float = 20.0,
) -> dict:
    """
    Simulate equity curve with Kelly sizing.

    Uses historical win rate to estimate probability, then sizes bets.
    Assumes binary outcome: win = +100% of stake, lose = -100% of stake.
    """
    # Use train win rate as probability estimate
    train_stats = results["train"]["stats"]
    p = train_stats["win_rate"] / 100.0 if train_stats["trades"] > 0 else 0.5

    # Kelly for even-money bet: f = 2p - 1
    # With fractional Kelly: f * kelly_fraction
    kelly = max(0, (2 * p - 1) * kelly_fraction)

    sim = {"train": {}, "test": {}}

    for period in ["train", "test"]:
        balance = starting_balance
        equity_curve = [balance]
        max_equity = balance
        max_drawdown = 0
        wins = 0
        losses = 0

        for trade in results[period]["trades"]:
            # Size based on current balance
            bet = min(balance * kelly, max_bet)
            if bet < 1.0:  # minimum bet
                bet = min(1.0, balance)

            if trade["win"]:
                balance += bet  # win returns 2x stake, profit = stake
                wins += 1
            else:
                balance -= bet
                losses += 1

            equity_curve.append(round(balance, 2))
            max_equity = max(max_equity, balance)
            drawdown = (max_equity - balance) / max_equity if max_equity > 0 else 0
            max_drawdown = max(max_drawdown, drawdown)

        sim[period] = {
            "start": starting_balance,
            "end": round(balance, 2),
            "return_pct": round(100 * (balance - starting_balance) / starting_balance, 1),
            "max_drawdown_pct": round(100 * max_drawdown, 1),
            "wins": wins,
            "losses": losses,
            "kelly": round(kelly, 4),
            "equity_curve": equity_curve,
        }

    return sim


def print_results(results: dict, train_range: list, test_range: list):
    """Print formatted results."""
    print("=" * 60)
    print("WALK-FORWARD BACKTEST RESULTS")
    print("=" * 60)
    
    for period, range_vals in [("train", train_range), ("test", test_range)]:
        stats = results[period]["stats"]
        print(f"\n{period} PERIOD: {range_vals[0]} → {range_vals[1]}")
        print(f"  Trades: {stats['trades']}")
        print(f"  Win rate: {stats['wins']}/{stats['trades']} ({stats['win_rate']:.1f}%)")
        print(f"  PnL: {stats['pnl']:+.1f} ticks")
        print(f"  Avg: {stats['avg_pnl']:+.2f} ticks/trade")
        
        if stats["trades"] > 0:
            # Break down by month
            by_month = defaultdict(lambda: {"trades": 0, "pnl": 0})
            for t in results[period]["trades"]:
                month = t["date"][:7]
                by_month[month]["trades"] += 1
                by_month[month]["pnl"] += t["pnl"]
            
            print("  By month:")
            for month in sorted(by_month.keys()):
                m = by_month[month]
                print(f"    {month}: {m['trades']} trades, {m['pnl']:+.1f} ticks")
    
    print()
    
    # Summary
    train_pnl = results["train"]["stats"]["pnl"]
    test_pnl = results["test"]["stats"]["pnl"]
    test_win = results["test"]["stats"]["win_rate"]
    
    print("SUMMARY")
    print(f"  Train PnL: {train_pnl:+.1f} ticks")
    print(f"  Test PnL: {test_pnl:+.1f} ticks ({test_win:.0f}% win rate)")
    
    if test_pnl > 0 and test_win > 50:
        print(f"  ✓ EDGE CONFIRMED ON HELD-OUT DATA")
    else:
        print(f"  ✗ No edge on test data")


def main():
    parser = argparse.ArgumentParser(description="Walk-forward backtest")
    parser.add_argument("--data-dir", type=Path, default=Path("data/markets"))
    parser.add_argument("--split", type=float, default=0.8, help="Train/test split ratio")
    parser.add_argument("--threshold", type=float, default=2.0, help="Spread threshold")
    parser.add_argument("--calib", action="store_true", help="Use calibration for tiebreaker")
    parser.add_argument("--export-calib", type=Path, help="Export calibration JSON to path")
    parser.add_argument("--simulate", type=float, metavar="BALANCE", help="Simulate with starting balance")
    parser.add_argument("--kelly", type=float, default=0.25, help="Kelly fraction (default: 0.25)")
    parser.add_argument("--max-bet", type=float, default=20.0, help="Max bet per trade (default: 20)")
    args = parser.parse_args()
    
    # Load and group data
    print(f"Loading markets from {args.data_dir}...")
    markets = load_markets(args.data_dir)
    by_date = group_by_date(markets)
    
    print(f"Loaded {len(markets)} markets across {len(by_date)} dates")
    
    if len(by_date) < 10:
        print("Not enough data for backtest")
        return
    
    dates = list(by_date.keys())
    split_idx = int(len(dates) * args.split)
    train_dates = [dates[0], dates[split_idx - 1]]
    test_dates = [dates[split_idx], dates[-1]]
    
    # Run backtest
    results = run_walkforward(
        by_date,
        split=args.split,
        spread_threshold=args.threshold,
        use_calibration=args.calib,
    )
    
    # Print results
    print_results(results, train_dates, test_dates)

    # Simulate equity if requested
    if args.simulate:
        sim = simulate_equity(
            results,
            starting_balance=args.simulate,
            kelly_fraction=args.kelly,
            max_bet=args.max_bet,
        )
        print("\n" + "=" * 60)
        print(f"EQUITY SIMULATION (${args.simulate:,.0f} start, {args.kelly:.0%} Kelly, ${args.max_bet:.0f} max)")
        print("=" * 60)
        for period in ["train", "test"]:
            s = sim[period]
            print(f"\n{period.upper()}:")
            print(f"  ${s['start']:,.0f} → ${s['end']:,.0f} ({s['return_pct']:+.1f}%)")
            print(f"  Max drawdown: {s['max_drawdown_pct']:.1f}%")
            print(f"  W/L: {s['wins']}/{s['losses']} (Kelly: {s['kelly']:.2%})")

        # Combined run: train then test
        combined_start = args.simulate
        combined_end = sim["test"]["end"] * (sim["train"]["end"] / args.simulate)
        print(f"\nCOMBINED (train → test):")
        print(f"  ${combined_start:,.0f} → ${combined_end:,.0f} ({100*(combined_end/combined_start - 1):+.1f}%)")

    # Export calibration if requested
    if args.export_calib:
        now = datetime.now(timezone.utc).isoformat()
        calib = {
            k: {"sigma": round(v, 3), "n": results["sigma_n"][k], "updated_at": now}
            for k, v in results["sigma"].items()
        }
        args.export_calib.write_text(json.dumps(calib, indent=2))
        print(f"\nCalibration exported to {args.export_calib} ({len(calib)} sources)")


if __name__ == "__main__":
    main()