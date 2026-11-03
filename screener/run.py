"""Esecuzione settimanale completa.

    python -m screener.run                 # tutti i titoli
    python -m screener.run --limit 20      # prova veloce sui primi 20
    python -m screener.run --tickers AAPL,JPM,O
"""
from __future__ import annotations

import argparse
import gzip
import json
import logging
import os
import time
from datetime import date, datetime, timezone

import pandas as pd

from . import constituents, scoring
from .config import DATA_DIR, DOCS_DIR, ROOT, load_config
from .metrics import SECTOR_IT, Engine, group_for, norm_sector, price_block
from .model import Datum
from .sources import finviz, macro, sec
from .sources import yahoo as yh

log = logging.getLogger("screener")
LATEST_JSON = DOCS_DIR / "data" / "latest.json"
WATCHLIST_JSON = ROOT / "watchlist" / "watchlist.json"
REPO = os.environ.get("GITHUB_REPOSITORY") or "gabri-franci/equity-screening"
HISTORY_DIR = DATA_DIR / "history"


def load_previous() -> dict:
    if LATEST_JSON.exists():
        try:
            return {r["ticker"]: r for r in json.loads(LATEST_JSON.read_text("utf-8"))["records"]}
        except Exception as e:
            log.warning("latest.json illeggibile: %s", e)
    return {}


def build_universe(mem: pd.DataFrame, cfg: dict, only: list[str] | None) -> dict[str, dict]:
    """Un record per azienda. Le classi secondarie (GOOG, FOX, NWS) confluiscono nella primaria."""
    dual = cfg.get("dual_class", {})
    uni: dict[str, dict] = {}
    for r in mem.itertuples(index=False):
        primary = dual.get(r.ticker, r.ticker)
        u = uni.setdefault(primary, {"ticker": primary, "name": r.name, "gics_sector": r.gics_sector,
                                     "memberships": {}, "secondary": set()})
        if primary != r.ticker:
            u["secondary"].add(r.ticker)
        else:
            u["name"] = r.name or u["name"]
        m = u["memberships"].get(r.index)
        active = bool(r.active)
        # se una delle due classi è attiva nell'indice, l'azienda è attiva
        if m is None or (active and not m["active"]):
            u["memberships"][r.index] = {"index": r.index, "active": active, "first_seen": r.first_seen,
                                         "removed_on": r.removed_on}
    if only:
        uni = {k: v for k, v in uni.items() if k in only}
    return uni


def evaluate(u: dict, fv_all: dict, macro_d: dict, cfg: dict, prev: dict, today: str) -> dict:
    t = u["ticker"]
    fv_row = fv_all.get(t)
    try:
        y = yh.fetch(t)
    except Exception as e:
        log.warning("%s: Yahoo non disponibile (%s)", t, e)
        y = None
    if y is None or not y.info:
        if t in prev:  # non si elimina mai nulla: si conserva l'ultimo dato disponibile
            rec = dict(prev[t])
            rec["stale"] = True
            rec.setdefault("stale_since", prev[t].get("updated", today))
            rec["memberships"] = list(u["memberships"].values())
            rec["secondary"] = sorted(u["secondary"])
            return rec
        y = yh.YahooData(ticker=t)

    info = y.info
    fv_sec = finviz.sector(fv_row)
    if fv_sec:
        sector, industry, sector_src = norm_sector(fv_sec[0]), fv_sec[1], "Finviz Elite"
    elif info.get("sector"):
        sector, industry, sector_src = norm_sector(info["sector"]), info.get("industry", ""), "Yahoo Finance (stessa classificazione Finviz)"
    else:
        sector, industry, sector_src = norm_sector(u["gics_sector"]), "", "Wikipedia (GICS)"
    group = group_for(t, sector, industry, cfg)
    skip_key = group if group in cfg["skip_by_group"] else "default"
    skip = set(cfg["skip_by_group"][skip_key])

    metrics = Engine(t, y, fv_row, macro_d, cfg).run(skip)
    return {
        "ticker": t, "name": info.get("longName") or info.get("shortName") or u["name"],
        "sector": sector, "sector_it": SECTOR_IT.get(sector, sector or "Non classificato"), "sector_src": sector_src,
        "industry": industry, "group": group, "group_name": cfg["groups"].get(group, {}).get("name", group),
        "tech_ai": group == "tech_ai", "cik": sec.cik_str(t),
        "price": info.get("currentPrice") or info.get("regularMarketPrice"), "currency": info.get("currency", "USD"),
        "quote": price_block(y),
        "news": y.news, "news_updated": today,
        "market_cap": info.get("marketCap"),
        "memberships": list(u["memberships"].values()), "secondary": sorted(u["secondary"]),
        "updated": today, "stale": False,
        "metrics": {m: d.to_dict() for m, d in metrics.items()},
    }


