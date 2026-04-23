"""Property-based tests for bot_v2 math functions.

Each test asserts a mathematical or economic invariant — not just a point
value — so that implementation errors (sign flips, wrong denominators,
off-by-one) are caught even when the function "returns something."
"""
from __future__ import annotations

import pytest

from bot_v2 import (
    norm_cdf,
    in_bucket,
    bucket_prob,
    calc_ev,
    calc_kelly,
    bet_size,
    MAX_BET,
    KELLY_FRACTION,
)


# =============================================================================
# norm_cdf — standard normal cumulative distribution
# =============================================================================

class TestNormCDF:
    def test_symmetry_at_zero(self):
        assert norm_cdf(0) == pytest.approx(0.5)

    def test_antisymmetry(self):
        for x in [0.5, 1.0, 2.0, 3.0]:
            assert norm_cdf(-x) == pytest.approx(1.0 - norm_cdf(x))

    def test_monotonic(self):
        xs = [i * 0.5 for i in range(-10, 11)]
        for i in range(len(xs) - 1):
            assert norm_cdf(xs[i]) < norm_cdf(xs[i + 1])

    def test_bounds(self):
        for x in [-10, -3, -1, 0, 1, 3, 10]:
            assert 0.0 <= norm_cdf(x) <= 1.0

    def test_tail_behavior(self):
        assert norm_cdf(-4) < 0.001
        assert norm_cdf(4) > 0.999


# =============================================================================
# in_bucket — bucket membership check
# =============================================================================

class TestInBucket:
    def test_inside_regular_bucket(self):
        assert in_bucket(72, 70, 75) is True

    def test_outside_regular_bucket(self):
        assert in_bucket(69, 70, 75) is False
        assert in_bucket(76, 70, 75) is False

    def test_lower_boundary_inclusive(self):
        assert in_bucket(70, 70, 75) is True

    def test_upper_boundary_inclusive(self):
        assert in_bucket(75, 70, 75) is True

    def test_single_point_bucket_exact_match(self):
        assert in_bucket(72, 72, 72) is True

    def test_single_point_bucket_rounds(self):
        assert in_bucket(72.4, 72, 72) is True
        assert in_bucket(71.6, 72, 72) is True
        assert in_bucket(71.4, 72, 72) is False
        assert in_bucket(72.6, 72, 72) is False

    def test_edge_sentinel_lower(self):
        assert in_bucket(0, -999, 45) is True
        assert in_bucket(-999, -999, 45) is True

    def test_edge_sentinel_upper(self):
        assert in_bucket(90, 85, 999) is True
        assert in_bucket(999, 85, 999) is True

    def test_float_forecast(self):
        assert in_bucket(72.5, 70, 75) is True
        assert in_bucket(75.1, 70, 75) is False


# =============================================================================
# bucket_prob — probability of temperature falling in a bucket
# =============================================================================

class TestBucketProb:
    def test_exact_bucket_inside_returns_one(self):
        assert bucket_prob(72, 70, 75) == 1.0

    def test_exact_bucket_outside_returns_zero(self):
        assert bucket_prob(69, 70, 75) == 0.0
        assert bucket_prob(76, 70, 75) == 0.0

    def test_lower_edge_returns_valid_probability(self):
        p = bucket_prob(40, -999, 45)
        assert 0.0 <= p <= 1.0

    def test_lower_edge_probability_decreases_as_forecast_approaches_edge(self):
        p_safe = bucket_prob(30, -999, 45)
        p_near = bucket_prob(43, -999, 45)
        assert p_safe > p_near

    def test_upper_edge_returns_valid_probability(self):
        p = bucket_prob(90, 85, 999)
        assert 0.0 <= p <= 1.0

    def test_upper_edge_probability_decreases_as_forecast_approaches_edge(self):
        p_safe = bucket_prob(100, 85, 999)
        p_near = bucket_prob(87, 85, 999)
        assert p_safe > p_near

    def test_sigma_override_affects_edge_probability(self):
        p_tight = bucket_prob(43, -999, 45, sigma=0.5)
        p_wide = bucket_prob(43, -999, 45, sigma=3.0)
        assert p_tight > p_wide

    def test_tiny_sigma_approximates_in_bucket(self):
        p_inside = bucket_prob(72, 70, 75, sigma=0.001)
        p_outside = bucket_prob(69, 70, 75, sigma=0.001)
        assert p_inside == pytest.approx(1.0)
        assert p_outside == pytest.approx(0.0)

    def test_lower_edge_at_forecast_equals_edge_is_05(self):
        p = bucket_prob(45, -999, 45, sigma=2.0)
        assert p == pytest.approx(0.5, abs=0.01)

    def test_upper_edge_at_forecast_equals_edge_is_05(self):
        p = bucket_prob(85, 85, 999, sigma=2.0)
        assert p == pytest.approx(0.5, abs=0.01)


