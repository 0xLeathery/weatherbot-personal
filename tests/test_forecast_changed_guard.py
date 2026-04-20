"""Tests for _try_close_forecast_changed — the forecast-shifted close helper.

Key invariant: the helper must only fire on positions with status == 'open'.
Without this guard the branch can re-fire on positions already closed by the
stop-loss path, crediting balance += cost + pnl a second time.
"""
from __future__ import annotations

import pytest
from bot_v2 import _try_close_forecast_changed


def _mkt(pos_status="open", existing_pnl=None):
    return {
        "status": "open" if pos_status == "open" else "closed",
        "position": {
            "status": pos_status,
            "market_id": "mkt_1",
            "bucket_low": 70,
            "bucket_high": 75,
            "entry_price": 0.50,
            "shares": 10,
            "cost": 50.0,
            "pnl": existing_pnl,
        },
    }


_LOC      = {"unit": "F"}
_OUTCOMES = [{"market_id": "mkt_1", "price": 0.30}]
_SNAP     = {"ts": "2026-04-20T00:00:00Z"}
# 85°F is 12.5° from mid-bucket (72.5), well past the 4.5° threshold
_FAR_FORECAST = 85.0


def test_skips_already_closed_position():
    mkt = _mkt(pos_status="closed", existing_pnl=-3.0)
    delta = _try_close_forecast_changed(mkt, _OUTCOMES, _FAR_FORECAST, _LOC, _SNAP)
    assert delta == 0.0
    assert mkt["position"]["pnl"] == -3.0  # not overwritten


def test_skips_when_no_position():
    mkt = {"status": "open", "position": None}
    delta = _try_close_forecast_changed(mkt, _OUTCOMES, _FAR_FORECAST, _LOC, _SNAP)
    assert delta == 0.0


def test_skips_when_forecast_none():
    mkt = _mkt(pos_status="open")
    delta = _try_close_forecast_changed(mkt, _OUTCOMES, None, _LOC, _SNAP)
    assert delta == 0.0


def test_closes_open_position_when_forecast_far():
    mkt = _mkt(pos_status="open")
    delta = _try_close_forecast_changed(mkt, _OUTCOMES, _FAR_FORECAST, _LOC, _SNAP)
    # pnl = (0.30 - 0.50) * 10 = -2.0; delta = cost + pnl = 50 + (-2) = 48
    assert delta == pytest.approx(48.0)
    assert mkt["position"]["status"] == "closed"
    assert mkt["position"]["pnl"] == pytest.approx(-2.0)
    assert mkt["position"]["close_reason"] == "forecast_changed"
    assert mkt["position"]["exit_price"] == pytest.approx(0.30)


def test_skips_open_position_when_forecast_still_in_bucket():
    mkt = _mkt(pos_status="open")
    near_forecast = 73.0  # inside 70–75 bucket
    delta = _try_close_forecast_changed(mkt, _OUTCOMES, near_forecast, _LOC, _SNAP)
    assert delta == 0.0
    assert mkt["position"]["status"] == "open"
