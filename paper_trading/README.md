# Paper trading

Un conto finto da 10.000 € che segue ogni giorno le decisioni di TradingAgents su
AAPL, NVDA e MSFT, per vedere se i consigli avrebbero fatto guadagnare.

**Il risultato è in [`state/report.md`](state/report.md)**: valore del conto,
confronto con chi avesse comprato SPY e tenuto, posizioni, operazioni e decisioni.
Le motivazioni complete di ogni decisione sono in `state/decisions/<data>/<titolo>.md`.

## Come funziona

Ogni sera, dopo la chiusura di Wall Street, il workflow
[`paper-trading.yml`](../.github/workflows/paper-trading.yml):

1. esegue all'**apertura** della seduta gli ordini decisi la sera prima (mai alla
   chiusura su cui sono stati decisi: sarebbe barare);
2. chiede agli agenti (Gemini Flash, analisti market e news, 1 round di dibattito,
   report in italiano) un giudizio su ogni titolo, mostrando loro il conto attuale;
3. trasforma il giudizio in un ordine per l'apertura successiva;
4. valuta il conto alla chiusura e salva tutto nel repository.

Ogni titolo ha a disposizione un terzo del conto. Il giudizio decide quanta parte
di quella quota tenere investita:

| Giudizio | Quota investita |
|---|---|
| Buy | 100% |
| Overweight | 75% |
| Hold | invariata |
| Underweight | 25% |
| Sell | 0% |

Non ci sono vendite allo scoperto. Una decisione illeggibile (REVIEW) non fa
trading. Le azioni sono frazionabili, ogni operazione paga lo 0,1% di commissione
simulata, e i ribilanciamenti più piccoli del 5% della quota vengono saltati.
I prezzi sono in dollari e il conto in euro, al cambio EUR/USD del giorno.

Le impostazioni sono in [`config.json`](config.json).

## Da fare una volta

Su GitHub, in **Settings → Secrets and variables → Actions**, aggiungi il secret
`GOOGLE_API_KEY` con la tua chiave Gemini. Il workflow parte da solo nei giorni
feriali; per lanciarlo a mano: **Actions → Paper trading → Run workflow**.

## A mano

```bash
python -m paper_trading.run daily     # elabora l'ultima seduta chiusa
python -m paper_trading.run report    # stampa il report
python -m paper_trading.run daily --date 2026-10-20 --signals AAPL=Buy,NVDA=Sell
```

`--signals` sostituisce gli agenti con giudizi fissi, per provare la contabilità
senza consumare chiamate a Gemini. Una seduta già elaborata non viene rifatta. Se
tutte le analisi falliscono (per esempio una chiave non valida) la seduta non viene
registrata e il workflow risulta in errore.

Limiti: dividendi e frazionamenti azionari non sono gestiti. È una simulazione con
denaro finto, non una consulenza finanziaria.
