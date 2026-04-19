(function (root) {
  "use strict";

  function classifyMarket(m) {
    if (m.resolved_outcome != null) return "resolved";
    var pos = m.position;
    if (!pos || !pos.status) return "no_position";
    if (pos.status === "closed") return "closed";
    return "open";
  }

  function _one(m, name) {
    if (!m) return null;
    var pos = m.position || {};
    var status = classifyMarket(m);
    var isResolved = status === "resolved";
    var isClosed = status === "closed";
    var pnl = isResolved ? (m.pnl == null ? 0 : m.pnl)
             : isClosed  ? (pos.pnl == null ? 0 : pos.pnl)
             : 0;
    var cost = pos.cost != null ? pos.cost
             : (pos.entry_price != null && pos.shares != null)
               ? pos.entry_price * pos.shares
               : 0;
    var unit = m.unit || "F";
    return {
      slug: (name || "").replace(/\.json$/, ""),
      city: m.city,
      city_name: m.city_name,
      date: m.date,
      unit: unit,
      station: m.station,
      event_end_date: m.event_end_date,
      hours_at_discovery: m.hours_at_discovery,
      status: status,
      position: {
        entry_price: pos.entry_price,
        shares: pos.shares,
        cost: cost,
        bucket: pos.bucket_low != null
          ? (pos.bucket_low + "-" + pos.bucket_high + "\xb0" + unit)
          : null,
        ev_multiplier: pos.ev,
        forecast_src: pos.forecast_src,
        forecast_temp: pos.forecast_temp,
        sigma: pos.sigma,
        opened_at: pos.opened_at,
        exit_price: pos.exit_price,
        close_reason: pos.close_reason,
        closed_at: pos.closed_at,
      },
      pnl: pnl,
      actual_temp: m.actual_temp,
      resolved_outcome: m.resolved_outcome,
      forecast_snapshots: m.forecast_snapshots || [],
      market_snapshots: m.market_snapshots || [],
    };
  }

  function transformMarkets(rawMarkets, names) {
    var out = [];
    for (var i = 0; i < rawMarkets.length; i++) {
      var r = _one(rawMarkets[i], (names || [])[i]);
      if (r != null) out.push(r);
    }
    out.sort(function (a, b) {
      return new Date(a.event_end_date) - new Date(b.event_end_date);
    });
    return out;
  }

  var api = { classifyMarket: classifyMarket, transformMarkets: transformMarkets };
  if (typeof window !== "undefined") window.MarketTransform = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
