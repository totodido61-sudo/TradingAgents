"""The paper-trading ledger: fills, sizing, fees and the benchmark."""

import pytest

from paper_trading import ledger
from paper_trading.report import render

pytestmark = pytest.mark.unit

FX = 1.25  # USD per EUR


def _account(**kw):
    return ledger.new_account(["AAPL", "NVDA"], 10_000, fee_rate=0.0, **kw)


def test_hold_and_review_queue_no_order():
    account = _account()
    ledger.queue_decision(account, "2026-10-01", "AAPL", "Hold")
    ledger.queue_decision(account, "2026-10-01", "NVDA", "REVIEW")
    assert account["pending_orders"] == []
    assert len(account["decisions"]) == 2


def test_buy_fills_its_slot_at_the_open_in_eur():
    account = _account()
    ledger.queue_decision(account, "2026-10-01", "AAPL", "Buy")
    trades = ledger.fill_orders(account, "2026-10-02", {"AAPL": 250.0, "NVDA": 100.0}, FX)
    # Half of 10,000 EUR is 6,250 USD, i.e. 25 shares at 250 USD.
    assert trades[0]["side"] == "BUY"
    assert trades[0]["quantity"] == pytest.approx(25)
    assert account["cash"] == pytest.approx(5_000)
    assert account["pending_orders"] == []


def test_sell_frees_cash_before_buys_and_fees_are_charged():
    account = ledger.new_account(["AAPL", "NVDA"], 10_000, fee_rate=0.01)
    ledger.queue_decision(account, "d1", "AAPL", "Buy")
    ledger.fill_orders(account, "d2", {"AAPL": 100.0, "NVDA": 100.0}, 1.0)
    assert account["cash"] == pytest.approx(10_000 - 5_000 * 1.01)
    ledger.queue_decision(account, "d2", "AAPL", "Sell")
    ledger.fill_orders(account, "d3", {"AAPL": 100.0, "NVDA": 100.0}, 1.0)
    assert "AAPL" not in account["positions"]
    # 5,000 EUR each way at 1%: 50 EUR of fees on the buy and 50 on the sell.
    assert account["cash"] == pytest.approx(9_900)


def test_buy_is_capped_by_cash_and_never_goes_negative():
    account = _account()
    account["cash"] = 1_000
    account["positions"] = {"NVDA": {"quantity": 90, "avg_price_usd": 100.0}}
    ledger.queue_decision(account, "d1", "AAPL", "Buy")
    ledger.fill_orders(account, "d2", {"AAPL": 100.0, "NVDA": 100.0}, 1.0)
    assert account["cash"] == pytest.approx(0)
    assert account["positions"]["AAPL"]["quantity"] == pytest.approx(10)


def test_small_rebalance_is_skipped():
    account = _account()
    ledger.queue_decision(account, "d1", "AAPL", "Buy")
    ledger.fill_orders(account, "d2", {"AAPL": 100.0, "NVDA": 100.0}, 1.0)
    ledger.queue_decision(account, "d2", "AAPL", "Buy")
    assert ledger.fill_orders(account, "d3", {"AAPL": 101.0, "NVDA": 100.0}, 1.0) == []


def test_order_without_price_stays_pending():
    account = _account()
    ledger.queue_decision(account, "d1", "AAPL", "Buy")
    assert ledger.fill_orders(account, "d2", {"NVDA": 100.0}, 1.0) == []
    assert account["pending_orders"][0]["ticker"] == "AAPL"


def test_mark_values_positions_and_benchmark():
    account = _account()
    ledger.start_benchmark(account, "d2", 500.0, FX)
    ledger.queue_decision(account, "d1", "AAPL", "Buy")
    ledger.fill_orders(account, "d2", {"AAPL": 250.0, "NVDA": 100.0}, FX)
    ledger.mark(account, "d2", {"AAPL": 275.0, "NVDA": 100.0}, FX, 550.0)
    point = account["equity_history"][-1]
    assert point["equity"] == pytest.approx(5_000 + 25 * 275 / FX)
    assert point["benchmark"] == pytest.approx(11_000)
    assert "Conto agenti" in render(account)
