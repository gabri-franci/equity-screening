"""Export Excel: un foglio per mercato (ordinato per settore), dati dedotti, composizione indici."""
from __future__ import annotations

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

MARKETS = ["S&P 500", "NASDAQ 100"]
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
    ws.freeze_panes = "D2"


def write(data: dict, path) -> None:
    crit = data["criteria"]
    metrics = [m for m in crit]
    wb = Workbook()
    wb.remove(wb.active)
    for market in MARKETS:
        ws = wb.create_sheet(market)
        cols = ["Settore", "Ticker", "Nome", "Gruppo valutazione", "Stato", "Anche in", "Altre classi", "Aggiornato"]
        for m in metrics:
            cols += [crit[m]["name"], "Giudizio"]
        _header(ws, cols)
        recs = [r for r in data["records"] if any(x["index"] == market for x in r["memberships"])]
        for r in sorted(recs, key=lambda r: (r["sector_it"], r["ticker"])):
            row = [r["sector_it"], r["ticker"], r["name"], r["group_name"], _status(r, market), _other(r, market),
                   ", ".join(r.get("secondary", [])),
                   r["updated"] + (" (dati non aggiornati)" if r.get("stale") else "")]
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
        ws.column_dimensions["C"].width = 28
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

    ws = wb.create_sheet("Note")
    ws.append(["Generato", data["generated"]])
    for k, d in data["macro"].items():
        ws.append([{"rf": "Tasso privo di rischio (%)", "erp": "Premio rischio azionario (%)"}[k], d.get("v"), d.get("src", ""), d.get("why", "")])
    ws.append(["Finviz Elite attivo", "Sì" if data["finviz_elite"] else "No (cascata da Yahoo)"])
    ws.append(["Legenda", "n.c. = non calcolabile (nessun giudizio); n.a. = metrica non applicabile al settore"])
    wb.save(path)
