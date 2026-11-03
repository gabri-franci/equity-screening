# Equity Screening — S&P 500 e NASDAQ 100

Database settimanale dei titoli di **S&P 500** e **NASDAQ 100**, valutati con i criteri del
documento *Equity_Screening_v1* (redditività, crescita, solidità, valutazione).

- **Dashboard**: `docs/index.html`, organizzata per Mercato → Settore → Titolo, con pulsante "Scarica Excel".
- **Excel**: `docs/screening.xlsx`: un foglio per mercato, il foglio "Dati dedotti" e la composizione degli indici.
- **Storico**: `data/history/AAAA-MM-GG.csv.gz` (una fotografia per settimana) e `data/membership.csv`
  (ingressi e uscite dagli indici). **Non si cancella mai nulla.**

## Come funziona

Ogni sabato GitHub Actions esegue `python -m screener.run`:

1. aggiorna l'elenco dei titoli degli indici (fonte Wikipedia). I titoli usciti restano, con la data di uscita;
2. per ogni azienda raccoglie i dati seguendo la cascata delle fonti;
3. calcola le metriche, applica i criteri e confronta ogni titolo con la mediana del suo settore;
4. salva storico, Excel e dashboard nel repository.

### Cascata delle fonti

| Ordine | Fonte | Modalità |
|---|---|---|
| 1 | Finviz Elite | Solo con abbonamento Elite: secret `FINVIZ_AUTH` |
| 2 | Yahoo Finance | Campo fornito direttamente |
| 3 | Yahoo Finance | Calcolato dai bilanci → **dato dedotto** |
| 4 | SEC EDGAR (10-K ufficiali) | Calcolato → **dato dedotto**, con numero di protocollo del documento |

Koyfin, TradingView, Investing, Morningstar, FactSet e roic.ai non hanno un accesso
automatico consentito, quindi servono per le verifiche manuali.

Ogni dato dedotto mostra nella dashboard un **box "Dato dedotto"** con la formula, i numeri
usati e i link ai documenti di origine.

### Regole principali

- **Periodo**: gli indicatori puntuali usano gli ultimi 12 mesi (somma degli ultimi 4 trimestri),
  i CAGR usano gli ultimi 3 esercizi chiusi.
- **Valori negativi o non calcolabili** (P/E con perdite, CAGR con base negativa, cassa netta):
  la dashboard mostra il valore o "n.c." con la spiegazione, **senza giudizio**.
- **Banche e assicurazioni**: ROIC, EBITDA, Current Ratio ed EV/EBITDA sono sostituiti da ROTE,
  P/TBV e CET1 (banche) e da P/B e Combined Ratio (assicurazioni).
- **REIT**: si usa il P/FFO al posto del P/E.
- **Tech & AI**: comprende il settore Technology più GOOGL, META, AMZN, NFLX, TSLA e UBER.
- **Doppie classi di azioni** (GOOG, FOX, NWS): valutate una volta sola, sotto la classe principale.
- **Titoli presenti in entrambi gli indici**: compaiono in tutti e due i mercati con l'etichetta "anche in…".

Tutte le soglie e le fasce di settore stanno in `config/criteria.yaml`, con l'origine di ciascuna:
dal documento oppure proposta, con la motivazione.

### WACC

Il WACC si calcola con il CAPM:

- **Costo del capitale proprio (Ke)** = tasso privo di rischio + β × premio per il rischio azionario.
- **Costo del debito (Kd)** = interessi TTM ÷ debito.
- **Pesi** = valori di mercato.

Le fonti:

- **Tasso privo di rischio**: Treasury USA a 10 anni, scaricato da FRED.
- **Beta**: Yahoo, calcolato su 5 anni con dati mensili.
- **Premio per il rischio azionario**: valore pubblicato da Damodaran, che va **aggiornato a mano una volta al mese**
  in `config/criteria.yaml` (campi `erp_pct` e `erp_date`), da https://pages.stern.nyu.edu/~adamodar/

## Configurazione (una volta sola)

1. **Secret SEC**: SEC EDGAR chiede di identificarsi.
   - Apri Settings → Secrets and variables → Actions → **New repository secret**.
   - Nome: `SEC_USER_AGENT`.
   - Valore: un nome e una tua email, per esempio `EquityScreening nome@esempio.com`.
2. **Permessi di Actions**: in Settings → Actions → General → *Workflow permissions* seleziona
   **Read and write permissions**.
3. **Dashboard online** (GitHub Pages): in Settings → Pages scegli *Source*: `Deploy from a branch`,
   branch `main`, cartella `/docs`.
   - Funziona su repository pubblici, oppure su repository privati con un piano GitHub a pagamento.
   - In alternativa scarica `docs/index.html` e aprilo dal computer: funziona anche offline.
4. **Primo avvio**: tab Actions → *Aggiornamento settimanale* → **Run workflow**.
   - Per una prova veloce scrivi `20` nel campo *limit*.
   - Lasciandolo vuoto analizza tutti i titoli (circa 600, richiede 30-60 minuti).
5. *(Opzionale)* Con Finviz Elite aggiungi il secret `FINVIZ_AUTH` con il tuo token di export.

## Uso locale

```bash
pip install -r requirements.txt
python -m pytest -q tests                      # test offline con dati simulati
python -m screener.run --tickers AAPL,JPM,O    # prova su pochi titoli
```

> Strumento di analisi: non è una raccomandazione di investimento.
