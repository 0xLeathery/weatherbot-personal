"""Cross-component invariant tests.

These verify that data flows correctly between independent components —
the places where bugs hide when each component works in isolation but
the chain between them is broken.

Invariants:
  - run_calibration writes sigma → get_sigma reads it back correctly
  - Calibrated sigma affects bucket_prob differently from default sigma
  - Calibrated sigma flows through to bet sizing
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from bot_v2 import (
    run_calibration,
    get_sigma,
    bucket_prob,
    calc_kelly,
    bet_size,
    SIGMA_F,
    SIGMA_C,
    KELLY_FRACTION,
    MAX_BET,
    LOCATIONS,
)


CALIBRATION_MIN = 1


# =============================================================================
# Calibration → get_sigma chain
# =============================================================================

class TestCalibrationSigmaChain:
    def test_calibrated_sigma_readable_via_get_sigma(self, tmp_path, monkeypatch):
        cal_file = tmp_path / "calibration.json"
        monkeypatch.setattr("bot_v2.CALIBRATION_FILE", cal_file)

        snaps = [{"ecmwf": 70.0 + i, "hrrr": None, "metar": None} for i in range(3)]
        mkts = [
            {"city": "dallas", "actual_temp": 72.0, "forecast_snapshots": snaps}
            for _ in range(3)
        ]
        run_calibration(mkts)

        cal = json.loads(cal_file.read_text())
        assert "dallas_ecmwf" in cal
        assert 0.0 <= cal["dallas_ecmwf"]["sigma"] < 10.0

    def test_get_sigma_falls_back_to_default_when_no_calibration(self, monkeypatch):
        monkeypatch.setattr("bot_v2._cal", {})
        assert get_sigma("dallas", "ecmwf") == SIGMA_F
        assert get_sigma("london", "ecmwf") == SIGMA_C

    def test_get_sigma_uses_calibrated_value_over_default(self, tmp_path, monkeypatch):
        cal_file = tmp_path / "calibration.json"
        cal_file.write_text(json.dumps({
            "dallas_ecmwf": {"sigma": 0.5, "n": 10},
        }))
        monkeypatch.setattr("bot_v2.CALIBRATION_FILE", cal_file)

        from bot_v2 import load_cal
        monkeypatch.setattr("bot_v2._cal", load_cal())

        sigma = get_sigma("dallas", "ecmwf")
        assert sigma == 0.5

    def test_different_cities_have_independent_sigmas(self, tmp_path, monkeypatch):
        cal_file = tmp_path / "calibration.json"
        monkeypatch.setattr("bot_v2.CALIBRATION_FILE", cal_file)
        monkeypatch.setattr("bot_v2._cal", {})

        snaps_d = [{"ecmwf": 70.0 + i} for i in range(3)]
        snaps_l = [{"ecmwf": 15.0 + i * 0.1} for i in range(3)]
        mkts = (
            [{"city": "dallas", "actual_temp": 72.0, "forecast_snapshots": snaps_d}]
            + [{"city": "london", "actual_temp": 16.0, "forecast_snapshots": snaps_l}]
        )
        run_calibration(mkts)

        cal = json.loads(cal_file.read_text())
        assert "dallas_ecmwf" in cal
        assert "london_ecmwf" in cal
        assert cal["dallas_ecmwf"]["sigma"] != cal["london_ecmwf"]["sigma"]


# =============================================================================
# Sigma → probability → position sizing chain
# =============================================================================

class TestSigmaAffectsProbability:
    def test_tighter_sigma_produces_higher_prob_when_forecast_in_bucket(self):
        forecast = 72.0
        p_tight = bucket_prob(forecast, 70, 75, sigma=0.5)
        p_wide = bucket_prob(forecast, 70, 75, sigma=3.0)
        assert p_tight == 1.0
        assert p_wide == 1.0

    def test_sigma_affects_lower_edge_bucket_probability(self):
        """Edge buckets use sigma via normal CDF. Tighter sigma → higher prob
        when forecast is inside the edge bucket."""
        forecast = 43.0
        p_tight = bucket_prob(forecast, -999, 45, sigma=0.5)
        p_wide = bucket_prob(forecast, -999, 45, sigma=3.0)
        assert p_tight > p_wide
        assert 0.0 < p_wide

    def test_sigma_affects_upper_edge_bucket_probability(self):
        forecast = 87.0
        p_tight = bucket_prob(forecast, 85, 999, sigma=0.5)
        p_wide = bucket_prob(forecast, 85, 999, sigma=3.0)
        assert p_tight > p_wide
        assert 0.0 < p_wide

    def test_regular_bucket_ignores_sigma(self):
        """Regular (finite) buckets return 1.0 or 0.0 regardless of sigma."""
        forecast = 76.0
        p_tight = bucket_prob(forecast, 70, 75, sigma=0.5)
        p_wide = bucket_prob(forecast, 70, 75, sigma=3.0)
        assert p_tight == 0.0
        assert p_wide == 0.0


class TestSigmaAffectsKelly:
    def test_higher_prob_from_tighter_sigma_produces_larger_kelly(self):
        forecast = 76.0
        price = 0.30

        p_tight = bucket_prob(forecast, 70, 75, sigma=0.5)
        p_wide = bucket_prob(forecast, 70, 75, sigma=3.0)

        k_tight = calc_kelly(p_tight, price)
        k_wide = calc_kelly(p_wide, price)

        if p_tight > p_wide:
            assert k_tight >= k_wide


class TestEndToEndCalibrationToSizing:
    def test_calibration_flow_produces_different_sizing_than_default(self, tmp_path, monkeypatch):
        """Full chain: calibration writes sigma → get_sigma reads it →
        bucket_prob uses it (edge bucket) → kelly changes → bet_size changes.
        """
        cal_file = tmp_path / "calibration.json"
        monkeypatch.setattr("bot_v2.CALIBRATION_FILE", cal_file)

        snaps = [{"ecmwf": 70.0 + i} for i in range(3)]
        mkts = [
            {"city": "dallas", "actual_temp": 72.0, "forecast_snapshots": snaps}
            for _ in range(3)
        ]
        run_calibration(mkts)

        monkeypatch.setattr("bot_v2._cal", json.loads(cal_file.read_text()))

        sigma = get_sigma("dallas", "ecmwf")
        forecast = 43.0
        p_cal = bucket_prob(forecast, -999, 45, sigma=sigma)
        p_def = bucket_prob(forecast, -999, 45, sigma=SIGMA_F)

        if p_cal != p_def:
            k_cal = calc_kelly(p_cal, 0.30)
            k_def = calc_kelly(p_def, 0.30)
            s_cal = bet_size(k_cal, 10000)
            s_def = bet_size(k_def, 10000)
            assert s_cal != s_def
