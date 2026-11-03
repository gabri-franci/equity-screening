"""Dashboard web statica (docs/index.html) con i dati incorporati: funziona su GitHub Pages
e anche aprendo il file direttamente dal computer."""
from __future__ import annotations

import json
from pathlib import Path

TEMPLATE = Path(__file__).with_name("dashboard_template.html")


def write(data: dict, path) -> None:
    payload = json.dumps(data, ensure_ascii=False, default=str).replace("</", "<\\/")
    html = TEMPLATE.read_text("utf-8").replace("__DATA__", payload)
    Path(path).write_text(html, "utf-8")
