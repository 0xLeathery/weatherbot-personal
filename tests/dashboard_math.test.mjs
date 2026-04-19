// tests/dashboard_math.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";

import { pickCurrentPrice } from "../web/dashboard_math.js";

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
