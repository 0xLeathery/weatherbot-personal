// tests/market_transform.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { transformMarkets, classifyMarket } =
  require("../web/market_transform.js");

test("classifyMarket: resolved when resolved_outcome is set", () => {
  const m = { resolved_outcome: "win", position: { status: "closed" } };
  assert.equal(classifyMarket(m), "resolved");
});

test("classifyMarket: closed when pos.status is closed and not resolved", () => {
  const m = { resolved_outcome: null, position: { status: "closed" } };
  assert.equal(classifyMarket(m), "closed");
});

test("classifyMarket: open when pos.status is open", () => {
  const m = { resolved_outcome: null, position: { status: "open" } };
  assert.equal(classifyMarket(m), "open");
});

test("classifyMarket: no_position when market closed and position is null", () => {
  const m = { resolved_outcome: null, position: null, status: "closed" };
  assert.equal(classifyMarket(m), "no_position");
});

test("classifyMarket: no_position when market closed and position is empty object", () => {
  const m = { resolved_outcome: null, position: {}, status: "closed" };
  assert.equal(classifyMarket(m), "no_position");
});

test("classifyMarket: open when pos.status=open even if market-level status is closed", () => {
  const m = { resolved_outcome: null, position: { status: "open", entry_price: 0.2, shares: 1 }, status: "closed" };
  assert.equal(classifyMarket(m), "open");
});

test("transformMarkets: maps two raw markets", () => {
  const raw = [
    {
      status: "open",
      position: { status: "open", entry_price: 0.2, shares: 5, cost: 1, bucket_low: 70, bucket_high: 75 },
      resolved_outcome: null,
      pnl: null,
      city: "nyc",
      city_name: "New York",
      date: "2026-04-21",
      unit: "F",
      station: "JFK",
      event_end_date: "2026-04-21T12:00:00Z",
      hours_at_discovery: 24,
      actual_temp: null,
      forecast_snapshots: [],
      market_snapshots: [],
    },
    {
      status: "closed",
      position: { status: "closed", entry_price: 0.3, shares: 3, cost: 0.9, pnl: 1, bucket_low: 60, bucket_high: 65 },
      resolved_outcome: null,
      pnl: null,
      city: "la",
      city_name: "Los Angeles",
      date: "2026-04-20",
      unit: "F",
      station: "LAX",
      event_end_date: "2026-04-20T12:00:00Z",
      hours_at_discovery: 48,
      actual_temp: null,
      forecast_snapshots: [],
      market_snapshots: [],
    },
  ];
  const names = ["nyc_2026-04-21.json", "la_2026-04-20.json"];
  const out = transformMarkets(raw, names);
  assert.equal(out.length, 2);
  // sorted by event_end_date — la (Apr 20) first
  assert.equal(out[0].status, "closed");
  assert.equal(out[0].pnl, 1);
  assert.equal(out[0].slug, "la_2026-04-20");
  assert.equal(out[1].status, "open");
  assert.equal(out[1].pnl, 0);
  assert.equal(out[1].slug, "nyc_2026-04-21");
});

test("transformMarkets: no_position markets have pnl 0 and cost 0", () => {
  const raw = [{
    status: "closed", position: null, resolved_outcome: null, pnl: null,
    event_end_date: "2026-04-20T12:00:00Z", market_snapshots: [],
    city: "chi", city_name: "Chicago", date: "2026-04-20", unit: "F",
    station: "ORD", hours_at_discovery: 12, actual_temp: null,
    forecast_snapshots: [],
  }];
  const out = transformMarkets(raw, ["chi_2026-04-20.json"]);
  assert.equal(out.length, 1);
  assert.equal(out[0].status, "no_position");
  assert.equal(out[0].pnl, 0);
  assert.equal(out[0].position.cost, 0);
});
