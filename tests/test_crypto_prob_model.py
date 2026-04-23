"""Financial and mathematical invariant tests for crypto_bot probability model.

The crypto bot's entire edge depends on compute_realized_vol and prob_above
being correct. Tests verify properties (monotonicity, bounds, symmetry)
rather than single-point values so that formula errors are caught.
"""
from __future__ import annotations

import math
import pytest

from crypto_bot import compute_realized_vol, prob_above, norm_cdf


# =============================================================================
# compute_realized_vol — annualized realized volatility from candles
# =============================================================================

class TestComputeRealizedVol:
    def test_always_non_negative(self):
        candles = [
            [0, 100, 105, 95, 102, 1000],
            [1, 102, 104, 98, 100, 1000],
            [2, 100, 103, 97, 101, 1000],
        ]
        assert compute_realized_vol(candles) >= 0

    def test_empty_candles_returns_zero(self):
        assert compute_realized_vol([]) == 0.0

    def test_single_candle_returns_zero(self):
        candles = [[0, 100, 105, 95, 102, 1000]]
        assert compute_realized_vol(candles) == 0.0

    def test_constant_prices_returns_zero(self):
        candles = [[i, 100, 100, 100, 100, 100] for i in range(10)]
        assert compute_realized_vol(candles) == 0.0

    def test_more_volatile_series_produces_higher_vol(self):
        stable = [[i, 100, 101, 99, 100 + i * 0.1, 1000] for i in range(20)]
        volatile = [[i, 100, 120, 80, 100 + i * 5, 1000] for i in range(20)]
        assert compute_realized_vol(volatile) > compute_realized_vol(stable)

    def test_annualized_value_in_reasonable_range_for_crypto(self):
        candles = [[i, 100 + i * 0.5, 105, 95, 100 + i * 0.5, 1000] for i in range(96)]
        vol = compute_realized_vol(candles)
        assert 0.1 < vol < 10.0

    def test_uses_close_prices(self):
        candles = [
            [0, 100, 110, 90, 100, 1000],
            [1, 100, 110, 90, 110, 1000],
            [2, 100, 110, 90, 120, 1000],
        ]
        vol = compute_realized_vol(candles)
        assert vol > 0


# =============================================================================
# prob_above — log-normal probability price closes above target
# =============================================================================

class TestProbAbove:
    def test_atm_is_half(self):
        assert prob_above(100, 100, 1.0, 24) == pytest.approx(0.5, abs=0.01)

    def test_itm_above_half(self):
        assert prob_above(110, 100, 1.0, 24) > 0.5

    def test_otm_below_half(self):
        assert prob_above(90, 100, 1.0, 24) < 0.5

    def test_monotonic_in_spot(self):
        for s1, s2 in [(90, 100), (100, 110), (110, 120)]:
            assert prob_above(s1, 100, 1.0, 24) < prob_above(s2, 100, 1.0, 24)

    def test_monotonic_in_target(self):
        for k1, k2 in [(90, 100), (100, 110), (110, 120)]:
            assert prob_above(100, k1, 1.0, 24) > prob_above(100, k2, 1.0, 24)

    def test_higher_vol_pushes_prob_toward_half(self):
        p_low_vol = prob_above(110, 100, 0.5, 24)
        p_high_vol = prob_above(110, 100, 2.0, 24)
        assert p_low_vol > p_high_vol > 0.5

    def test_all_outputs_in_bounds(self):
        for s in [50, 100, 150]:
            for k in [50, 100, 150]:
                for v in [0.5, 1.0, 2.0]:
                    for h in [1, 24, 168]:
                        p = prob_above(s, k, v, h)
                        assert 0.0 <= p <= 1.0

    def test_zero_vol_returns_half(self):
        assert prob_above(100, 100, 0.0, 24) == 0.5

    def test_zero_hours_returns_half(self):
        assert prob_above(100, 100, 1.0, 0) == 0.5

    def test_negative_current_returns_half(self):
        assert prob_above(-10, 100, 1.0, 24) == 0.5

    def test_negative_target_returns_half(self):
        assert prob_above(100, -10, 1.0, 24) == 0.5

    def test_long_horizon_approaches_half(self):
        p_short = prob_above(110, 100, 1.0, 1)
        p_long = prob_above(110, 100, 1.0, 8760)
        assert abs(p_long - 0.5) < abs(p_short - 0.5)

    def test_uses_log_normal_cdf(self):
        S, K, vol, hours = 110, 100, 1.0, 24
        T = hours / (365.25 * 24)
        d2 = math.log(S / K) / (vol * math.sqrt(T))
        expected = norm_cdf(d2)
        assert prob_above(S, K, vol, hours) == pytest.approx(round(expected, 4))
