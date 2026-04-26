"""Tests for the closure ledger — data/closures.jsonl.

Every realized closure in bot_v2.py reaches one of four sites that calls
apply_closure_to_state. record_closure is called on the adjacent line at
each of those sites. _build_closure_row constructs the row dict; record_closure
appends one JSON line per closure to data/closures.jsonl.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from bot_v2 import _build_closure_row


def _full_pos():
    return {
        "market_id":    "mkt_1",
        "question":     "Will Dallas high be 60-65 on May 1?",
        "entry_price":  0.420,
        "exit_price":   0.350,
        "shares":       23.81,
        "cost":         10.00,
        "pnl":          -1.66,
        "bucket_low":   60,
        "bucket_high":  65,
        "forecast_src": "hrrr",
        "forecast_temp": 62.4,
        "ecmwf_temp":   59.8,
        "spread_at_entry": 2.6,
        "sigma":        2.31,
        "p":            0.5821,
        "ev":           0.0445,
        "opened_at":    "2026-04-30T12:00:00+00:00",
        "closed_at":    "2026-04-30T18:30:00+00:00",
        "close_reason": "stop_loss",
        "status":       "closed",
    }


def _full_mkt(pos):
    return {
        "market_id":   "mkt_1",
        "city":        "dallas",
        "city_name":   "Dallas",
        "date":        "2026-05-01",
        "position":    pos,
        "status":      "closed",
        "pnl":         None,
    }


class TestBuildClosureRow:
    def test_full_row_has_every_spec_field(self):
        pos = _full_pos()
        mkt = _full_mkt(pos)
        row = _build_closure_row(mkt, pos)

        assert row["type"] == "closure"
        assert row["ts"] == "2026-04-30T18:30:00+00:00"  # comes from pos.closed_at
        assert row["city"] == "dallas"
        assert row["date_target"] == "2026-05-01"
        assert row["market_id"] == "mkt_1"
        assert row["close_reason"] == "stop_loss"
        assert row["opened_at"] == "2026-04-30T12:00:00+00:00"
        assert row["entry_price"] == 0.420
        assert row["exit_price"] == 0.350
        assert row["shares"] == 23.81
        assert row["cost"] == 10.00
        assert row["pnl"] == -1.66
        assert row["bucket_low"] == 60
        assert row["bucket_high"] == 65
        assert row["forecast_src"] == "hrrr"
        assert row["forecast_temp_at_entry"] == 62.4
        assert row["ecmwf_temp_at_entry"] == 59.8
        assert row["spread_at_entry"] == 2.6
        assert row["sigma_at_entry"] == 2.31
        assert row["p_at_entry"] == 0.5821
        assert row["ev_at_entry"] == 0.0445

    def test_missing_spread_fields_become_null(self):
        # Pre-eabdb67 closure: no spread-strategy fields on pos.
        pos = {
            "market_id":   "mkt_1",
            "entry_price": 0.50, "exit_price": 0.60, "shares": 20.0, "cost": 10.0,
            "pnl":         2.00, "bucket_low": 60, "bucket_high": 65,
            "opened_at":   "2026-04-22T00:00:00+00:00",
            "closed_at":   "2026-04-22T12:00:00+00:00",
            "close_reason": "resolved",
        }
        mkt = _full_mkt(pos)
        row = _build_closure_row(mkt, pos)

        assert row["spread_at_entry"] is None
        assert row["sigma_at_entry"] is None
        assert row["forecast_src"] is None
        assert row["ecmwf_temp_at_entry"] is None
        assert row["forecast_temp_at_entry"] is None
        assert row["p_at_entry"] is None
        assert row["ev_at_entry"] is None
        # Guaranteed-non-null fields still present
        assert row["pnl"] == 2.00
        assert row["entry_price"] == 0.50
        assert row["exit_price"] == 0.60
        assert row["close_reason"] == "resolved"

    def test_ts_falls_back_to_now_when_closed_at_missing(self):
        # Defensive: if closed_at is missing for any reason, ts must still be set.
        pos = _full_pos()
        del pos["closed_at"]
        mkt = _full_mkt(pos)
        row = _build_closure_row(mkt, pos)
        assert row["ts"]  # non-empty
        assert row["ts"].endswith("+00:00") or row["ts"].endswith("Z")
