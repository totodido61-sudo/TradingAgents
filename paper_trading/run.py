"""Run one paper-trading session, or print the report.

    python -m paper_trading.run daily              # after the US close
    python -m paper_trading.run report

``daily`` takes the last closed session D, fills the orders pending from earlier
decisions at D's open, asks the agents for a rating on every ticker at D, queues
those as orders for the next open, and values the account at D's close. It runs
at most once per session, so a repeated or late run is harmless.

``--signals AAPL=Buy,NVDA=Hold`` replaces the agents with fixed ratings, to try
the bookkeeping without an LLM; ``--date`` replays a past session.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import tempfile
from datetime import date as Date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from paper_trading import ledger
from paper_trading.report import render

HERE = Path(__file__).resolve().parent
STATE = HERE / "state"
ACCOUNT = STATE / "account.json"
REPORT = STATE / "report.md"

_NEW_YORK = ZoneInfo("America/New_York")
# Yahoo's daily bar for today is partial until a little after the 16:00 close.
_SETTLED = time(16, 15)

logger = logging.getLogger("paper_trading")


def _bars(symbol: str, end: Date):
    import yfinance as yf

    frame = yf.Ticker(symbol).history(
        start=end - timedelta(days=14), end=end + timedelta(days=1), auto_adjust=False
    )
    if frame.empty:
        raise RuntimeError(f"Yahoo Finance returned no prices for {symbol}")
    frame.index = [ts.date() for ts in frame.index]
    return frame


def last_closed_session(reference: str, now: datetime | None = None) -> str:
    now = now or datetime.now(_NEW_YORK)
    sessions = [d for d in _bars(reference, now.date()).index
                if d < now.date() or (d == now.date() and now.time() >= _SETTLED)]
    return str(sessions[-1])


def prices_on(symbols: list[str], day: str) -> tuple[dict, dict]:
    """Open and close in USD for every symbol that traded on ``day``."""
    target = Date.fromisoformat(day)
    opens, closes = {}, {}
    for symbol in symbols:
        frame = _bars(symbol, target)
        if target in frame.index:
            opens[symbol] = float(frame.loc[target, "Open"])
            closes[symbol] = float(frame.loc[target, "Close"])
    return opens, closes


def fx_on(fx_ticker: str, day: str) -> tuple[float, float]:
    """EURUSD open and close on ``day``, or the latest bar before it."""
    frame = _bars(fx_ticker, Date.fromisoformat(day))
    row = frame[frame.index <= Date.fromisoformat(day)].iloc[-1]
    return float(row["Open"]), float(row["Close"])


def ask_agents(cfg: dict, account: dict, day: str, closes: dict, fx: float) -> dict:
    """The agents' rating and reasoning for each ticker; a failure is REVIEW."""
    from tradingagents.default_config import DEFAULT_CONFIG
    from tradingagents.graph.trading_graph import TradingAgentsGraph
    from tradingagents.portfolio import PortfolioContext

    scratch = Path(tempfile.mkdtemp(prefix="paper_trading_"))
    config = {**DEFAULT_CONFIG, **cfg["llm"], "backend_url": None,
              "results_dir": str(scratch / "logs"), "data_cache_dir": str(scratch / "cache"),
              # Kept with the account so each run learns from the settled outcomes.
              "memory_log_path": str(STATE / "trading_memory.md")}
    graph = TradingAgentsGraph(selected_analysts=cfg["analysts"], config=config)
    # The agents see the book in USD, the currency their prices are quoted in.
    portfolio = PortfolioContext.model_validate({
        "cash": account["cash"] * fx, "currency": "USD",
        "positions": [{"ticker": t, "quantity": p["quantity"], "average_price": p["avg_price_usd"]}
                      for t, p in account["positions"].items()],
    })

    results = {}
    for ticker in cfg["tickers"]:
        if ticker not in closes:
            continue
        try:
            state, signal = graph.propagate(ticker, day, portfolio=portfolio)
            results[ticker] = (signal, state.get("final_trade_decision", ""), None)
        except Exception as exc:  # one failed ticker must not cost the others their run
            logger.exception("Analysis of %s on %s failed", ticker, day)
            results[ticker] = ("REVIEW", "", f"{type(exc).__name__}: {exc}")
    return results


