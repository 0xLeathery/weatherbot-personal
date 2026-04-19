// tests/dashboard_integration.test.mjs
// End-to-end reconciliation: take a real (trimmed) live payload, run it
// through the full transform + math helpers, assert the KPIs that the
// dashboard would render.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const require = createRequire(import.meta.url);
const DM = require("../web/dashboard_math.js");
const MT = require("../web/market_transform.js");

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

async function loadFixture() {
  const raw = await readFile(resolve(__dirname, "fixtures/live_snapshot.json"), "utf8");
  return JSON.parse(raw);
}

test("live fixture: transform + math helpers produce coherent KPIs", async () => {
  const { state, manifest, rawMarkets } = await loadFixture();
  const names = manifest.markets;
  const raw = names.map((n) => rawMarkets[n]);
  const markets = MT.transformMarkets(raw, names);

  // Every market gets exactly one status.
  const validStatuses = new Set(["open", "closed", "resolved", "no_position"]);
  for (const m of markets) assert.ok(validStatuses.has(m.status), `bad status: ${m.status}`);

  const openMarkets = markets.filter((m) => m.status === "open");

  // No ghost opens (would have undefined entry_price).
  for (const m of openMarkets) {
    assert.notEqual(m.position.entry_price, undefined, `open market has no entry: ${m.slug}`);
  }

  // Equity math doesn't produce NaN.
  const unrealized = openMarkets.reduce((s, m) => {
    const { price } = DM.pickCurrentPrice(m);
    const entry = m.position.entry_price;
    const shares = m.position.shares || 0;
    if (price == null || entry == null) return s;
    return s + (price - entry) * shares;
  }, 0);
  assert.ok(Number.isFinite(unrealized), "unrealized is not finite");

  const reserved = DM.computeReservedCost(markets);
  assert.ok(Number.isFinite(reserved));

  const equity = DM.computeEquityMark({
    cash: state.balance,
    reserved,
    unrealized,
  });
  assert.ok(Number.isFinite(equity));

  // totalTrades is wins+losses.
  const stats = DM.deriveStateStats({
    markets,
    equity: [],
    startingBalance: state.starting_balance,
  });
  assert.equal(stats.totalTrades, stats.wins + stats.losses);

  // Peak KPI is at least cash (once server peak is honored).
  const peak = Math.max(
    typeof state.peak_balance === "number" ? state.peak_balance : 0,
    stats.peakBalance,
  );
  assert.ok(peak >= state.balance - 1e-6, `peak ${peak} < cash ${state.balance}`);
});
