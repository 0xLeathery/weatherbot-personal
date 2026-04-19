// tests/dashboard_math.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";

import {
  pickCurrentPrice,
  computeReservedCost,
  computeEquityMark,
  deriveStateStats,
} from "../web/dashboard_math.js";

test("pickCurrentPrice returns the newest snapshot's position_price", () => {
  const market = {
    position: { entry_price: 0.2, shares: 100 },
    market_snapshots: [
      { ts: "t1", top_price: 0.4, position_price: 0.18 },
      { ts: "t2", top_price: 0.5, position_price: 0.22 },
    ],
  };
  const result = pickCurrentPrice(market);
  assert.deepEqual(result, { price: 0.22, stale: false });
});

test("pickCurrentPrice walks back past snapshots without position_price", () => {
  const market = {
    position: { entry_price: 0.2 },
    market_snapshots: [
      { ts: "t1", top_price: 0.4, position_price: 0.18 },
      { ts: "t2", top_price: 0.5, position_price: null },
    ],
  };
  const result = pickCurrentPrice(market);
  assert.deepEqual(result, { price: 0.18, stale: false });
});

test("pickCurrentPrice falls back to entry_price when no snapshot has it", () => {
  const market = {
    position: { entry_price: 0.2 },
    market_snapshots: [
      { ts: "t1", top_price: 0.4 },
      { ts: "t2", top_price: 0.5, position_price: null },
    ],
  };
  const result = pickCurrentPrice(market);
  assert.deepEqual(result, { price: 0.2, stale: true });
});

test("pickCurrentPrice falls back when market_snapshots is empty", () => {
  const market = { position: { entry_price: 0.2 }, market_snapshots: [] };
  assert.deepEqual(pickCurrentPrice(market), { price: 0.2, stale: true });
});

test("computeReservedCost sums cost over open markets only", () => {
  const markets = [
    { status: "open",     position: { cost: 20 } },
    { status: "open",     position: { cost: 15 } },
    { status: "closed",   position: { cost: 25 } },
    { status: "resolved", position: { cost: 30 } },
  ];
  assert.equal(computeReservedCost(markets), 35);
});

test("computeReservedCost ignores positions with missing cost", () => {
  const markets = [
    { status: "open", position: {} },
    { status: "open", position: { cost: 10 } },
  ];
  assert.equal(computeReservedCost(markets), 10);
});

test("computeEquityMark adds cash, reserved, and unrealized", () => {
  assert.equal(computeEquityMark({ cash: 680.93, reserved: 300, unrealized: 0 }), 980.93);
  assert.equal(computeEquityMark({ cash: 680.93, reserved: 300, unrealized: -42.5 }), 938.43);
  assert.equal(computeEquityMark({ cash: 1000, reserved: 0, unrealized: 0 }), 1000);
});

test("deriveStateStats counts W/L from pnl sign and totals by status", () => {
  const markets = [
    { status: "open",     pnl: 0 },
    { status: "open",     pnl: 0 },
    { status: "closed",   pnl: -5 },
    { status: "closed",   pnl: -3 },
    { status: "resolved", pnl: 12 },
    { status: "resolved", pnl: -7 },
    { status: "resolved", pnl: 0 },   // tie -> loss
  ];
  const equity = [
    { balance: 1000 }, { balance: 1020 }, { balance: 990 }, { balance: 1055 },
  ];
  const stats = deriveStateStats({ markets, equity, startingBalance: 1000 });
  assert.equal(stats.wins, 1);
  assert.equal(stats.losses, 4);
  assert.equal(stats.openCount, 2);
  assert.equal(stats.resolvedCount, 5);
  assert.equal(stats.totalTrades, 7);
  assert.equal(stats.peakBalance, 1055);
});

test("deriveStateStats peak is at least starting balance when no trades", () => {
  const stats = deriveStateStats({ markets: [], equity: [], startingBalance: 1000 });
  assert.equal(stats.peakBalance, 1000);
  assert.equal(stats.totalTrades, 0);
  assert.equal(stats.wins, 0);
  assert.equal(stats.losses, 0);
});
