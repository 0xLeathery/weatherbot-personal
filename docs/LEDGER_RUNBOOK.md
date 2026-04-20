# Ledger Runbook

## The invariant

```
balance = starting_balance + Σ(realized_pnl) − Σ(open_position_costs)
```

The bot uses **reservation accounting**: entry subtracts `cost` from balance
(`balance -= cost`); closure returns it plus profit (`balance += cost + pnl`).
`balance` tracks **free cash**, not equity. Open positions have their cost
reserved out of balance until they close.

Any tool or script that computes `balance = starting + Σ(realized)` without
subtracting open costs will silently destroy reservations and produce false drift.
Do not use that pattern.

## Drift detection

```bash
python3 tools/verify_ledger.py --data data
```

Expected healthy output:

```
starting_balance:    1,000.00
balance_on_disk:     855.00
balance_from_mkts:   855.00
drift:               0.00
realized markets:    3
no-position closes:  2
open positions:      2  (reserved cost $145.00)
ok:                  True
```

Non-zero drift means the on-disk balance diverges from what the market files
support. Investigate before trading.

## Repairing drift

Always preview first:

```bash
python3 tools/repair_ledger.py --data data --dry-run
python3 tools/repair_ledger.py --data data
python3 tools/verify_ledger.py --data data   # confirm ok: True
```

`repair_ledger` only touches `balance` and (conservatively) `peak_balance`.
It does not rewrite `wins`, `losses`, or `total_trades`.

## Wins/losses staleness

If win-rate KPIs look wrong independently of balance drift, run:

```bash
python3 tools/backfill_state.py --dry-run
python3 tools/backfill_state.py
```

The two tools compose: `repair_ledger` first, then `backfill_state` if needed.

## Pushing repaired state to Railway (production)

`data/` is a Railway volume (not git-tracked). Git push does **not** update it.

```bash
# On local machine — encode the repaired file
base64 -i data/state.json | railway run -- sh -c 'base64 -d > /app/data/state.json'
```

Or via interactive SSH:

```bash
railway ssh
# inside container:
cat > /app/data/state.json << 'EOF'
<paste JSON here>
EOF
```

After pushing, redeploy (`railway up` or trigger via dashboard) so the bot
picks up any code changes alongside the data repair.

## Known past incidents

| Date | Drift | Root cause |
|---|---|---|
| 2026-04-20 | +$361.48 | Morning repair used `balance = starting + realized` (ignored open costs); plus forecast-changed close branch in `bot_v2.py` lacked `status == "open"` guard, re-crediting closed positions each scan cycle. Fixed in same-day commits. |
