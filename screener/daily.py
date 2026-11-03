"""Aggiornamento giornaliero leggero: solo prezzi e notizie.

I dati di bilancio e i giudizi restano quelli dell'ultimo giro settimanale.

    python -m screener.daily
"""
from __future__ import annotations

import json
import logging
import time
from datetime import date, datetime, timezone

import pandas as pd

from . import run
from .metrics import price_block
from .sources import yahoo as yh

log = logging.getLogger("screener.daily")


def refresh(rec: dict) -> bool:
    import yfinance as yf

    t = yf.Ticker(rec["ticker"])
    start = (pd.Timestamp.today() - pd.DateOffset(years=5, days=20)).date().isoformat()
    for i in range(3):
        try:
            h = t.history(start=start, interval="1d", auto_adjust=False)
            if h is None or h.empty:
                raise RuntimeError("storico vuoto")
            c = h["Close"].dropna()
            c.index = pd.to_datetime(c.index).tz_localize(None).normalize()
            y = yh.YahooData(ticker=rec["ticker"], info={"currency": rec.get("currency", "USD")}, hist=c)
            rec["quote"] = price_block(y)
            news = yh.fetch_news(t)
            if news:
                rec["news"], rec["news_updated"] = news, date.today().isoformat()
            return True
        except Exception as e:
            log.debug("%s: %s", rec["ticker"], e)
            time.sleep(2 ** (i + 1))
    return False


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    data = json.loads(run.LATEST_JSON.read_text("utf-8"))
    ok = 0
    for i, rec in enumerate(data["records"], 1):
        if refresh(rec):
            ok += 1
        if i % 50 == 0:
            log.info("%d/%d", i, len(data["records"]))
        time.sleep(0.2)
    data["prices_updated"] = datetime.now(timezone.utc).isoformat(timespec="minutes")
    run.write_outputs(data)
    log.info("Prezzi e notizie aggiornati per %d titoli su %d", ok, len(data["records"]))


if __name__ == "__main__":
    main()
