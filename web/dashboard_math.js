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

// In the browser, expose as a global so the React/Babel script (which is
// NOT a module) can call DashboardMath.pickCurrentPrice without an import.
// In Node tests, `globalThis.window` is undefined so this is a no-op.
if (typeof globalThis !== "undefined" && typeof globalThis.window !== "undefined") {
  globalThis.window.DashboardMath = Object.assign(
    globalThis.window.DashboardMath || {},
    { pickCurrentPrice }
  );
}
