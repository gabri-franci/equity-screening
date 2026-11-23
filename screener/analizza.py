"""Analisi su richiesta dalla dashboard (barra "Cerca e analizza titoli").

Due modalità, avviate dal workflow .github/workflows/analizza.yml:

    # anteprima: cerca i titoli, calcola tutte le metriche, scrive previews/<id>.json
    python -m screener.analizza preview --request-id abc --payload "Hermes|Copart|ASML"

    # aggiunta: calcola i titoli scelti e li inserisce nei dati della dashboard e nelle liste
    python -m screener.analizza add --request-id abc --payload "RMS.PA:watchlist|ASML:da_valutare"
"""
from __future__ import annotations

import argparse
import json
import logging
import time
from datetime import date, datetime, timedelta, timezone

from . import run, scoring
from .config import ROOT, load_config
from .model import Datum
from .sources import finviz

log = logging.getLogger("screener.analizza")
PREVIEW_DIR = ROOT / "previews"
LISTS = {"watchlist": run.WATCHLIST_JSON, "da_valutare": run.DA_VALUTARE_JSON}


def resolve(query: str) -> tuple[str | None, list[dict]]:
    """Da nome o ticker al simbolo Yahoo. Ritorna (simbolo scelto, candidati)."""
    import yfinance as yf

    q = query.strip()
    cands = []
    try:
        for x in yf.Search(q, max_results=8, news_count=0).quotes or []:
            if x.get("quoteType") in ("EQUITY", "ETF") and x.get("symbol"):
                cands.append({"symbol": x["symbol"], "name": x.get("longname") or x.get("shortname") or x["symbol"],
                              "exchange": x.get("exchDisp") or x.get("exchange", ""), "type": x.get("quoteType")})
    except Exception as e:
        log.warning("ricerca '%s': %s", q, e)
    exact = next((c for c in cands if c["symbol"].upper() == q.upper()), None)
    if exact:
        return exact["symbol"], cands
    if not cands:
        # magari è già un simbolo valido che la ricerca non trova
        try:
            info = yf.Ticker(q.upper()).info or {}
            if info.get("quoteType"):
                return q.upper(), [{"symbol": q.upper(), "name": info.get("longName") or q.upper(),
                                    "exchange": info.get("fullExchangeName", ""), "type": info.get("quoteType")}]
        except Exception:
            pass
        return None, []
    equities = [c for c in cands if c["type"] == "EQUITY"]
    return (equities or cands)[0]["symbol"], cands


def load_latest() -> dict:
    return json.loads(run.LATEST_JSON.read_text("utf-8"))


def compute(symbol: str, latest: dict, cfg: dict) -> dict:
    prev = {r["ticker"]: r for r in latest["records"]}
    old = prev.get(symbol, {})
    u = {"ticker": symbol, "name": old.get("name", symbol), "gics_sector": "",
         "memberships": {m["index"]: m for m in old.get("memberships", [])}, "secondary": set(old.get("secondary", []))}
    macro = {k: Datum(**{kk: vv for kk, vv in v.items() if kk in Datum.__dataclass_fields__})
             for k, v in latest.get("macro", {}).items()}
    rec = run.evaluate(u, finviz.fetch_all() if finviz.enabled() else {}, macro, cfg, {}, date.today().isoformat())
    meds = latest.get("peer_medians", {})
    for m, d in rec["metrics"].items():
        d.update(scoring.rate(m, d, rec["group"], cfg, rec["metrics"]))
        pm = meds.get(rec["group"], {}).get(m)
        if pm:
            d["peer"] = pm
    return rec


def preview(request_id: str, queries: list[str]) -> None:
    cfg = load_config()
    latest = load_latest()
    results = []
    for q in queries:
        sym, cands = resolve(q)
        item = {"query": q, "symbol": sym, "candidates": cands[:6]}
        if not sym:
            item["status"] = "not_found"
        else:
            try:
                item["record"] = compute(sym, latest, cfg)
                item["status"] = "ok"
            except Exception as e:
                log.exception("analisi %s", sym)
                item.update(status="error", error=str(e)[:300])
        results.append(item)
        time.sleep(0.3)
    PREVIEW_DIR.mkdir(exist_ok=True)
    # le anteprime più vecchie di 2 giorni non servono più
    cutoff = datetime.now(timezone.utc) - timedelta(days=2)
    for f in PREVIEW_DIR.glob("*.json"):
        if datetime.fromtimestamp(f.stat().st_mtime, timezone.utc) < cutoff:
            f.unlink()
    (PREVIEW_DIR / f"{request_id}.json").write_text(json.dumps(
        {"request_id": request_id, "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
         "results": results}, ensure_ascii=False, default=str), "utf-8")
    log.info("Anteprima %s: %d titoli", request_id, len(results))


def add(pairs: list[tuple[str, str]]) -> None:
    cfg = load_config()
    latest = load_latest()
    by_t = {r["ticker"]: i for i, r in enumerate(latest["records"])}
    # Le liste (watchlist/da_valutare.json) le aggiorna la dashboard stessa: qui si calcolano solo i dati,
    # così una stellina messa mentre questo calcolo gira non viene sovrascritta.
    for sym, lst in pairs:
        if sym in by_t:
            continue  # già nei dati
        rec = compute(sym, latest, cfg)
        latest["records"].append(rec)
        by_t[sym] = len(latest["records"]) - 1
    latest["records"].sort(key=lambda r: r["ticker"])
    run.write_outputs(latest)
    log.info("Aggiunti: %s", pairs)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["preview", "add"])
    ap.add_argument("--request-id", default="manual")
    ap.add_argument("--payload", required=True)
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    items = [x.strip() for x in a.payload.split("|") if x.strip()][:20]
    if a.mode == "preview":
        preview(a.request_id, items)
    else:
        pairs = []
        for x in items:
            sym, _, lst = x.rpartition(":")
            if sym and lst in LISTS:
                pairs.append((sym.strip().upper(), lst))
        add(pairs)


if __name__ == "__main__":
    main()