def apply_scores(records: list[dict], cfg: dict) -> dict:
    meds = scoring.peer_medians(records)
    for r in records:
        for m, d in r["metrics"].items():
            for k in ("label", "band", "peer"):
                d.pop(k, None)
            d.update(scoring.rate(m, d, r["group"], cfg))
            pm = meds.get(r["group"], {}).get(m)
            if pm:
                d["peer"] = pm
    return meds


def save_history(records: list[dict], today: str) -> None:
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    rows = []
    for r in records:
        q = r.get("quote") or {}
        rows.append({"date": today, "ticker": r["ticker"], "metric": "price", "value": q.get("price"),
                     "label": "", "source": q.get("src", ""), "deduced": False, "stale": r.get("stale", False)})
        for m, d in r["metrics"].items():
            rows.append({"date": today, "ticker": r["ticker"], "metric": m, "value": d.get("v"),
                         "label": d.get("label", ""), "source": d.get("src", ""), "deduced": d.get("deduced", False),
                         "stale": r.get("stale", False)})
    with gzip.open(HISTORY_DIR / f"{today}.csv.gz", "wt", encoding="utf-8") as f:
        pd.DataFrame(rows).to_csv(f, index=False)


def load_watchlist() -> list[str]:
    try:
        return sorted(set(json.loads(WATCHLIST_JSON.read_text("utf-8")).get("tickers", [])))
    except Exception:
        return []


def write_outputs(out: dict) -> None:
    """Scrive latest.json, Excel e dashboard (usato sia dal giro settimanale sia da quello giornaliero)."""
    out["watchlist"] = load_watchlist()
    out["repo"] = REPO
    LATEST_JSON.parent.mkdir(parents=True, exist_ok=True)
    LATEST_JSON.write_text(json.dumps(out, ensure_ascii=False, default=str), "utf-8")
    from . import dashboard, excel
    excel.write(out, DOCS_DIR / "screening.xlsx")
    dashboard.write(out, DOCS_DIR / "index.html")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int)
    ap.add_argument("--tickers")
    ap.add_argument("--skip-constituents", action="store_true", help="usa membership.csv senza riscaricare")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = load_config()
    today = date.today().isoformat()

    mem = constituents.load_membership()
    if not a.skip_constituents:
        try:
            current = constituents.fetch_current()
            if current:
                mem = constituents.update_membership(current, today)
                constituents.save_membership(mem)
        except Exception as e:
            log.error("Aggiornamento composizione indici fallito, uso l'elenco salvato: %s", e)
    if mem.empty:
        raise SystemExit("Nessun elenco titoli disponibile (né online né in data/membership.csv)")

    w = cfg["wacc"]
    macro_d = {"rf": macro.risk_free(w["risk_free_series"]), "erp": macro.equity_risk_premium(w["erp_pct"], w["erp_date"])}
    fv_all = finviz.fetch_all()
    prev = load_previous()
    only = [x.strip().upper() for x in a.tickers.split(",")] if a.tickers else None
    uni = build_universe(mem, cfg, only)
    tickers = sorted(uni)[: a.limit] if a.limit else sorted(uni)

    records = []
    for i, t in enumerate(tickers, 1):
        log.info("[%d/%d] %s", i, len(tickers), t)
        records.append(evaluate(uni[t], fv_all, macro_d, cfg, prev, today))
        time.sleep(0.3)
    # titoli già valutati in passato ma non in questo giro (es. --limit): restano nel file
    if a.limit or only:
        done = {r["ticker"] for r in records}
        records += [r for t, r in prev.items() if t not in done]

    meds = apply_scores(records, cfg)
    out = {
        "generated": datetime.now(timezone.utc).isoformat(timespec="minutes"),
        "macro": {k: v.to_dict() for k, v in macro_d.items()},
        "finviz_elite": finviz.enabled(),
        "criteria": cfg["criteria"], "groups": cfg["groups"], "peer_medians": meds,
        "records": sorted(records, key=lambda r: r["ticker"]),
    }
    save_history(records, today)
    write_outputs(out)
    ok = sum(1 for r in records if not r.get("stale"))
    log.info("Completato: %d titoli aggiornati, %d con dati precedenti", ok, len(records) - ok)


if __name__ == "__main__":
    main()
