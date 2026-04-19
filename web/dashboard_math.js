// web/dashboard_math.js
// Pure math helpers for Dashboard.html.
// Classic script so it's available to the inline Babel block synchronously —
// no module/defer race with Babel standalone's DOMContentLoaded handler.
// Node tests load this via createRequire() in dashboard_math.test.mjs.

(function (global) {
  function pickCurrentPrice(market) {
    var entry = market && market.position && market.position.entry_price;
    var snaps = (market && market.market_snapshots) || [];
    for (var i = snaps.length - 1; i >= 0; i--) {
      var s = snaps[i];
      if (s && s.position_price != null) {
        return { price: s.position_price, stale: false };
      }
    }
    return { price: entry != null ? entry : null, stale: true };
  }

  function computeReservedCost(markets) {
    var total = 0;
    for (var i = 0, xs = markets || []; i < xs.length; i++) {
      var m = xs[i];
      if (m && m.status === "open") {
        total += (m.position && m.position.cost) || 0;
      }
    }
    return total;
  }

  function computeEquityMark(args) {
    function num(v) {
      var n = (v == null) ? 0 : v;
      if (!isFinite(n)) throw new Error("computeEquityMark: non-finite input");
      return n;
    }
    return num(args && args.cash) + num(args && args.reserved) + num(args && args.unrealized);
  }

  function deriveStateStats(args) {
    var markets = (args && args.markets) || [];
    var equity = (args && args.equity) || [];
    var startingBalance = (args && args.startingBalance) || 0;
    var wins = 0, losses = 0, openCount = 0, noPositionCount = 0, resolvedCount = 0;
    for (var i = 0; i < markets.length; i++) {
      var m = markets[i];
      if (m.status === "open") { openCount++; continue; }
      if (m.status === "no_position") { noPositionCount++; continue; }
      resolvedCount++;
      if (m.pnl == null) continue;
      if (m.pnl > 0) wins++;
      else losses++;
    }
    var peak = startingBalance;
    for (var j = 0; j < equity.length; j++) {
      var e = equity[j];
      if (e && typeof e.balance === "number" && e.balance > peak) peak = e.balance;
    }
    return {
      wins: wins,
      losses: losses,
      openCount: openCount,
      noPositionCount: noPositionCount,
      resolvedCount: resolvedCount,
      totalTrades: wins + losses,
      peakBalance: peak,
    };
  }

  var api = {
    pickCurrentPrice: pickCurrentPrice,
    computeReservedCost: computeReservedCost,
    computeEquityMark: computeEquityMark,
    deriveStateStats: deriveStateStats,
  };

  // Browser: attach to window so Babel/React block can call DashboardMath.*.
  if (typeof window !== "undefined") {
    window.DashboardMath = api;
  }
  // Node (CommonJS): export the same API for tests via createRequire().
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
})(typeof globalThis !== "undefined" ? globalThis : this);
