"""Tests for the trading_horizon_days prompt instruction."""

from __future__ import annotations

import pytest

import tradingagents.default_config as default_config_module
from tradingagents.agents.context import (
    get_horizon_instruction,
    get_instrument_context_from_state,
)
from tradingagents.dataflows.config import run_config


@pytest.mark.unit
def test_unset_horizon_leaves_context_unchanged():
    with run_config({"trading_horizon_days": None}):
        assert get_horizon_instruction() == ""
        state = {"company_of_interest": "AAPL", "instrument_context": "PRECOMPUTED"}
        assert get_instrument_context_from_state(state) == "PRECOMPUTED"


@pytest.mark.unit
def test_horizon_is_appended_to_context():
    with run_config({"trading_horizon_days": 2}):
        state = {"company_of_interest": "AAPL", "instrument_context": "PRECOMPUTED"}
        context = get_instrument_context_from_state(state)
    assert context.startswith("PRECOMPUTED ")
    assert "within 2 trading days" in context


@pytest.mark.unit
def test_horizon_appended_to_fallback_context():
    with run_config({"trading_horizon_days": 1}):
        context = get_instrument_context_from_state({"company_of_interest": "NVDA"})
    assert "NVDA" in context
    assert "within 1 trading day" in context


@pytest.mark.unit
def test_horizon_accepts_string_from_env():
    with run_config({"trading_horizon_days": "2"}):
        assert "within 2 trading days" in get_horizon_instruction()


@pytest.mark.unit
@pytest.mark.parametrize("bad", ["two", 0, -1])
def test_invalid_horizon_raises(bad):
    with run_config({"trading_horizon_days": bad}):
        with pytest.raises(ValueError):
            get_horizon_instruction()


@pytest.mark.unit
def test_env_overrides_set_horizon_and_holding_period(monkeypatch):
    for key in list(default_config_module._ENV_OVERRIDES):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("TRADINGAGENTS_TRADING_HORIZON_DAYS", "2")
    monkeypatch.setenv("TRADINGAGENTS_HOLDING_PERIOD_DAYS", "2")
    config = default_config_module.build_default_config()
    assert config["trading_horizon_days"] == "2"
    assert config["holding_period_days"] == 2
