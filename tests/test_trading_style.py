"""The ``trading_style`` setting reaches every agent that shapes the decision."""
from pathlib import Path

import pytest

import tradingagents.default_config as default_config_module
from tradingagents.agents.context import get_trading_style_instruction
from tradingagents.dataflows.config import set_config

_AGENTS_DIR = Path(__file__).resolve().parents[1] / "tradingagents" / "agents"

# Every node whose output decides the trade. Analysts only report data.
DECISION_AGENTS = [
    "researchers/bull_researcher.py",
    "researchers/bear_researcher.py",
    "managers/research_manager.py",
    "managers/portfolio_manager.py",
    "risk_mgmt/aggressive_debator.py",
    "risk_mgmt/conservative_debator.py",
    "risk_mgmt/neutral_debator.py",
    "trader/trader.py",
]


@pytest.mark.unit
class TestTradingStyleInstruction:
    @pytest.mark.parametrize("style", [None, "", "default"])
    def test_default_style_adds_no_tokens(self, style):
        set_config({"trading_style": style})
        assert get_trading_style_instruction() == ""

    @pytest.mark.parametrize("style", ["intraday", " Intraday "])
    def test_intraday_asks_for_a_same_day_aggressive_trade(self, style):
        set_config({"trading_style": style})
        out = get_trading_style_instruction()
        assert "intraday" in out
        assert "never held overnight" in out
        assert "aggressive" in out

    def test_unknown_style_fails_loudly(self):
        set_config({"trading_style": "scalping"})
        with pytest.raises(ValueError, match="trading_style"):
            get_trading_style_instruction()


@pytest.mark.unit
@pytest.mark.parametrize("rel", DECISION_AGENTS)
def test_decision_agent_applies_the_trading_style(rel):
    assert "get_trading_style_instruction()" in (_AGENTS_DIR / rel).read_text()


@pytest.mark.unit
def test_env_sets_style_and_holding_period(monkeypatch):
    for key in list(default_config_module._ENV_OVERRIDES):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("TRADINGAGENTS_TRADING_STYLE", "intraday")
    monkeypatch.setenv("TRADINGAGENTS_HOLDING_PERIOD_DAYS", "1")
    config = default_config_module.build_default_config()
    assert config["trading_style"] == "intraday"
    assert config["holding_period_days"] == 1
