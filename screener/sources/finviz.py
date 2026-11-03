"""Finviz Elite — export CSV ufficiale (richiede abbonamento Elite).

Attivo solo se esiste la variabile d'ambiente FINVIZ_AUTH (token Elite, da impostare
come secret del repository). Con l'account gratuito Finviz non offre export e vieta
la lettura automatica delle pagine: in quel caso questo modulo resta spento e la
cascata parte da Yahoo.
"""
from __future__ import annotations

import logging
import os
from io import StringIO

import pandas as pd

from ..config import GENERIC_UA, http_get
from ..model import Datum

log = logging.getLogger(__name__)
SRC = "Finviz Elite"
AUTH = os.environ.get("FINVIZ_AUTH", "").strip()

# Viste Finviz: 111 panoramica, 121 valutazione, 161 dati finanziari
VIEWS = (111, 121, 161)
FILTERS = {"S&P 500": "idx_sp500", "NASDAQ 100": "idx_ndx"}

# metrica interna -> possibili intestazioni della colonna nell'export
COLUMNS = {
    "pe_ttm": ["P/E"],
    "pe_fwd": ["Forward P/E", "Fwd P/E"],
    "roe": ["Return on Equity", "ROE"],
    "roic": ["Return on Invested Capital", "ROIC", "Return on Investment", "ROI"],
    "op_margin": ["Operating Margin", "Oper M"],
    "current_ratio": ["Current Ratio", "Curr R"],
    "de": ["Total Debt/Equity", "Debt/Eq"],
    "ev_ebitda": ["EV/EBITDA"],
    "ev_sales": ["EV/Sales"],
}
PCT = {"roe", "roic", "op_margin"}


def enabled() -> bool:
    return bool(AUTH)


def _num(x):
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return None
    s = str(x).replace("%", "").replace(",", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def fetch_all() -> dict[str, dict]:
    """{ticker: {colonna: valore}} per tutti i titoli dei due indici."""
    if not enabled():
        return {}
    data: dict[str, dict] = {}
    for idx, flt in FILTERS.items():
        for v in VIEWS:
            url = f"https://elite.finviz.com/export.ashx?v={v}&f={flt}&auth={AUTH}"
            try:
                df = pd.read_csv(StringIO(http_get(url, headers={"User-Agent": GENERIC_UA}).text))
            except Exception as e:
                log.warning("Finviz export v=%s %s: %s", v, idx, e)
                continue
            for rec in df.to_dict("records"):
                t = str(rec.get("Ticker", "")).replace(".", "-")
                if t:
                    data.setdefault(t, {}).update(rec)
    log.info("Finviz Elite: %d titoli", len(data))
    return data


def get(row: dict | None, metric: str, ticker: str) -> Datum | None:
    if not row:
        return None
    for col in COLUMNS.get(metric, []):
        if col in row:
            v = _num(row[col])
            if v is not None:
                return Datum(v=v, src=SRC, definition=f"Campo Finviz '{col}'",
                             docs=[{"label": f"Finviz {ticker}", "url": f"https://finviz.com/quote.ashx?t={ticker}"}])
    return None


def sector(row: dict | None) -> tuple[str, str] | None:
    if row and row.get("Sector"):
        return str(row["Sector"]), str(row.get("Industry", ""))
    return None
