"""Semantic correctness tests for parse_temp_range.

Tests that the parser extracts the right economic meaning from Polymarket
market questions — sentinel values for edge buckets, finite bounds for
regular buckets, and graceful handling of malformed input.
"""
from __future__ import annotations

import pytest

from bot_v2 import parse_temp_range


class TestOrBelow:
    def test_fahrenheit(self):
        low, high = parse_temp_range("Will the high be 45°F or below on March 7?")
        assert low == -999.0
        assert high == 45.0

    def test_celsius(self):
        low, high = parse_temp_range("Will the high be 10°C or below on April 1?")
        assert low == -999.0
        assert high == 10.0

    def test_negative_temperature(self):
        low, high = parse_temp_range("Will the high be -5°C or below on January 15?")
        assert low == -999.0
        assert high == -5.0

    def test_no_degree_symbol(self):
        low, high = parse_temp_range("Will the high be 45F or below on March 7?")
        assert low == -999.0
        assert high == 45.0


class TestOrHigher:
    def test_fahrenheit(self):
        low, high = parse_temp_range("Will the high be 85°F or higher on July 20?")
        assert low == 85.0
        assert high == 999.0

    def test_celsius(self):
        low, high = parse_temp_range("Will the high be 30°C or higher on August 5?")
        assert low == 30.0
        assert high == 999.0

    def test_no_degree_symbol(self):
        low, high = parse_temp_range("Will the high be 85F or higher on July 20?")
        assert low == 85.0
        assert high == 999.0


class TestBetween:
    def test_fahrenheit(self):
        low, high = parse_temp_range("Will the high be between 70-75°F on March 7?")
        assert low == 70.0
        assert high == 75.0

    def test_celsius(self):
        low, high = parse_temp_range("Will the high be between 20-25°C on April 1?")
        assert low == 20.0
        assert high == 25.0

    def test_negative_temps(self):
        low, high = parse_temp_range("Will the high be between -10--5°C on January 15?")
        assert low == -10.0
        assert high == -5.0

    def test_no_degree_symbol(self):
        low, high = parse_temp_range("Will the high be between 70-75F on March 7?")
        assert low == 70.0
        assert high == 75.0


class TestExactMatch:
    def test_fahrenheit(self):
        low, high = parse_temp_range("Will the high be 72°F on March 7?")
        assert low == 72.0
        assert high == 72.0

    def test_celsius(self):
        low, high = parse_temp_range("Will the high be 22°C on April 1?")
        assert low == 22.0
        assert high == 22.0

    def test_no_degree_symbol(self):
        low, high = parse_temp_range("Will the high be 72F on March 7?")
        assert low == 72.0
        assert high == 72.0


class TestEdgeCases:
    def test_empty_string(self):
        assert parse_temp_range("") is None

    def test_none_input(self):
        assert parse_temp_range(None) is None

    def test_unrelated_text(self):
        assert parse_temp_range("Some unrelated question about weather") is None

    def test_partial_match_no_return(self):
        assert parse_temp_range("Will the high be 45°F") is None

    def test_invariant_low_le_high_for_regular_buckets(self):
        low, high = parse_temp_range("Will the high be between 70-75°F on March 7?")
        assert low <= high

    def test_sentinel_invariant_or_below(self):
        low, high = parse_temp_range("Will the high be 45°F or below on March 7?")
        assert low == -999.0

    def test_sentinel_invariant_or_higher(self):
        low, high = parse_temp_range("Will the high be 85°F or higher on July 20?")
        assert high == 999.0
