"""The paper-trading account: EUR cash holding US stocks priced in USD.

Pure bookkeeping, with no network and no LLM, so every rule here is testable.
Prices come in as USD per share and ``fx`` as USD per 1 EUR (Yahoo's EURUSD=X),
so a USD amount converts to EUR as ``usd / fx``.

Each ticker owns an equal slot of the account's equity. A rating sets the target
share of that slot; Hold and an unreadable decision (REVIEW) leave the position
as it is. A decision taken after the close of day D becomes a pending order that
fills at the open of the next session, never at the close it was decided on.
"""

from __future__ import annotations

import json
from pathlib import Path

# Share of a ticker's slot to hold after each rating. Hold and REVIEW are absent
# on purpose: they keep the position unchanged rather than rebalancing it.
TARGET_FRACTION = {"Buy": 1.0, "Overweight": 0.75, "Underweight": 0.25, "Sell": 0.0}

# Quantities below this are dust left by float arithmetic, not a position.
_DUST = 1e-9


def target_fraction(rating: str) -> float | None:
    return TARGET_FRACTION.get(rating)


def new_account(tickers: list[str], capital: float, *, currency: str = "EUR",
                benchmark: str = "SPY", fee_rate: float = 0.001,
                min_trade_fraction: float = 0.05) -> dict:
    return {
        "currency": currency,
        "tickers": list(tickers),
        "initial_capital": capital,
        "settings": {"fee_rate": fee_rate, "min_trade_fraction": min_trade_fraction},
        "start_date": None,
        "last_run_date": None,
        "cash": capital,
        "positions": {},
        "pending_orders": [],
        "decisions": [],
        "trades": [],
        "equity_history": [],
        "benchmark": {"ticker": benchmark, "quantity": None, "start_date": None},
        "last_prices_usd": {},
        "last_fx": None,
    }


def load(path: Path) -> dict | None:
    return json.loads(path.read_text()) if path.exists() else None


def save(account: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(account, indent=2, ensure_ascii=False) + "\n")


def equity(account: dict, prices_usd: dict[str, float], fx: float) -> float:
    """Cash plus positions valued at ``prices_usd``, in EUR."""
    held = sum(p["quantity"] * prices_usd[t] / fx for t, p in account["positions"].items())
    return account["cash"] + held


def benchmark_value(account: dict, price_usd: float | None, fx: float) -> float:
    """The buy-and-hold benchmark in EUR; the initial capital until it is bought."""
    quantity = account["benchmark"]["quantity"]
    if quantity is None or price_usd is None:
        return account["initial_capital"]
    return quantity * price_usd / fx


def start_benchmark(account: dict, date: str, open_usd: float, fx: float) -> None:
    """Buy the benchmark with the whole initial capital, once, at an open."""
    if account["benchmark"]["quantity"] is None:
        account["benchmark"]["quantity"] = account["initial_capital"] * fx / open_usd
        account["benchmark"]["start_date"] = date


def queue_decision(account: dict, date: str, ticker: str, rating: str) -> None:
    """Record a decision; a rating with a target becomes an order for the next open."""
    account["decisions"].append({"date": date, "ticker": ticker, "rating": rating})
    fraction = target_fraction(rating)
    if fraction is not None:
        account["pending_orders"].append(
            {"ticker": ticker, "rating": rating, "target_fraction": fraction, "decided_on": date}
        )


def fill_orders(account: dict, date: str, open_usd: dict[str, float], fx: float) -> list[dict]:
    """Fill pending orders at ``date``'s open and return the trades made.

    Sells go first so their proceeds can fund the buys; a buy larger than the
    cash left is scaled down. Moves smaller than ``min_trade_fraction`` of a slot
    are skipped so a repeated Buy does not churn on daily price drift. An order
    whose ticker has no price stays pending.
    """
    settings = account["settings"]
    fee_rate = settings["fee_rate"]
    slot = equity(account, open_usd, fx) / len(account["tickers"])
    min_trade = slot * settings["min_trade_fraction"]

    # A later decision for the same ticker supersedes an earlier unfilled one.
    latest: dict[str, dict] = {}
    for order in account["pending_orders"]:
        latest[order["ticker"]] = order
    waiting, sells, buys = [], [], []
    for ticker, order in latest.items():
        price = open_usd.get(ticker)
        if price is None:
            waiting.append(order)
            continue
        held = account["positions"].get(ticker, {}).get("quantity", 0.0)
        delta = slot * order["target_fraction"] * fx / price - held
        if abs(delta) * price / fx < min_trade:
            continue
        (sells if delta < 0 else buys).append((order, price, delta))
    account["pending_orders"] = waiting

    trades = []
    for order, price, delta in sells:
        quantity = -delta
        value = quantity * price / fx
        fee = value * fee_rate
        account["cash"] += value - fee
        position = account["positions"][order["ticker"]]
        position["quantity"] -= quantity
        if position["quantity"] <= _DUST:
            del account["positions"][order["ticker"]]
        trades.append(_trade(date, order, "SELL", quantity, price, fx, value, fee))

    for order, price, delta in buys:
        unit_cost = price / fx * (1 + fee_rate)
        quantity = min(delta, max(account["cash"], 0.0) / unit_cost)
        value = quantity * price / fx
        if value < min_trade:
            continue
        fee = value * fee_rate
        account["cash"] = max(account["cash"] - value - fee, 0.0)
        position = account["positions"].setdefault(
            order["ticker"], {"quantity": 0.0, "avg_price_usd": 0.0}
        )
        total = position["quantity"] + quantity
        position["avg_price_usd"] = (
            position["quantity"] * position["avg_price_usd"] + quantity * price
        ) / total
        position["quantity"] = total
        trades.append(_trade(date, order, "BUY", quantity, price, fx, value, fee))

    account["trades"].extend(trades)
    return trades


def _trade(date, order, side, quantity, price, fx, value, fee) -> dict:
    return {
        "date": date, "ticker": order["ticker"], "side": side, "quantity": quantity,
        "price_usd": price, "fx": fx, "value": value, "fee": fee,
        "rating": order["rating"], "decided_on": order["decided_on"],
    }


def mark(account: dict, date: str, close_usd: dict[str, float], fx: float,
         benchmark_close: float | None) -> None:
    """Value the account at ``date``'s close and append it to the history."""
    account["equity_history"].append({
        "date": date,
        "equity": equity(account, close_usd, fx),
        "cash": account["cash"],
        "benchmark": benchmark_value(account, benchmark_close, fx),
    })
    account["last_prices_usd"] = dict(close_usd)
    if benchmark_close is not None:
        account["last_prices_usd"][account["benchmark"]["ticker"]] = benchmark_close
    account["last_fx"] = fx
    account["last_run_date"] = date
