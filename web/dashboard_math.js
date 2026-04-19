// web/dashboard_math.js
// Pure math helpers for Dashboard.html.
// Loaded from Dashboard.html via <script type="module">.
// Also importable from Node tests via `import { ... } from ".../dashboard_math.js"`.
//
// Keep this file dependency-free — no imports.

export function pickCurrentPrice(market) {
  const entry = market?.position?.entry_price;
  const snaps = market?.market_snapshots || [];
  for (let i = snaps.length - 1; i >= 0; i--) {
    const s = snaps[i];
    if (s && s.position_price != null) {
      return { price: s.position_price, stale: false };
    }
  }
  return { price: entry, stale: true };
}

export function computeReservedCost(markets) {
  let total = 0;
  for (const m of markets || []) {
    if (m && m.status === "open") {
      total += m.position?.cost || 0;
    }
  }
  return total;
}

export function computeEquityMark({ cash, reserved, unrealized }) {
  return (cash || 0) + (reserved || 0) + (unrealized || 0);
}

export function deriveStateStats({ markets, equity, startingBalance }) {
  let wins = 0, losses = 0, openCount = 0, resolvedCount = 0;
  for (const m of markets || []) {
    if (m.status === "open") { openCount++; continue; }
    resolvedCount++;
    if (m.pnl > 0) wins++;
    else losses++;             // ties count as losses — matches backfill
  }
  let peak = startingBalance || 0;
  for (const e of equity || []) {
    if (e && typeof e.balance === "number" && e.balance > peak) peak = e.balance;
  }
  return {
    wins, losses,
    openCount, resolvedCount,
    totalTrades: openCount + resolvedCount,
    peakBalance: peak,
  };
}

// In the browser, expose as a global so the React/Babel script (which is
// NOT a module) can call DashboardMath.pickCurrentPrice without an import.
// In Node tests, `globalThis.window` is undefined so this is a no-op.
if (typeof globalThis !== "undefined" && typeof globalThis.window !== "undefined") {
  globalThis.window.DashboardMath = Object.assign(
    globalThis.window.DashboardMath || {},
    { pickCurrentPrice, computeReservedCost, computeEquityMark, deriveStateStats }
  );
}
