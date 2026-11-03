"""Dati macro per il WACC: tasso privo di rischio (FRED) e premio per il rischio azionario (Damodaran)."""
from __future__ import annotations

import logging
from io import StringIO

import pandas as pd

from ..config import GENERIC_UA, http_get
from ..model import Datum

log = logging.getLogger(__name__)

FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}"
DAMODARAN_URL = "https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/implpr.html"


def risk_free(series: str = "DGS10") -> Datum:
    url = FRED_CSV.format(sid=series)
    try:
        df = pd.read_csv(StringIO(http_get(url, retries=2, timeout=60).text))
        df.columns = ["date", "value"]
        df["value"] = pd.to_numeric(df["value"], errors="coerce")
        df = df.dropna()
        last = df.iloc[-1]
        return Datum(v=round(float(last["value"]), 3), src="FRED (Federal Reserve St. Louis)",
                     definition="Rendimento Treasury USA a 10 anni, ultimo dato giornaliero",
                     docs=[{"label": f"FRED {series} al {last['date']}", "url": f"https://fred.stlouisfed.org/series/{series}"}])
    except Exception as e:
        log.warning("FRED non disponibile, uso Yahoo ^TNX: %s", e)
    try:
        import yfinance as yf
        h = yf.Ticker("^TNX").history(period="1mo")["Close"].dropna()
        return Datum(v=round(float(h.iloc[-1]), 3), src="Yahoo Finance (^TNX)", deduced=True,
                     definition="Rendimento Treasury USA a 10 anni (indice CBOE ^TNX)",
                     why=f"FRED non raggiungibile: uso la chiusura del {h.index[-1].date()} dell'indice ^TNX, "
                         f"che replica il rendimento del Treasury a 10 anni.",
                     docs=[{"label": "Yahoo Finance ^TNX", "url": "https://finance.yahoo.com/quote/%5ETNX"}])
    except Exception as e:
        log.warning("Anche ^TNX non disponibile: %s", e)
        return Datum(v=None, note="tasso risk free non disponibile (FRED e Yahoo ^TNX)")


def equity_risk_premium(value_pct: float, value_date: str) -> Datum:
    """Premio per il rischio azionario: valore pubblicato da Damodaran, salvato in config.

    Non viene letto in automatico dalla pagina perché il formato non è stabile e un
    numero sbagliato falserebbe tutti i WACC. Va aggiornato a mano (1 numero al mese).
    """
    return Datum(v=value_pct, src="Damodaran (NYU Stern) — valore in config/criteria.yaml", deduced=True,
                 definition="Premio per il rischio azionario implicito S&P 500",
                 why=f"Valore {value_pct}% pubblicato da Damodaran per {value_date}, copiato in configurazione.",
                 docs=[{"label": "Damodaran — Implied ERP", "url": DAMODARAN_URL}])
