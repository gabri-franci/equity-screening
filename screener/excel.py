"""Export Excel: un foglio per mercato (ordinato per settore), dati dedotti, composizione indici."""
from __future__ import annotations

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

MARKETS = ["S&P 500", "NASDAQ 100"]
PRICE_CHG = ["1G", "1S", "1M", "3M", "6M", "YTD", "1A", "3A", "5A"]
FILL = {"Molto buono": "C6EFCE", "Molto bene": "C6EFCE", "Buono": "E2F0D9", "Bene": "E2F0D9",
        "Nella fascia": "E2F0D9", "Sì": "E2F0D9", "Sopra fascia": "FCE4D6", "Sotto fascia": "DDEBF7", "No": "FCE4D6"}
HEAD = Font(bold=True, color="FFFFFF")
HEAD_FILL = PatternFill("solid", fgColor="1F3864")


def _status(r: dict, market: str) -> str:
    m = next((x for x in r["memberships"] if x["index"] == market), None)
    if not m:
        return ""
    return "In indice" if m["active"] else f"Uscito il {m['removed_on']}"


def _other(r: dict, market: str) -> str:
    return ", ".join(x["index"] for x in r["memberships"] if x["index"] != market and x["active"])


def _header(ws, cols):
    ws.append(cols)
    for c in ws[1]:
        c.font, c.fill = HEAD, HEAD_FILL
        c.alignment = Alignment(wrap_text=True, vertical="center")
    ws.freeze_panes = "E2"


def write(data: dict, path) -> None:
    crit = data["criteria"]
    metrics = [m for m in crit]
    wb = Workbook()
    wb.remove(wb.active)
    for market in MARKETS:
        ws = wb.create_sheet(market)
        wl = set(data.get("watchlist", []))
        cols = ["Settore", "Ticker", "Watchlist", "Nome", "Gruppo valutazione", "Stato", "Anche in", "Altre classi", "Aggiornato",
                "Prezzo", "Valuta", "Data prezzo"] + [f"Var. {p} %" for p in PRICE_CHG]
        for m in metrics:
            cols += [crit[m]["name"], "Giudizio"]
        _header(ws, cols)
        recs = [r for r in data["records"] if any(x["index"] == market for x in r["memberships"])]
        for r in sorted(recs, key=lambda r: (r["sector_it"], r["ticker"])):
            row = [r["sector_it"], r["ticker"], "★" if r["ticker"] in wl else "", r["name"], r["group_name"], _status(r, market), _other(r, market),
                   ", ".join(r.get("secondary", [])),
                   r["updated"] + (" (dati non aggiornati)" if r.get("stale") else "")]
            q = r.get("quote") or {}
            row += [q.get("price"), q.get("currency", ""), q.get("date", "")]
            row += [(q.get("changes") or {}).get(p) for p in PRICE_CHG]
            for m in metrics:
                d = r["metrics"].get(m)
                if d is None:
                    row += ["n.a.", ""]
                elif d.get("v") is None:
                    row += ["n.c.", d.get("note", "")]
                else:
                    v = d["v"]
                    row += ["Sì" if v is True else "No" if v is False else v, d.get("label", "")]
            ws.append(row)
            for i, m in enumerate(metrics):
                cell = ws.cell(row=ws.max_row, column=len(cols) - 2 * len(metrics) + 2 * i + 2)
                if cell.value in FILL:
                    cell.fill = PatternFill("solid", fgColor=FILL[cell.value])
        for i in range(1, len(cols) + 1):
            ws.column_dimensions[get_column_letter(i)].width = 14
        ws.column_dimensions["C"].width = 10
        ws.column_dimensions["D"].width = 28
        ws.auto_filter.ref = ws.dimensions

    ws = wb.create_sheet("Dati dedotti")
    _header(ws, ["Ticker", "Metrica", "Valore", "Fonte", "Definizione", "Ragionamento", "Documenti di origine"])
    for r in data["records"]:
        for m, d in r["metrics"].items():
            if d.get("deduced"):
                ws.append([r["ticker"], crit.get(m, {}).get("name", m), d.get("v"), d.get("src", ""),
                           d.get("definition", ""), d.get("why", "") or d.get("note", ""),
                           "\n".join(f"{x['label']} — {x['url']}" for x in d.get("docs", []) if x)])
    for col, w in zip("ABCDEFG", (8, 22, 10, 22, 40, 70, 70)):
        ws.column_dimensions[col].width = w

    ws = wb.create_sheet("Composizione indici")
    _header(ws, ["Ticker", "Indice", "Nome", "Primo ingresso rilevato", "Stato", "Data uscita"])
    for r in data["records"]:
        for x in r["memberships"]:
            ws.append([r["ticker"], x["index"], r["name"], x["first_seen"], "Attivo" if x["active"] else "Uscito", x["removed_on"]])

    # storico prezzi settimanale (chiusura del venerdì), una colonna per titolo
    ws = wb.create_sheet("Storico prezzi")
    recs = [r for r in data["records"] if (r.get("quote") or {}).get("weekly")]
    dates = sorted({d for r in recs for d, _ in r["quote"]["weekly"]})
    _header(ws, ["Data (venerdì)"] + [r["ticker"] for r in recs])
    ws.freeze_panes = "B2"
    lookup = [{d: v for d, v in r["quote"]["weekly"]} for r in recs]
    for d in dates:
        ws.append([f"{str(d)[:4]}-{str(d)[4:6]}-{str(d)[6:]}"] + [lk.get(d) for lk in lookup])

    ws = wb.create_sheet("Note")
    ws.append(["Generato", data["generated"]])
    for k, d in data["macro"].items():
        ws.append([{"rf": "Tasso privo di rischio (%)", "erp": "Premio rischio azionario (%)"}[k], d.get("v"), d.get("src", ""), d.get("why", "")])
    ws.append(["Finviz Elite attivo", "Sì" if data["finviz_elite"] else "No (cascata da Yahoo)"])
    ws.append(["Legenda", "n.c. = non calcolabile (nessun giudizio); n.a. = metrica non applicabile al settore"])
    wb.save(path)
