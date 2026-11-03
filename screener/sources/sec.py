"""SEC EDGAR — bilanci ufficiali 10-K/10-Q in formato XBRL (gratis, API ufficiale).

Usato per dedurre i dati che Yahoo non fornisce e per citare il documento esatto
(numero di protocollo "accession") da cui il dato è stato ricavato.
"""
from __future__ import annotations

import logging
import time
from datetime import date

from ..config import SEC_USER_AGENT, http_get

log = logging.getLogger(__name__)
SRC = "SEC EDGAR"

_tickers: dict | None = None
_cache: dict = {}
_last_call = 0.0


def _get(url: str):
    global _last_call
    wait = 0.15 - (time.time() - _last_call)  # limite SEC: max 10 richieste/secondo
    if wait > 0:
        time.sleep(wait)
    _last_call = time.time()
    return http_get(url, headers={"User-Agent": SEC_USER_AGENT, "Accept-Encoding": "gzip, deflate"}).json()


def cik_for(ticker: str) -> str | None:
    global _tickers
    if _tickers is None:
        try:
            raw = _get("https://www.sec.gov/files/company_tickers.json")
            _tickers = {v["ticker"].upper().replace(".", "-"): str(v["cik_str"]).zfill(10) for v in raw.values()}
        except Exception as e:
            log.warning("SEC company_tickers non disponibile: %s", e)
            _tickers = {}
    return _tickers.get(ticker.upper())


def facts(ticker: str) -> dict | None:
    cik = cik_for(ticker)
    if not cik:
        return None
    if cik not in _cache:
        try:
            _cache[cik] = _get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json")
        except Exception as e:
            log.info("SEC facts %s: %s", ticker, e)
            _cache[cik] = None
    return _cache[cik]


def doc(cik: str, entry: dict) -> dict:
    accn = entry.get("accn", "")
    return {
        "label": f"SEC {entry.get('form', '')} — periodo chiuso {entry.get('end')} — "
                 f"protocollo {accn} (depositato {entry.get('filed', '')})",
        "url": f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accn.replace('-', '')}/",
    }


def _entries(f: dict, concepts: list[str], unit: str = "USD"):
    for c in concepts:
        node = f.get("facts", {}).get("us-gaap", {}).get(c)
        if node and unit in node.get("units", {}):
            yield c, node["units"][unit]


def annual(ticker: str, concepts: list[str], unit: str = "USD", flow: bool = True) -> list[tuple]:
    """Valori annuali da 10-K: [(fine_periodo, valore, entry, concetto)] dal più recente.

    flow=True per voci di conto economico/flussi (durata ~1 anno), False per voci patrimoniali.
    """
    f = facts(ticker)
    if not f:
        return []
    best: dict[str, tuple] = {}
    for concept, items in _entries(f, concepts, unit):
        for e in items:
            if not str(e.get("form", "")).startswith("10-K"):
                continue
            if flow:
                try:
                    days = (date.fromisoformat(e["end"]) - date.fromisoformat(e["start"])).days
                except Exception:
                    continue
                if not 350 <= days <= 380:
                    continue
            prev = best.get(e["end"])
            if prev is None or e.get("filed", "") > prev[2].get("filed", ""):
                best[e["end"]] = (e["end"], float(e["val"]), e, concept)
        if best:
            break  # usa il primo concetto che ha dati, per coerenza di definizione
    return sorted(best.values(), key=lambda x: x[0], reverse=True)


def latest_ratio(ticker: str, keywords: list[str], lo: float, hi: float) -> tuple | None:
    """Ultimo rapporto (unità 'pure' o percentuale) il cui nome contiene una delle parole chiave.

    Cerca in tutti i namespace (us-gaap e tag propri della società) e scarta i valori
    fuori dall'intervallo plausibile [lo, hi] (espresso come frazione, es. 0.04-0.30).
    """
    f = facts(ticker)
    if not f:
        return None
    found = []
    for ns, concepts in f.get("facts", {}).items():
        for name, node in concepts.items():
            low = name.lower()
            if not any(k.lower() in low for k in keywords):
                continue
            if any(x in low for x in ("minimum", "required", "wellcapitalized", "buffer", "adequacy")):
                continue
            for unit, items in node.get("units", {}).items():
                if unit not in ("pure", "percent"):
                    continue
                for e in items:
                    v = float(e["val"])
                    v = v / 100 if v > 1 else v
                    if lo <= v <= hi and str(e.get("form", "")).startswith("10-"):
                        found.append((e["end"], v, e, f"{ns}:{name}"))
    if not found:
        return None
    return max(found, key=lambda x: (x[0], x[2].get("filed", "")))


def cik_str(ticker: str) -> str:
    return cik_for(ticker) or ""
