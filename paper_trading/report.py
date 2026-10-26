"""The account as a Markdown report, in Italian, from the saved ledger alone."""

from __future__ import annotations

_RATING_IT = {
    "Buy": "Compra", "Overweight": "Sovrappesa", "Hold": "Mantieni",
    "Underweight": "Sottopesa", "Sell": "Vendi", "REVIEW": "Illeggibile (nessun trade)",
}


def _eur(value: float) -> str:
    return f"{value:,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")


def _pct(value: float) -> str:
    return f"{value:+.2f}%".replace(".", ",")


def render(account: dict, recent_days: int = 5) -> str:
    capital = account["initial_capital"]
    history = account["equity_history"]
    lines = ["# Paper trading TradingAgents", ""]
    if not history:
        return "\n".join(lines + ["Nessuna sessione registrata finora.", ""])

    last = history[-1]
    prices, fx = account["last_prices_usd"], account["last_fx"]
    value, bench = last["equity"], last["benchmark"]
    lines += [
        f"Aggiornato alla chiusura del **{last['date']}** · avvio {account['start_date']} · "
        f"titoli: {', '.join(account['tickers'])}",
        "",
        "## Riepilogo",
        "",
        "| | Valore | Rendimento |",
        "|---|---:|---:|",
        f"| Capitale iniziale | {_eur(capital)} | |",
        f"| **Conto agenti** | **{_eur(value)}** | **{_pct((value / capital - 1) * 100)}** |",
        f"| Benchmark: {account['benchmark']['ticker']} comprato e tenuto | {_eur(bench)} | "
        f"{_pct((bench / capital - 1) * 100)} |",
        f"| Differenza agenti − benchmark | {_eur(value - bench)} | |",
        f"| Liquidità | {_eur(account['cash'])} | |",
        "",
        f"Cambio EUR/USD usato: {fx:.4f}. Commissione simulata: "
        f"{account['settings']['fee_rate'] * 100:.2f}% per operazione.",
        "",
        "## Posizioni aperte",
        "",
    ]
    if account["positions"]:
        lines += ["| Titolo | Azioni | Prezzo medio $ | Ultimo $ | Valore | Guadagno |",
                  "|---|---:|---:|---:|---:|---:|"]
        for ticker, pos in sorted(account["positions"].items()):
            price = prices[ticker]
            gain = (price / pos["avg_price_usd"] - 1) * 100
            lines.append(
                f"| {ticker} | {pos['quantity']:.4f} | {pos['avg_price_usd']:.2f} | {price:.2f} | "
                f"{_eur(pos['quantity'] * price / fx)} | {_pct(gain)} |"
            )
    else:
        lines.append("Nessuna posizione: tutto in liquidità.")

    if account["pending_orders"]:
        lines += ["", "**Ordini in attesa** (eseguiti all'apertura della prossima seduta): "
                  + ", ".join(f"{o['ticker']} → {_RATING_IT[o['rating']]}"
                              for o in account["pending_orders"])]

    dates = sorted({d["date"] for d in account["decisions"]})[-recent_days:]
    lines += ["", f"## Decisioni degli agenti (ultime {len(dates)} sedute)", "",
              "| Data | " + " | ".join(account["tickers"]) + " |",
              "|---|" + "---|" * len(account["tickers"])]
    for date in reversed(dates):
        by_ticker = {d["ticker"]: d["rating"] for d in account["decisions"] if d["date"] == date}
        lines.append(f"| {date} | " + " | ".join(
            _RATING_IT.get(by_ticker.get(t, ""), "—") for t in account["tickers"]) + " |")
    lines.append("")
    lines.append("Le motivazioni complete sono in `paper_trading/state/decisions/<data>/<titolo>.md`.")

    lines += ["", "## Operazioni eseguite", ""]
    if account["trades"]:
        lines += ["| Data | Titolo | Lato | Azioni | Prezzo $ | Importo | Commissione | Decisione del |",
                  "|---|---|---|---:|---:|---:|---:|---|"]
        for t in reversed(account["trades"]):
            side = "Acquisto" if t["side"] == "BUY" else "Vendita"
            lines.append(
                f"| {t['date']} | {t['ticker']} | {side} | {t['quantity']:.4f} | "
                f"{t['price_usd']:.2f} | {_eur(t['value'])} | {_eur(t['fee'])} | "
                f"{t['decided_on']} ({_RATING_IT[t['rating']]}) |"
            )
    else:
        lines.append("Nessuna operazione finora.")

    lines += ["", "## Andamento giornaliero", "",
              "| Data | Conto agenti | Benchmark | Liquidità |", "|---|---:|---:|---:|"]
    for point in reversed(history):
        lines.append(f"| {point['date']} | {_eur(point['equity'])} | {_eur(point['benchmark'])} | "
                     f"{_eur(point['cash'])} |")

    lines += ["", "_Simulazione con denaro finto: nessun ordine reale viene inviato. "
              "Non è una consulenza finanziaria._", ""]
    return "\n".join(lines)
