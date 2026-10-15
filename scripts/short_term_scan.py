"""Short-term scan: run TradingAgents on a few tickers and print a trade ticket.

Each ticker is analysed as of its last completed trading session, with the
agents told to plan a trade that is opened and closed within
``--horizon`` trading days. A Buy / Overweight / Sell / Underweight rating is
an actionable signal; Hold means no trade. The ticket lists what to enter on a
trading platform: direction, entry, stop-loss, target and exit deadline.

Usage::

    python scripts/short_term_scan.py AAPL MSFT --horizon 2

Settings that are not flags (provider, models, output language, retries) come
from the TRADINGAGENTS_* environment variables like any other run; the
defaults below apply only when those are unset. Results are also written as
JSON under ``<results_dir>/short_term_scan/``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date, datetime
from pathlib import Path

ACTIONABLE = {"Buy": "LONG (acquisto)", "Overweight": "LONG (acquisto)",
              "Sell": "SHORT / non entrare long", "Underweight": "SHORT / non entrare long"}

_DEFAULTS = {
    "TRADINGAGENTS_LLM_PROVIDER": "google",
    "TRADINGAGENTS_DEEP_THINK_LLM": "gemini-3.8-flash",
    "TRADINGAGENTS_QUICK_THINK_LLM": "gemini-3.8-flash",
    "TRADINGAGENTS_OUTPUT_LANGUAGE": "Italian",
    "TRADINGAGENTS_LLM_MAX_RETRIES": "6",
}


def _field(text: str, label: str) -> str | None:
    """The value of a ``**Label**: value`` line in a rendered agent report."""
    match = re.search(rf"\*\*{re.escape(label)}\*\*:\s*(.+)", text or "")
    if not match:
        return None
    value = match.group(1).strip()
    return None if value.lower() in {"not provided", "none", ""} else value


def _last_session(ticker: str) -> str:
    """Date of the ticker's last completed daily bar."""
    import yfinance as yf

    bars = yf.Ticker(ticker).history(period="10d", interval="1d")
    if bars.empty:
        raise RuntimeError(f"no recent price data for {ticker}")
    last = bars.index[-1].date()
    # A bar dated today is the session still trading: analyse the one before.
    if last >= date.today() and len(bars) > 1:
        last = bars.index[-2].date()
    return last.isoformat()


def scan(ticker: str, horizon: int, analysts: list[str], debate_rounds: int) -> dict:
    from tradingagents.default_config import build_default_config
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    config = build_default_config()
    config.update({
        "trading_horizon_days": horizon,
        "holding_period_days": horizon,
        "max_debate_rounds": debate_rounds,
        "max_risk_discuss_rounds": debate_rounds,
    })
    trade_date = _last_session(ticker)
    graph = TradingAgentsGraph(selected_analysts=analysts, debug=False, config=config)
    state, rating = graph.propagate(ticker, trade_date)

    trader = state.get("trader_investment_plan", "")
    final = state.get("final_trade_decision", "")
    return {
        "ticker": ticker,
        "analysis_date": trade_date,
        "horizon_days": horizon,
        "rating": rating,
        "actionable": rating in ACTIONABLE,
        "direction": ACTIONABLE.get(rating, "nessuna operazione"),
        "entry": _field(trader, "Entry Price"),
        "stop_loss": _field(trader, "Stop Loss"),
        "position_sizing": _field(trader, "Position Sizing"),
        "price_target": _field(final, "Price Target"),
        "time_horizon": _field(final, "Time Horizon"),
        "summary": _field(final, "Executive Summary"),
        "final_trade_decision": final,
        "trader_investment_plan": trader,
    }


def ticket(result: dict) -> str:
    """The trade ticket for one ticker, in the shape a platform order needs."""
    if not result["actionable"]:
        return (f"{result['ticker']} ({result['analysis_date']}): {result['rating']} "
                f"- nessuna operazione nei prossimi {result['horizon_days']} giorni.")
    na = "non indicato"
    return "\n".join([
        f"=== SEGNALE {result['ticker']} — {result['rating'].upper()} ===",
        f"Direzione:      {result['direction']}",
        f"Ingresso:       {result['entry'] or 'a mercato all apertura'}",
        f"Stop loss:      {result['stop_loss'] or na}",
        f"Take profit:    {result['price_target'] or na}",
        f"Dimensione:     {result['position_sizing'] or na}",
        f"Uscita entro:   {result['horizon_days']} giorni di borsa "
        f"(orizzonte indicato: {result['time_horizon'] or na})",
        f"Dati al:        chiusura del {result['analysis_date']}",
        f"Sintesi:        {result['summary'] or na}",
    ])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("tickers", nargs="+")
    parser.add_argument("--horizon", type=int, default=2, help="trading days (default 2)")
    parser.add_argument("--analysts", default="market,news",
                        help="comma-separated: market,social,news,fundamentals")
    parser.add_argument("--debate-rounds", type=int, default=1)
    args = parser.parse_args(argv)

    for key, value in _DEFAULTS.items():
        os.environ.setdefault(key, value)

    analysts = [a.strip() for a in args.analysts.split(",") if a.strip()]
    results, failures = [], []
    for ticker in args.tickers:
        try:
            results.append(scan(ticker.upper(), args.horizon, analysts, args.debate_rounds))
        except Exception as exc:  # one bad ticker must not hide the others' signals
            failures.append(f"{ticker.upper()}: {type(exc).__name__}: {exc}")

    from tradingagents.default_config import build_default_config
    out_dir = Path(build_default_config()["results_dir"]) / "short_term_scan"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"scan_{datetime.now():%Y%m%d_%H%M%S}.json"
    out_file.write_text(json.dumps({"results": results, "failures": failures},
                                   ensure_ascii=False, indent=2))

    signals = [r for r in results if r["actionable"]]
    print()
    for result in results:
        print(ticket(result))
        print()
    for failure in failures:
        print(f"ERRORE {failure}")
    print(f"SEGNALI OPERATIVI: {len(signals)} su {len(results)} ticker analizzati")
    print(f"Dettagli completi: {out_file}")
    return 1 if failures and not results else 0


if __name__ == "__main__":
    sys.exit(main())
