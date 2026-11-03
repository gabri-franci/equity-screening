"""Stampa un riepilogo leggibile di docs/data/latest.json (usato nei registri di prova)."""
from __future__ import annotations

import json
from collections import Counter

from .run import LATEST_JSON
from .constituents import load_membership


def main() -> None:
    mem = load_membership()
    print("Composizione:", dict(Counter(mem[mem.active]["index"])) if not mem.empty else "vuota")
    d = json.loads(LATEST_JSON.read_text("utf-8"))
    print("Macro:", {k: v.get("v") for k, v in d["macro"].items()})
    missing = Counter()
    for r in d["records"]:
        print(f"\n=== {r['ticker']} | {r['name']} | {r['sector']} / {r['industry']} | gruppo {r['group']} | "
              f"indici {[m['index'] for m in r['memberships']]} | stale={r.get('stale')}")
        q = r.get("quote") or {}
        print(f"  prezzo {q.get('price')} {q.get('currency', '')} al {q.get('date')} | var {q.get('changes')} | "
              f"settimanali {len(q.get('weekly') or [])} giornalieri {len(q.get('daily') or [])}")
        for m, x in r["metrics"].items():
            v = x.get("v")
            if v is None:
                missing[m] += 1
            src = x.get("src", "")
            print(f"  {m:16} {str(v):>10} {x.get('label', ''):14} [{src}] {x.get('note', '')[:110]}")
    print("\nMetriche mancanti (conteggio):", dict(missing))


if __name__ == "__main__":
    main()
