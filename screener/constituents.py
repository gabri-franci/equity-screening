"""Composizione degli indici S&P 500 e NASDAQ 100, con storico ingressi/uscite.

Regola utente: non si elimina mai nulla. Un titolo uscito dall'indice resta nel
file data/membership.csv con active=False e la data di uscita.
"""
from __future__ import annotations

import logging
import re
from datetime import date
from io import StringIO

import pandas as pd

from .config import DATA_DIR, GENERIC_UA, http_get

log = logging.getLogger(__name__)

MEMBERSHIP_CSV = DATA_DIR / "membership.csv"
INDEXES = {
    "S&P 500": "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
    "NASDAQ 100": "https://en.wikipedia.org/wiki/Nasdaq-100",
}
COLUMNS = ["ticker", "index", "name", "gics_sector", "first_seen", "last_seen", "active", "removed_on"]


def to_yahoo(sym: str) -> str:
    """BRK.B -> BRK-B (formato Yahoo)."""
    return str(sym).strip().upper().replace(".", "-")


def _clean_cols(t: pd.DataFrame) -> pd.DataFrame:
    """Appiattisce intestazioni multiple e toglie le note tipo 'Ticker[3]'."""
    cols = []
    for c in t.columns:
        c = " ".join(str(x) for x in c) if isinstance(c, tuple) else str(c)
        cols.append(re.sub(r"\[.*?\]", "", c).strip())
    t = t.copy()
    t.columns = cols
    return t


def _col(t: pd.DataFrame, *keys: str) -> str | None:
    for k in keys:
        for c in t.columns:
            if k.lower() in c.lower():
                return c
    return None


def _pick_table(html: str, min_rows: int, max_rows: int) -> pd.DataFrame:
    seen = []
    for t in pd.read_html(StringIO(html)):
        t = _clean_cols(t)
        seen.append(f"{len(t)} righe {list(t.columns)[:6]}")
        if _col(t, "symbol", "ticker") and min_rows <= len(t) <= max_rows:
            return t
    raise ValueError("tabella dei costituenti non trovata. Tabelle viste: " + " | ".join(seen[:8]))


def fetch_current() -> dict[str, pd.DataFrame]:
    """Scarica l'elenco attuale dei titoli. Ritorna {indice: DataFrame(ticker, name, gics_sector)}.

    Se un indice non si riesce a leggere viene saltato: per quell'indice resta valido
    l'elenco salvato in data/membership.csv.
    """
    out = {}
    for idx, url in INDEXES.items():
        try:
            html = http_get(url, headers={"User-Agent": GENERIC_UA}).text
            lo, hi = (480, 520) if idx == "S&P 500" else (95, 110)
            t = _pick_table(html, lo, hi)
            sym = _col(t, "symbol", "ticker")
            name = _col(t, "security", "company", "name") or sym
            sector = _col(t, "sector")
            df = pd.DataFrame({
                "ticker": t[sym].map(to_yahoo),
                "name": t[name].astype(str),
                "gics_sector": t[sector].astype(str) if sector else "",
            })
            out[idx] = df[df["ticker"].str.len() > 0]
            log.info("%s: %d titoli", idx, len(out[idx]))
        except Exception as e:
            log.error("%s: elenco titoli non aggiornato (%s)", idx, e)
    return out


def load_membership() -> pd.DataFrame:
    if MEMBERSHIP_CSV.exists():
        df = pd.read_csv(MEMBERSHIP_CSV, dtype=str, keep_default_na=False)
        df["active"] = df["active"].map(lambda x: str(x) == "True")
        return df
    return pd.DataFrame(columns=COLUMNS)


def update_membership(current: dict[str, pd.DataFrame], today: str | None = None) -> pd.DataFrame:
    """Aggiorna lo storico senza mai cancellare righe."""
    today = today or date.today().isoformat()
    mem = load_membership()
    rows = {(r.ticker, r.index): r._asdict() for r in mem.itertuples(index=False)}
    for idx, df in current.items():
        present = set(df["ticker"])
        for r in df.itertuples(index=False):
            key = (r.ticker, idx)
            if key in rows:
                row = rows[key]
                row.update(name=r.name, gics_sector=r.gics_sector, last_seen=today)
                if not row["active"]:  # rientrato nell'indice
                    row.update(active=True, removed_on="")
            else:
                rows[key] = dict(ticker=r.ticker, index=idx, name=r.name, gics_sector=r.gics_sector,
                                 first_seen=today, last_seen=today, active=True, removed_on="")
        for key, row in rows.items():
            if key[1] == idx and key[0] not in present and row["active"]:
                row.update(active=False, removed_on=today)
                log.info("Uscito da %s: %s", idx, key[0])
    out = pd.DataFrame(list(rows.values()), columns=COLUMNS).sort_values(["index", "ticker"])
    return out.reset_index(drop=True)


def save_membership(df: pd.DataFrame) -> None:
    MEMBERSHIP_CSV.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(MEMBERSHIP_CSV, index=False)