def fixed_signals(spec: str, tickers: list[str]) -> dict:
    pairs = dict(item.split("=", 1) for item in spec.split(",") if item)
    return {t: (pairs.get(t, "Hold"), "Segnale fisso passato con --signals.", None)
            for t in tickers}


def daily(cfg: dict, day: str | None, signals: str | None) -> int:
    account = ledger.load(ACCOUNT) or ledger.new_account(
        cfg["tickers"], cfg["initial_capital"], currency=cfg["currency"],
        benchmark=cfg["benchmark"], fee_rate=cfg["fee_rate"],
        min_trade_fraction=cfg["min_trade_fraction"],
    )
    day = day or last_closed_session(cfg["tickers"][0])
    if account["last_run_date"] and day <= account["last_run_date"]:
        print(f"Seduta {day} già elaborata (ultima: {account['last_run_date']}). Niente da fare.")
        return 0

    symbols = [*cfg["tickers"], cfg["benchmark"]]
    opens, closes = prices_on(symbols, day)
    if not closes:
        print(f"Nessuna seduta di borsa il {day}.")
        return 0
    fx_open, fx_close = fx_on(cfg["fx_ticker"], day)

    # Work on a copy: if every analysis fails the session is not recorded, so a
    # rerun after the problem is fixed starts from the same account.
    trial = json.loads(json.dumps(account))
    if trial["start_date"] is None:
        trial["start_date"] = day
    else:
        if cfg["benchmark"] in opens:
            ledger.start_benchmark(trial, day, opens[cfg["benchmark"]], fx_open)
        ledger.fill_orders(trial, day, {t: p for t, p in opens.items() if t in cfg["tickers"]},
                           fx_open)

    decisions = (fixed_signals(signals, cfg["tickers"]) if signals
                 else ask_agents(cfg, trial, day, closes, fx_close))
    if decisions and all(error for _, _, error in decisions.values()):
        for ticker, (_, _, error) in decisions.items():
            print(f"{ticker}: {error}", file=sys.stderr)
        print("Tutte le analisi sono fallite: seduta non registrata.", file=sys.stderr)
        return 1

    for ticker, (rating, text, error) in decisions.items():
        ledger.queue_decision(trial, day, ticker, rating)
        note = STATE / "decisions" / day / f"{ticker}.md"
        note.parent.mkdir(parents=True, exist_ok=True)
        note.write_text(f"# {ticker} · {day} · {rating}\n\n" + (text or f"Errore: {error}") + "\n")

    ledger.mark(trial, day, {t: p for t, p in closes.items() if t in cfg["tickers"]}, fx_close,
                closes.get(cfg["benchmark"]))
    ledger.save(trial, ACCOUNT)
    REPORT.write_text(render(trial))
    point = trial["equity_history"][-1]
    print(f"Seduta {day}: " + ", ".join(f"{t} {r}" for t, (r, _, _) in decisions.items())
          + f" · conto {point['equity']:,.2f} € · benchmark {point['benchmark']:,.2f} €")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=["daily", "report"])
    parser.add_argument("--date", help="session to process, YYYY-MM-DD (default: last closed)")
    parser.add_argument("--signals", help="fixed ratings instead of the agents, e.g. AAPL=Buy")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    cfg = json.loads((HERE / "config.json").read_text())
    if args.command == "report":
        account = ledger.load(ACCOUNT)
        print(render(account) if account else "Nessun conto ancora: lancia prima 'daily'.")
        return 0
    return daily(cfg, args.date, args.signals)


if __name__ == "__main__":
    sys.exit(main())
