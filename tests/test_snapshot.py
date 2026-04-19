"""Tests for bot_v2 snapshot helpers."""
from __future__ import annotations

import pytest

from bot_v2 import compute_position_price


def test_returns_price_of_matching_market_id():
    outcomes = [
        {"market_id": "TOP", "price": 0.5, "range": [70, 71]},
        {"market_id": "BOT", "price": 0.12, "range": [68, 69]},
    ]
    position = {"market_id": "BOT"}
    assert compute_position_price(outcomes, position) == 0.12


def test_returns_none_when_no_position():
    outcomes = [{"market_id": "X", "price": 0.3}]
    assert compute_position_price(outcomes, None) is None


def test_returns_none_when_market_id_absent():
    outcomes = [{"market_id": "X", "price": 0.3}]
    assert compute_position_price(outcomes, {"market_id": "Y"}) is None


def test_returns_none_when_outcomes_empty():
    assert compute_position_price([], {"market_id": "X"}) is None