# =============================================================================
# calc_ev — expected value of a binary prediction market bet
# =============================================================================

class TestCalcEV:
    def test_positive_ev_when_probability_exceeds_price(self):
        assert calc_ev(0.7, 0.3) > 0

    def test_negative_ev_when_probability_below_price(self):
        assert calc_ev(0.3, 0.7) < 0

    def test_zero_ev_when_probability_equals_price(self):
        assert calc_ev(0.5, 0.5) == pytest.approx(0.0, abs=0.001)

    def test_ev_formula_correctness(self):
        p, price = 0.7, 0.3
        expected = p * (1.0 / price - 1.0) - (1.0 - p)
        assert calc_ev(p, price) == pytest.approx(round(expected, 4))

    def test_boundary_price_zero_returns_zero(self):
        assert calc_ev(0.8, 0.0) == 0.0

    def test_boundary_price_one_returns_zero(self):
        assert calc_ev(0.8, 1.0) == 0.0

    def test_negative_price_returns_zero(self):
        assert calc_ev(0.5, -0.1) == 0.0

    def test_price_above_one_returns_zero(self):
        assert calc_ev(0.5, 1.5) == 0.0

    def test_high_confidence_high_ev(self):
        ev = calc_ev(0.9, 0.2)
        assert ev > calc_ev(0.6, 0.2)


# =============================================================================
# calc_kelly — fractional Kelly Criterion position sizing
# =============================================================================

class TestKelly:
    def test_positive_kelly_when_ev_positive(self):
        assert calc_kelly(0.7, 0.3) > 0

    def test_zero_kelly_when_no_edge(self):
        assert calc_kelly(0.5, 0.5) == pytest.approx(0.0, abs=0.01)

    def test_zero_kelly_when_ev_negative(self):
        assert calc_kelly(0.3, 0.7) == 0.0

    def test_kelly_scaled_by_fraction(self):
        full_kelly_b = 1.0 / 0.3 - 1.0
        full_f = (0.7 * full_kelly_b - 0.3) / full_kelly_b
        expected = round(min(max(0.0, full_f) * KELLY_FRACTION, 1.0), 4)
        assert calc_kelly(0.7, 0.3) == expected

    def test_kelly_bounded_zero_to_one(self):
        for p in [0.01, 0.1, 0.3, 0.5, 0.7, 0.9, 0.99]:
            for price in [0.01, 0.1, 0.3, 0.5, 0.7, 0.9, 0.99]:
                k = calc_kelly(p, price)
                assert 0.0 <= k <= 1.0

    def test_boundary_prices_return_zero(self):
        assert calc_kelly(0.8, 0.0) == 0.0
        assert calc_kelly(0.8, 1.0) == 0.0

    def test_higher_edge_produces_larger_kelly(self):
        k1 = calc_kelly(0.6, 0.3)
        k2 = calc_kelly(0.8, 0.3)
        assert k2 > k1


# =============================================================================
# bet_size — converts Kelly fraction to dollar amount
# =============================================================================

class TestBetSize:
    def test_proportional_to_balance(self):
        s1 = bet_size(0.01, 100)
        s2 = bet_size(0.01, 200)
        assert s2 == pytest.approx(s1 * 2)

    def test_never_exceeds_max_bet(self):
        assert bet_size(1.0, MAX_BET * 10) == pytest.approx(MAX_BET)

    def test_zero_kelly_zero_bet(self):
        assert bet_size(0.0, 1000) == 0.0

    def test_small_kelly_proportional(self):
        expected = min(0.05 * 1000, MAX_BET)
        assert bet_size(0.05, 1000) == pytest.approx(expected)

    def test_at_max_bet_boundary(self):
        kelly = MAX_BET / 1000.0
        assert bet_size(kelly, 1000) == pytest.approx(MAX_BET)
        assert bet_size(kelly * 2, 1000) == pytest.approx(MAX_BET)
