"""Applicazione dei criteri del documento e confronto con la mediana di settore."""
from __future__ import annotations

import statistics

OPS = {
    "gt": lambda x, t: x > t, "gte": lambda x, t: x >= t,
    "lt": lambda x, t: x < t, "lte": lambda x, t: x <= t,
}


def match_level(value: float, levels: list[dict]) -> str:
    for lv in levels:
        if all(OPS[op](value, th) for op, th in lv.items() if op in OPS):
            return lv["label"]
    return ""


def band_position(value: float, band: dict) -> str:
    if value < band["min"]:
        return "Sotto fascia"
    if value > band["max"]:
        return "Sopra fascia"
    return "Nella fascia"


def roic_trend(series: list[float], rule: dict) -> str:
    if len(series) < 3 or min(series) <= rule["min_each_year"]:
        return ""
    if series[-1] >= series[0] + rule["growth_pp"]:
        return rule["label_growing"]
    if max(series) - min(series) <= rule["stable_band_pp"]:
        return rule["label_stable"]
    return ""


def rate(metric: str, datum: dict, group: str, cfg: dict) -> dict:
    """Ritorna {"label": ..., "band": {...}} per una metrica. Nessun giudizio se il valore manca."""
    crit = cfg["criteria"].get(metric, {})
    v = datum.get("v")
    out: dict = {}
    if v is None:
        return out
    if crit.get("unit") == "bool":
        out["label"] = "Sì" if v else "No"
        return out
    if "only_groups" in crit and group not in crit["only_groups"]:
        return out  # soglia valida solo per alcuni settori (es. regola EV/Sales: industriali)
    if "trend" in crit:
        out["label"] = roic_trend(datum.get("series", []), crit["trend"])
        return out
    if "levels" in crit:
        out["label"] = match_level(v, crit["levels"])
    if "band" in crit:
        band = cfg["groups"].get(group, {}).get(crit["band"])
        if band:
            out["band"] = {"min": band["min"], "max": band["max"], "origin": band.get("origin", ""),
                           "why": band.get("why", ""), "group": cfg["groups"][group]["name"]}
            out["label"] = band_position(v, band)
    return out


def peer_medians(records: list[dict]) -> dict:
    """Mediana per gruppo di settore e metrica (aziende uniche, solo titoli attivi e aggiornati)."""
    buckets: dict = {}
    for r in records:
        if r.get("stale"):
            continue
        for m, d in r["metrics"].items():
            v = d.get("v")
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                buckets.setdefault(r["group"], {}).setdefault(m, []).append(v)
    return {g: {m: {"median": round(statistics.median(vs), 2), "n": len(vs)} for m, vs in ms.items() if len(vs) >= 3}
            for g, ms in buckets.items()}
