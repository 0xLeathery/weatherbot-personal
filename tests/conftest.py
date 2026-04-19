"""Shared fixtures for bot tests."""
from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture
def tmp_data_dir(tmp_path: Path) -> Path:
    """A scratch data/ tree with markets/ and an empty state.json."""
    d = tmp_path / "data"
    (d / "markets").mkdir(parents=True)
    (d / "state.json").write_text(json.dumps({
        "balance": 1000.0,
        "starting_balance": 1000.0,
        "total_trades": 0,
        "wins": 0,
        "losses": 0,
        "peak_balance": 1000.0,
    }, indent=2))
    return d


def _write_market(dir_: Path, name: str, market: dict) -> Path:
    p = dir_ / "markets" / f"{name}.json"
    p.write_text(json.dumps(market, indent=2))
    return p


@pytest.fixture
def write_market(tmp_data_dir: Path):
    def _writer(name: str, market: dict) -> Path:
        return _write_market(tmp_data_dir, name, market)
    return _writer
