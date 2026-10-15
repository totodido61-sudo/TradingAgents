"""Intraday call for the next session on each ticker, for a scheduled daily run.

Analyses the last completed trading day and prints one summary block per ticker,
marking whether the call is a move to make (Buy / Overweight / Underweight / Sell)
or a Hold. Exits 0 even when a ticker fails, so one bad ticker does not hide the
others; the failure is printed in its block.

    python scripts/daily_intraday.py AAPL NVDA

Spend is capped per calendar month (UTC), $5 by default (--budget-usd): every
run appends its estimated cost to spend/ledger.jsonl, a ticker is skipped when
the month has less than one run's worth left, and a run stops at the call that
reaches the cap. The ledger only counts this script's runs, so commit it after
each run when the checkout is thrown away.

The provider, models and language come from the TRADINGAGENTS_* environment,
with Gemini Flash and Italian as the defaults here.
"""

import argparse
import datetime as dt
import json
import os
import sys
import time
from pathlib import Path

# Run against this checkout, not an older installed copy of the package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langchain_core.callbacks import UsageMetadataCallbackHandler

from tradingagents.default_config import build_default_config
from tradingagents.graph.trading_graph import TradingAgentsGraph

ACTIONABLE = {"Buy", "Overweight", "Underweight", "Sell"}
TRANSIENT = ("503", "UNAVAILABLE", "disconnected")
# Paid-tier USD per 1M tokens (input, output), from ai.google.dev/gemini-api/docs/pricing.
# gemini-3.8-flash doubles from 2027-01-01. Thinking tokens bill as output.
PRICES = {
    "gemini-3.8-flash": (0.75, 3.75),
    "gemini-3.5-flash-lite": (0.30, 2.50),
    "gemini-3.1-flash-lite": (0.25, 1.50),
    "gemini-3.1-pro-preview": (2.00, 12.00),
}


# The most one analysis is expected to cost; a month with less than this left
# skips the run rather than start one the cap would cut off.
RUN_RESERVE_USD = 0.30
LEDGER = Path(__file__).resolve().parents[1] / "spend" / "ledger.jsonl"


class BudgetExceeded(RuntimeError):
    pass


def cost_usd(usage: dict) -> float:
    """Estimated USD cost of a usage callback's totals; unknown models price as Pro."""
    total = 0.0
    for model, u in usage.items():
        price_in, price_out = PRICES.get(model.removeprefix("models/"), PRICES["gemini-3.1-pro-preview"])
        total += (u.get("input_tokens", 0) * price_in + u.get("output_tokens", 0) * price_out) / 1e6
    return total


def cost_line(usage: dict) -> str:
    tokens = "; ".join(
        f"{m.removeprefix('models/')}: {u.get('input_tokens', 0)} in / {u.get('output_tokens', 0)} out"
        for m, u in usage.items()
    )
    return f"COSTO STIMATO: ${cost_usd(usage):.3f}  ({tokens})"


class BudgetGuard(UsageMetadataCallbackHandler):
    """Counts tokens and stops the run once the month's spend reaches the budget."""

    raise_error = True

    def __init__(self, remaining_usd: float):
        super().__init__()
        self.remaining_usd = remaining_usd

    def on_llm_end(self, response, **kwargs):
        super().on_llm_end(response, **kwargs)
        if cost_usd(self.usage_metadata) >= self.remaining_usd:
            raise BudgetExceeded("budget mensile raggiunto durante l'analisi")


def month_spend(month: str) -> float:
    if not LEDGER.exists():
        return 0.0
    rows = (json.loads(line) for line in LEDGER.read_text().splitlines() if line.strip())
    return sum(r["cost_usd"] for r in rows if r["month"] == month)


def record_spend(month: str, date: str, ticker: str, cost: float) -> None:
    LEDGER.parent.mkdir(exist_ok=True)
    row = {"month": month, "analysis_date": date, "ticker": ticker, "cost_usd": round(cost, 4)}
    with LEDGER.open("a") as f:
        f.write(json.dumps(row) + "\n")


def last_completed_session(today: dt.date) -> dt.date:
    """The weekday before ``today``; exchange holidays fall back to the prior bar."""
    day = today - dt.timedelta(days=1)
    while day.weekday() >= 5:
        day -= dt.timedelta(days=1)
    return day


def config() -> dict:
    env_defaults = {
        "TRADINGAGENTS_LLM_PROVIDER": "google",
        "TRADINGAGENTS_DEEP_THINK_LLM": "gemini-3.8-flash",
        "TRADINGAGENTS_QUICK_THINK_LLM": "gemini-3.5-flash-lite",
        "TRADINGAGENTS_OUTPUT_LANGUAGE": "Italian",
        "TRADINGAGENTS_TRADING_STYLE": "intraday",
        "TRADINGAGENTS_HOLDING_PERIOD_DAYS": "1",
        "TRADINGAGENTS_LLM_MAX_RETRIES": "10",
    }
    for key, value in env_defaults.items():
        os.environ.setdefault(key, value)
    return build_default_config()


def analyse(graph: TradingAgentsGraph, ticker: str, date: str, attempts: int = 3):
    for attempt in range(1, attempts + 1):
        try:
            state, rating = graph.propagate(ticker, date)
            return state, rating
        except Exception as exc:  # noqa: BLE001 — retried or reported below
            if attempt == attempts or not any(k in str(exc) for k in TRANSIENT):
                raise
            time.sleep(60 * attempt)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("tickers", nargs="+")
    parser.add_argument("--date", help="Analysis date, YYYY-MM-DD (default: last completed session)")
    parser.add_argument("--analysts", default="market,news")
    parser.add_argument("--budget-usd", type=float, default=5.0, help="Monthly spend cap (default 5.0)")
    args = parser.parse_args()

    now = dt.datetime.now(dt.UTC)
    month = now.strftime("%Y-%m")
    date = args.date or last_completed_session(now.date()).isoformat()
    cfg = config()

    for ticker in args.tickers:
        print(f"===== {ticker} (dati al {date}) =====")
        remaining = args.budget_usd - month_spend(month)
        if remaining < RUN_RESERVE_USD:
            print(f"BUDGET: speso ${args.budget_usd - remaining:.2f} su ${args.budget_usd:.2f} "
                  f"nel mese {month}; analisi saltata.\n")
            continue
        usage = BudgetGuard(remaining)
        graph = TradingAgentsGraph(
            selected_analysts=args.analysts.split(","), config=cfg, callbacks=[usage]
        )
        try:
            state, rating = analyse(graph, ticker, date)
        except Exception as exc:  # noqa: BLE001 — one ticker must not stop the rest
            print(f"ERRORE: {str(exc)[:500]}")
            print(cost_line(usage.usage_metadata), "\n")
            continue
        finally:
            record_spend(month, date, ticker, cost_usd(usage.usage_metadata))
        print(f"MOSSA: {'SI' if rating in ACTIONABLE else 'NO'}  RATING: {rating}")
        print(cost_line(usage.usage_metadata))
        print(f"SPESA DEL MESE: ${month_spend(month):.2f} su ${args.budget_usd:.2f}")
        print(state["trader_investment_plan"])
        print("\n--- Decisione finale ---")
        print(state["final_trade_decision"], "\n", flush=True)


if __name__ == "__main__":
    main()
