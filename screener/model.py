"""Struttura di un singolo dato con la sua tracciabilità (fonte, definizione, deduzione)."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass
class Datum:
    v: Any = None                 # valore (numero, bool) o None se non disponibile
    src: str = ""                 # fonte, es. "Yahoo Finance" / "SEC EDGAR" / "Finviz Elite"
    definition: str = ""          # definizione/formula usata da quella fonte
    deduced: bool = False         # True se il dato è stato calcolato da noi
    why: str = ""                 # ragionamento con i numeri usati (solo per i dati dedotti)
    docs: list = field(default_factory=list)   # documenti di origine [{"label":..., "url":...}]
    note: str = ""                # es. "non calcolabile: EBITDA negativo"
    series: list = field(default_factory=list)  # serie storica, es. ROIC per esercizio

    @property
    def ok(self) -> bool:
        return self.v is not None

    def to_dict(self) -> dict:
        d = asdict(self)
        return {k: val for k, val in d.items() if val not in ("", [], None, False) or k == "v"}


def missing(note: str, src: str = "") -> Datum:
    return Datum(v=None, src=src, note=note)


def pct(x):
    return None if x is None else round(float(x) * 100, 2)


def fmt_money(x) -> str:
    if x is None:
        return "n.d."
    x = float(x)
    for unit, div in (("bn", 1e9), ("mln", 1e6)):
        if abs(x) >= div:
            return f"{x / div:,.2f} {unit}"
    return f"{x:,.2f}"
