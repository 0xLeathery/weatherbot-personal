"""Tests for bot_v2.get_actual_temp() — the dual-API fallback chain.

The function tries Visual Crossing first (if VC_KEY is set), then falls back
to Open-Meteo archive API. Bugs in this chain mean missing actual_temp data,
which breaks calibration and resolution.

Invariants tested:
  - VC success → returned directly, Open-Meteo NOT called
  - VC failure → Open-Meteo IS called, its result returned
  - Both fail → None returned
  - VC_KEY empty → skip VC entirely, go straight to Open-Meteo
  - VC returns None tempmax → treated as failure, falls through
"""
from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest


def _vc_response(tempmax):
    mock = MagicMock()
    mock.json.return_value = {"days": [{"tempmax": tempmax}]}
    return mock


def _vc_response_no_temp():
    mock = MagicMock()
    mock.json.return_value = {"days": [{"tempmax": None}]}
    return mock


def _vc_response_empty_days():
    mock = MagicMock()
    mock.json.return_value = {"days": []}
    return mock


def _openmeteo_response(temp):
    mock = MagicMock()
    mock.json.return_value = {"daily": {"temperature_2m_max": [temp]}}
    return mock


def _openmeteo_response_no_temp():
    mock = MagicMock()
    mock.json.return_value = {"daily": {"temperature_2m_max": [None]}}
    return mock


def _openmeteo_response_empty():
    mock = MagicMock()
    mock.json.return_value = {"daily": {"temperature_2m_max": []}}
    return mock


class TestVisualCrossingSuccess:
    def test_vc_returns_temp_directly(self, monkeypatch):
        monkeypatch.setattr("bot_v2.VC_KEY", "test-key")
        call_log = []

        def mock_get(url, *args, **kwargs):
            call_log.append(url)
            if "visualcrossing" in url:
                return _vc_response(75.5)
            return _openmeteo_response(999.0)

        monkeypatch.setattr("bot_v2.requests.get", mock_get)

        from bot_v2 import get_actual_temp
        result = get_actual_temp("dallas", "2026-04-01")

        assert result == 75.5
        vc_calls = [u for u in call_log if "visualcrossing" in u]
        om_calls = [u for u in call_log if "archive-api.open-meteo" in u]
        assert len(vc_calls) == 1
        assert len(om_calls) == 0

    def test_vc_null_tempmax_falls_through_to_openmeteo(self, monkeypatch):
        monkeypatch.setattr("bot_v2.VC_KEY", "test-key")
        call_log = []

        def mock_get(url, *args, **kwargs):
            call_log.append(url)
            if "visualcrossing" in url:
                return _vc_response_no_temp()
            if "archive-api.open-meteo" in url:
                return _openmeteo_response(72.0)
            return MagicMock()

        monkeypatch.setattr("bot_v2.requests.get", mock_get)

        from bot_v2 import get_actual_temp
        result = get_actual_temp("dallas", "2026-04-01")

        assert result == 72.0
        assert any("visualcrossing" in u for u in call_log)
        assert any("archive-api.open-meteo" in u for u in call_log)

    def test_vc_empty_days_falls_through(self, monkeypatch):
        monkeypatch.setattr("bot_v2.VC_KEY", "test-key")
        call_log = []

        def mock_get(url, *args, **kwargs):
            call_log.append(url)
            if "visualcrossing" in url:
                return _vc_response_empty_days()
            if "archive-api.open-meteo" in url:
                return _openmeteo_response(68.0)
            return MagicMock()

        monkeypatch.setattr("bot_v2.requests.get", mock_get)

        from bot_v2 import get_actual_temp
        result = get_actual_temp("dallas", "2026-04-01")

        assert result == 68.0


class TestVisualCrossingFailure:
    def test_vc_exception_falls_through_to_openmeteo(self, monkeypatch):
        monkeypatch.setattr("bot_v2.VC_KEY", "test-key")
        call_log = []

        def mock_get(url, *args, **kwargs):
            call_log.append(url)
            if "visualcrossing" in url:
                raise Exception("VC timeout")
            if "archive-api.open-meteo" in url:
                return _openmeteo_response(70.0)
            return MagicMock()

        monkeypatch.setattr("bot_v2.requests.get", mock_get)

        from bot_v2 import get_actual_temp
        result = get_actual_temp("dallas", "2026-04-01")

        assert result == 70.0
        assert any("visualcrossing" in u for u in call_log)
        assert any("archive-api.open-meteo" in u for u in call_log)


