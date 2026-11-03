"""Yahoo Finance (libreria yfinance, accesso non ufficiale)."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

import pandas as pd

log = logging.getLogger(__name__)
SRC = "Yahoo Finance"


@dataclass
class YahooData:
    ticker: str
    info: dict = field(default_factory=dict)
    inc_a: pd.DataFrame = field(default_factory=pd.DataFrame)   # conto economico annuale
    bal_a: pd.DataFrame = field(default_factory=pd.DataFrame)   # stato patrimoniale annuale
    cf_a: pd.DataFrame = field(default_factory=pd.DataFrame)    # rendiconto finanziario annuale
    inc_q: pd.DataFrame = field(default_factory=pd.DataFrame)   # trimestrali
    bal_q: pd.DataFrame = field(default_factory=pd.DataFrame)
    cf_q: pd.DataFrame = field(default_factory=pd.DataFrame)
    cf_ttm: pd.DataFrame = field(default_factory=pd.DataFrame)  # rendiconto finanziario ultimi 12 mesi
    hist: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))  # prezzi di chiusura giornalieri, 5 anni

    def url(self, page: str = "") -> str:
        return f"https://finance.yahoo.com/quote/{self.ticker}/{page}".rstrip("/")


def fetch(ticker: str, retries: int = 3) -> YahooData:
    import yfinance as yf

    last = None
    for i in range(retries):
        try:
            t = yf.Ticker(ticker)
            d = YahooData(ticker=ticker, info=t.info or {})
            for attr, name in (("inc_a", "income_stmt"), ("bal_a", "balance_sheet"), ("cf_a", "cashflow"),
                               ("inc_q", "quarterly_income_stmt"), ("bal_q", "quarterly_balance_sheet"),
                               ("cf_q", "quarterly_cashflow"), ("cf_ttm", "ttm_cashflow")):
                try:
                    df = getattr(t, name)
                    if df is not None and not df.empty:
                        setattr(d, attr, df.sort_index(axis=1, ascending=False))
                except Exception as e:  # una tabella mancante non blocca il resto
                    log.debug("%s %s: %s", ticker, name, e)
            try:
                h = t.history(period="5y", interval="1d", auto_adjust=False)
                if h is not None and not h.empty:
                    c = h["Close"].dropna()
                    c.index = pd.to_datetime(c.index).tz_localize(None).normalize()
                    d.hist = c
            except Exception as e:
                log.debug("%s history: %s", ticker, e)
            if not d.info or d.info.get("quoteType") is None and d.inc_a.empty:
                raise RuntimeError("risposta vuota")
            return d
        except Exception as e:
            last = e
            time.sleep(2 ** (i + 1))
    raise RuntimeError(f"Yahoo {ticker}: {last}")


# ----------------------------------------------------------------- helpers
def row(df: pd.DataFrame, *names: str) -> pd.Series | None:
    """Prima riga disponibile tra i nomi indicati (Yahoo cambia talvolta le etichette)."""
    if df is None or df.empty:
        return None
    for n in names:
        if n in df.index:
            s = pd.to_numeric(df.loc[n], errors="coerce")
            if isinstance(s, pd.DataFrame):
                s = s.iloc[0]
            if s.notna().any():
                return s
    return None


def latest(df: pd.DataFrame, *names: str):
    """(valore, data) della colonna più recente non vuota."""
    s = row(df, *names)
    if s is None:
        return None, None
    s = s.dropna()
    if s.empty:
        return None, None
    return float(s.iloc[0]), s.index[0]


def ttm(df_q: pd.DataFrame, *names: str):
    """Somma degli ultimi 4 trimestri. Ritorna (valore, [date]) o (None, None)."""
    s = row(df_q, *names)
    if s is None:
        return None, None
    s = s.dropna()
    if len(s) < 4:
        return None, None
    last4 = s.iloc[:4]
    return float(last4.sum()), list(last4.index)


def annual_series(df_a: pd.DataFrame, *names: str) -> dict:
    """{data fine esercizio: valore} in ordine dal più recente."""
    s = row(df_a, *names)
    if s is None:
        return {}
    return {k: float(v) for k, v in s.dropna().items()}


def d(ts) -> str:
    try:
        return pd.Timestamp(ts).date().isoformat()
    except Exception:
        return str(ts)
