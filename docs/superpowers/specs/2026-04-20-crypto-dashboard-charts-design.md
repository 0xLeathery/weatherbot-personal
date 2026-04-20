# Crypto Dashboard Charts Replication — Design Spec

**Date**: 2026-04-20  
**Approach**: Copy and adapt (Approach B)  
**Scope**: Add 4 chart/table components to CryptoView  
**Status**: Approved

---

## Overview

The crypto dashboard currently displays only a KPI strip and Open Positions table. We will replicate the full chart suite from the weather dashboard by copying and adapting the existing chart components to work with crypto position data.

**Charts to add:**
1. Realized PnL chart (equity curve)
2. Drawdown chart
3. PnL breakdown by asset (bar chart)
4. Realized trades table

Both bots share identical state schemas and compatible position data structures, confirming feasibility.

---

## Current State

**CryptoView (existing components):**
- KPI strip: Balance, Open count, Win Rate, Total P&L
- Open Positions table: symbol, question, entry price, EV, model probability, opened date

**Missing (to add):**
- Realized PnL chart
- Drawdown chart
- PnL breakdown chart
- Realized trades table

---

## Data Structure Compatibility

**Weather bot state.json:**
```json
{
  "balance": 658.89,
  "starting_balance": 1000.0,
  "total_trades": 32,
  "wins": 2,
  "losses": 30,
  "peak_balance": 1000.0
}
```

**Crypto bot state.json:**
```json
{
  "balance": 1000.0,
  "starting_balance": 1000.0,
  "total_trades": 0,
  "wins": 0,
  "losses": 0,
  "peak_balance": 1000.0
}
```

**Identical schema confirmed** ✓

**Position data compatibility:**
- Weather positions have: `market_id`, `entry_price`, `pnl`, `closed_at`, `outcome`, `event_end_date`
- Crypto positions have: `market_id`, `entry_price`, `pnl`, `closed_at`, `outcome`, `symbol`, `question`
- Both support the same chart transformations (chronological grouping, asset grouping, PnL aggregation)

---

## Component Design

### 1. Realized PnL Chart
- **Purpose**: Show cumulative PnL over time from all closed positions
- **Data**: `resolved` array sorted by `closed_at`
- **Rendering**: Line chart with cumulative sum, SVG
- **Adaptation from weather**: Copy ResolvedPnLChart component, reuse data accessors (pnl, closed_at identical)

### 2. Drawdown Chart
- **Purpose**: Show maximum drawdown from peak balance
- **Data**: `state.peak_balance` vs running balance from cumulative PnL
- **Rendering**: Fill area chart showing drawdown magnitude
- **Adaptation from weather**: Copy DrawdownChart component, reuse math (no crypto-specific changes needed)

### 3. PnL Breakdown by Asset
- **Purpose**: Aggregate realized PnL grouped by crypto asset (BTC, ETH, SOL, etc.)
- **Data**: Group `resolved` array by `p.symbol`, sum `pnl` per group
- **Rendering**: Bar chart (one bar per asset)
- **Adaptation from weather**: Copy PnLByCity component, change grouping key from city slug to `symbol`

### 4. Realized Trades Table
- **Purpose**: Show all closed positions with entry/exit details
- **Data**: `resolved` array with columns: symbol, question, entry_price, exit_price, pnl, outcome, closed_at
- **Rendering**: HTML table, scrollable
- **Adaptation from weather**: Copy ResolvedTradesTable component, reuse pnl/outcome/entry_price columns

---

## Implementation Steps

1. **Locate source** in Dashboard.html:
   - Find `ResolvedPnLChart({ equity, height = 160 })` function
   - Find `DrawdownChart({ equity, height = 160 })` function
   - Find `PnLByCity({ markets })` function (for asset breakdown pattern)
   - Find realized trades table markup (within weather view)

2. **Copy & adapt**:
   - Extract full function bodies (SVG logic, scaling, data transforms)
   - Replace data accessors: city slug → `symbol`, market fields → position fields
   - Paste into CryptoView component

3. **Structure in CryptoView**:
   ```
   <div className="kpi-strip">...</div>  // existing
   <section className="card">
     <h2>Open Positions ...</h2>        // existing
   </section>
   <div className="chart-wrap">         // NEW: Realized PnL
     <ResolvedPnLChart ... />
   </div>
   <div className="chart-wrap">         // NEW: Drawdown
     <DrawdownChart ... />
   </div>
   <div className="chart-wrap">         // NEW: PnL by Asset
     <PnLByAssetChart ... />
   </div>
   <section className="card">           // NEW: Realized Trades
     <h2>Realized Trades ({resolved.length})</h2>
     <table>...</table>
   </section>
   ```

4. **Styling**: Reuse existing CSS classes (chart-wrap, kpi-strip, card, data-table, etc.)

5. **No changes to weather code** — weather components remain untouched

---

## Data Transformations

### Realized PnL aggregation (for equity curve):
```javascript
const resolved = positions.filter(p => p.status === "closed").sort((a, b) => 
  (a.closed_at || "").localeCompare(b.closed_at || "")
);
let running = 0;
const equity = resolved.map(p => {
  running += p.pnl || 0;
  return { ts: p.closed_at, v: running };
});
```

### PnL by asset:
```javascript
const byAsset = {};
resolved.forEach(p => {
  byAsset[p.symbol] = (byAsset[p.symbol] || 0) + (p.pnl || 0);
});
```

### Equity array (required by DrawdownChart):
```javascript
// DrawdownChart expects { ts, balance } — running balance, not delta
let runningBalance = state.starting_balance;
const equity = resolved.map(p => {
  runningBalance += p.pnl || 0;
  return { ts: p.closed_at, balance: runningBalance };
});
// DrawdownChart tracks peak internally: peak = Math.max(peak, e.balance)
```

---

## Testing Strategy

**Before crypto bot trades:**
- Structure is in place, no data to render
- Empty state messages: "No realized PnL yet — chart fills as positions close"

**After crypto bot executes trades:**
1. Navigate to crypto tab
2. Verify charts render without console errors
3. Spot-check data: sum of chart values matches total P&L in KPI strip
4. Verify asset breakdown sums correctly
5. Verify realized trades table shows all closed positions
6. Compare styling to weather charts (consistency)

**Edge cases:**
- No realized trades yet (empty resolved array) — show empty-state message
- All trades are losses — charts render correctly (negative values)
- Single trade — equity curve is single point, no line drawn

---

## Risk Assessment

**Low risk** — Approach B (copy & adapt) is minimal:
- No refactoring of existing weather code
- No new abstractions or dependencies
- Charts are self-contained, isolated from weather view
- Data structure compatibility already verified
- If charts diverge later (unlikely), they can be refactored individually

**No blockers** — crypto position data is ready and compatible.

---

## Success Criteria

✓ All 4 chart/table components added to CryptoView  
✓ Charts render when position data exists  
✓ Data aggregations match manual calculation  
✓ Styling matches weather charts  
✓ No regressions to weather dashboard  
✓ Empty-state messages when no trades  

---

## Follow-Up

After implementation:
- Run crypto bot to generate position data
- Verify charts populate correctly
- Test interaction (hover, clicks, if applicable)
- Monitor for edge cases (large position counts, date sorting, etc.)