class TestBothApisFail:
    def test_both_fail_returns_none(self, monkeypatch):
        monkeypatch.setattr("bot_v2.VC_KEY", "test-key")

        def mock_get(url, *args, **kwargs):
            raise Exception("all APIs down")

        monkeypatch.setattr("bot_v2.requests.get", mock_get)

        from bot_v2 import get_actual_temp
        result = get_actual_temp("dallas", "2026-04-01")

        assert result is None

    def test_vc_succeeds_but_openmeteo_also_fails(self, monkeypatch):
        """VC returns None temp, Open-Meteo also fails → None."""
        monkeypatch.setattr("bot_v2.VC_KEY", "test-key")
        call_count = [0]

        def mock_get(url, *args, **kwargs):
            call_count[0] += 1
            if "visualcrossing" in url:
                return _vc_response_no_temp()
            raise Exception("Open-Meteo down")

        monkeypatch.setattr("bot_v2.requests.get", mock_get)

        from bot_v2 import get_actual_temp
        result = get_actual_temp("dallas", "2026-04-01")

        assert result is None
        assert call_count[0] == 2


class TestVcKeyNotSet:
    def test_empty_vc_key_skips_vc_entirely(self, monkeypatch):
        monkeypatch.setattr("bot_v2.VC_KEY", "")
        call_log = []

        def mock_get(url, *args, **kwargs):
            call_log.append(url)
            if "archive-api.open-meteo" in url:
                return _openmeteo_response(65.0)
            return MagicMock()

        monkeypatch.setattr("bot_v2.requests.get", mock_get)

        from bot_v2 import get_actual_temp
        result = get_actual_temp("dallas", "2026-04-01")

        assert result == 65.0
        vc_calls = [u for u in call_log if "visualcrossing" in u]
        assert len(vc_calls) == 0

    def test_openmeteo_fails_with_no_vc_key(self, monkeypatch):
        monkeypatch.setattr("bot_v2.VC_KEY", "")

        def mock_get(url, *args, **kwargs):
            raise Exception("down")

        monkeypatch.setattr("bot_v2.requests.get", mock_get)

        from bot_v2 import get_actual_temp
        result = get_actual_temp("dallas", "2026-04-01")

        assert result is None


class TestUnitHandling:
    def test_fahrenheit_city_uses_fahrenheit_unit(self, monkeypatch):
        monkeypatch.setattr("bot_v2.VC_KEY", "")
        captured_url = [None]

        def mock_get(url, *args, **kwargs):
            captured_url[0] = url
            return _openmeteo_response(75.0)

        monkeypatch.setattr("bot_v2.requests.get", mock_get)

        from bot_v2 import get_actual_temp
        get_actual_temp("dallas", "2026-04-01")

        assert "temperature_unit=fahrenheit" in captured_url[0]

    def test_celsius_city_uses_celsius_unit(self, monkeypatch):
        monkeypatch.setattr("bot_v2.VC_KEY", "")
        captured_url = [None]

        def mock_get(url, *args, **kwargs):
            captured_url[0] = url
            return _openmeteo_response(22.0)

        monkeypatch.setattr("bot_v2.requests.get", mock_get)

        from bot_v2 import get_actual_temp
        get_actual_temp("london", "2026-04-01")

        assert "temperature_unit=celsius" in captured_url[0]


class TestOpenMeteoEdgeCases:
    def test_openmeteo_returns_none_temp(self, monkeypatch):
        monkeypatch.setattr("bot_v2.VC_KEY", "")

        def mock_get(url, *args, **kwargs):
            return _openmeteo_response_no_temp()

        monkeypatch.setattr("bot_v2.requests.get", mock_get)

        from bot_v2 import get_actual_temp
        result = get_actual_temp("dallas", "2026-04-01")

        assert result is None

    def test_openmeteo_returns_empty_array(self, monkeypatch):
        monkeypatch.setattr("bot_v2.VC_KEY", "")

        def mock_get(url, *args, **kwargs):
            return _openmeteo_response_empty()

        monkeypatch.setattr("bot_v2.requests.get", mock_get)

        from bot_v2 import get_actual_temp
        result = get_actual_temp("dallas", "2026-04-01")

        assert result is None
