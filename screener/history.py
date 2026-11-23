"""Storico per esercizio (ultimi 6-7 esercizi) e metriche a 5 anni.

Fonte principale: bilanci ufficiali SEC (10-K), perché Yahoo fornisce solo 4 esercizi
e un CAGR a 5 anni ne richiede 6 (anno di partenza + 5 anni). Gli esercizi che mancano
in SEC vengono completati, quando possibile, con i bilanci annuali di Yahoo.

Ogni valore ricorda il documento da cui viene, così i box "Dato dedotto" possono citarlo.
"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from .model import Datum, fmt_money, missing
from .sources import sec
from .sources import yahoo as yh
from .sources.yahoo import YahooData

# Etichette XBRL (us-gaap) in ordine di preferenza
REV = ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues",
       "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueNet", "SalesRevenueGoodsNet"]
EPS = ["EarningsPerShareDiluted", "EarningsPerShareBasicAndDiluted"]
NI = ["NetIncomeLoss", "NetIncomeLossAvailableToCommonStockholdersBasic", "ProfitLoss"]
EBIT = ["OperatingIncomeLoss"]
OCF = ["NetCashProvidedByUsedInOperatingActivities", "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"]
CAPEX = ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"]
DA = ["DepreciationDepletionAndAmortization", "DepreciationAndAmortization", "DepreciationAmortizationAndAccretionNet",
      "Depreciation"]
EQ = ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"]
CASH = ["CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"]
STI = ["ShortTermInvestments", "MarketableSecuritiesCurrent", "AvailableForSaleSecuritiesDebtSecuritiesCurrent"]
LTD_TOTAL = ["LongTermDebt", "LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities"]
LTD_NONCUR = ["LongTermDebtNoncurrent", "LongTermDebtAndCapitalLeaseObligations"]
LTD_CUR = ["LongTermDebtCurrent", "LongTermDebtAndCapitalLeaseObligationsCurrent", "DebtCurrent"]
STD = ["ShortTermBorrowings", "CommercialPaper"]
SHARES = ["WeightedAverageNumberOfDilutedSharesOutstanding"]
TAX = ["IncomeTaxExpenseBenefit"]
ASSETS = ["Assets"]
PRETAX = ["IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
          "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments"]

YAHOO_ROWS = {  # stessa grandezza nei bilanci annuali Yahoo (riserva)
    "rev": ("inc_a", ("Total Revenue",)), "eps": ("inc_a", ("Diluted EPS",)), "ni": ("inc_a", ("Net Income",)),
    "ebit": ("inc_a", ("Operating Income", "EBIT")), "ocf": ("cf_a", ("Operating Cash Flow",)),
    "capex": ("cf_a", ("Capital Expenditure",)), "da": ("cf_a", ("Depreciation And Amortization",)),
    "eq": ("bal_a", ("Stockholders Equity", "Common Stock Equity")),
    "cash": ("bal_a", ("Cash Cash Equivalents And Short Term Investments", "Cash And Cash Equivalents")),
    "debt": ("bal_a", ("Total Debt",)), "shares": ("inc_a", ("Diluted Average Shares",)),
    "assets": ("bal_a", ("Total Assets",)),
}

YEARS = 5


def _d(s: str) -> date:
    return date.fromisoformat(str(s)[:10])


class FiscalHistory:
    def __init__(self, ticker: str, y: YahooData, tax_default: float, tax_clip: tuple[float, float]):
        self.t = ticker
        info = y.info or {}
        self.cur = info.get("currency") or "USD"
        self.fin_cur = info.get("financialCurrency") or self.cur
        self.y = y
        self.cik = sec.cik_str(ticker)
        self.tax_default = tax_default
        self.tax_clip = tax_clip
        self._s: dict[str, dict] = {}

    # -------------------------------------------------------------- serie
    def _split_factor(self, entry: dict | None) -> float:
        """Frazionamenti avvenuti DOPO il deposito del documento: il dato va rettificato."""
        if not entry or self.y.splits is None or self.y.splits.empty:
            return 1.0
        filed = pd.Timestamp(entry.get("filed") or entry.get("end"))
        f = 1.0
        for dt, ratio in self.y.splits.items():
            if dt > filed and ratio > 0:
                f *= float(ratio)
        return f

    def series(self, key: str) -> dict[str, dict]:
        """{fine_esercizio: {"v", "doc", "src", "adj"}} per una grandezza, SEC + riserva Yahoo."""
        if key in self._s:
            return self._s[key]
        if key == "debt":
            out = self._debt()
        else:
            concepts, unit, flow = {
                "rev": (REV, "USD", True), "eps": (EPS, "USD/shares", True), "ni": (NI, "USD", True),
                "ebit": (EBIT, "USD", True), "ocf": (OCF, "USD", True), "capex": (CAPEX, "USD", True),
                "da": (DA, "USD", True), "eq": (EQ, "USD", False), "cash": (CASH, "USD", False),
                "sti": (STI, "USD", False), "shares": (SHARES, "shares", True), "tax": (TAX, "USD", True),
                "pretax": (PRETAX, "USD", True), "assets": (ASSETS, "USD", False),
            }[key]
            out = {}
            for end, (v, e, concept) in sec.annual_merged(self.t, concepts, unit, flow).items():
                adj = 1.0
                if key in ("eps", "shares"):
                    adj = self._split_factor(e)
                    v = v / adj if key == "eps" else v * adj
                out[end] = {"v": v, "doc": sec.doc(self.cik, e), "src": f"SEC {concept}", "adj": adj}
        # riserva Yahoo per gli esercizi mancanti
        if key in YAHOO_ROWS:
            attr, rows = YAHOO_ROWS[key]
            for ts, v in yh.annual_series(getattr(self.y, attr), *rows).items():
                end = yh.d(ts)
                if any(abs((_d(end) - _d(k)).days) <= 15 for k in out):
                    continue
                page = {"inc_a": "financials", "bal_a": "balance-sheet", "cf_a": "cash-flow"}[attr]
                out[end] = {"v": abs(v) if key == "capex" else v, "src": "Yahoo Finance",
                            "doc": {"label": f"Yahoo Finance — bilancio annuale esercizio {end}",
                                    "url": self.y.url(page)}, "adj": 1.0}
        self._s[key] = dict(sorted(out.items(), reverse=True))
        return self._s[key]

    def _debt(self) -> dict[str, dict]:
        """Debito finanziario totale = debito a lungo (incl. quota corrente) + debito a breve."""
        tot = sec.annual_merged(self.t, LTD_TOTAL, flow=False)
        nonc = sec.annual_merged(self.t, LTD_NONCUR, flow=False)
        cur = sec.annual_merged(self.t, LTD_CUR, flow=False)
        short = sec.annual_merged(self.t, STD, flow=False)
        out = {}
        for end in set(tot) | set(nonc):
            if end in tot:
                v, e, c = tot[end]
                parts = [f"{c} {fmt_money(v)}"]
            else:
                v, e, c = nonc[end]
                parts = [f"{c} {fmt_money(v)}"]
                if end in cur:
                    v += cur[end][0]
                    parts.append(f"{cur[end][2]} {fmt_money(cur[end][0])}")
            if end in short:
                v += short[end][0]
                parts.append(f"{short[end][2]} {fmt_money(short[end][0])}")
            out[end] = {"v": v, "doc": sec.doc(self.cik, e), "src": "SEC " + " + ".join(parts), "adj": 1.0}
        return out

    def fy_ends(self) -> list[str]:
        ends = set(self.series("ni")) | set(self.series("ebit")) | set(self.series("rev"))
        return sorted(ends, reverse=True)

    def at(self, key: str, end: str) -> dict | None:
        s = self.series(key)
        if end in s:
            return s[end]
        for k, v in s.items():
            if abs((_d(k) - _d(end)).days) <= 15:
                return v
        return None

    def price_at(self, end: str) -> tuple[float, str] | None:
        h = self.y.hist
        if h is None or h.empty:
            return None
        sub = h[h.index <= pd.Timestamp(end)]
        if sub.empty or (pd.Timestamp(end) - sub.index[-1]).days > 7:
            return None
        return float(sub.iloc[-1]), sub.index[-1].date().isoformat()

    def tax_rate(self, end: str) -> tuple[float, str]:
        tx, pt = self.at("tax", end), self.at("pretax", end)
        lo, hi = self.tax_clip
        if tx and pt and pt["v"] > 0:
            r = min(max(tx["v"] / pt["v"], lo), hi)
            return r, f"{r:.1%}"
        return self.tax_default, f"{self.tax_default:.0%} standard"

    def last_years(self, n: int = YEARS) -> list[str]:
        return self.fy_ends()[:n]

    # ------------------------------------------------------------ metriche
    def cagr5(self, key: str, label: str, fcf: bool = False) -> Datum:
        ends = self.fy_ends()
        if not ends:
            return missing("bilanci annuali non disponibili")
        e0 = ends[0]
        target = _d(e0) - timedelta(days=round(365.25 * YEARS))

        def value(end):
            if fcf:
                o, c = self.at("ocf", end), self.at("capex", end)
                if not o:
                    return None
                return {"v": o["v"] - abs(c["v"] if c else 0.0), "doc": o["doc"], "src": o["src"], "adj": 1.0}
            return self.at(key, end)

        start_end = next((k for k in ends if abs((_d(k) - target).days) <= 20), None)
        a, b = value(e0), value(start_end) if start_end else None
        years = YEARS
        if a and not b:
            # meno di 5 anni di bilanci (es. società non USA: Yahoo dà 4 esercizi): si usa il periodo disponibile
            avail = [k for k in ends if value(k)]
            oldest = avail[-1] if avail else None
            years = round((_d(e0) - _d(oldest)).days / 365.25) if oldest else 0
            if years >= 3:
                start_end, b = oldest, value(oldest)
        if not a or not b:
            n = len([k for k in ends if value(k)])
            return missing(f"servono 6 esercizi di bilancio (anno di partenza + 5 anni): disponibili {n}")
        base = Datum(src="Bilanci annuali (calcolato)", deduced=True,
                     definition=f"CAGR {years} anni = ({label} {e0[:4]} / {label} {start_end[:4]})^(1/{years}) − 1",
                     docs=[b["doc"], a["doc"]], period_years=years if years < YEARS else 0)
        fmt = (lambda x: f"{x:,.2f}") if key == "eps" else fmt_money
        txt = f"{label} esercizio chiuso il {start_end}: {fmt(b['v'])} ({b['src']}) → esercizio chiuso il {e0}: {fmt(a['v'])} ({a['src']})"
        if key == "eps" and (a.get("adj", 1) != 1 or b.get("adj", 1) != 1):
            txt += ". EPS rettificati per i frazionamenti azionari avvenuti dopo il deposito del bilancio"
        if b["v"] <= 0 or a["v"] <= 0:
            base.note = f"non calcolabile: valore iniziale o finale ≤ 0 — {txt}"
            return base
        g = (a["v"] / b["v"]) ** (1 / years) - 1
        base.v = round(g * 100, 2)
        base.why = f"{txt}; CAGR = {base.v}%"
        if years < YEARS:
            base.why += (f". ATTENZIONE: disponibili solo {years} anni di bilanci invece di 5, "
                         f"quindi il CAGR è calcolato su {years} anni")
        return base

    def roic_avg5(self) -> Datum:
        rows, docs, vals = [], [], []
        for end in self.last_years():
            ebit, eq = self.at("ebit", end), self.at("eq", end)
            if not ebit or not eq:
                continue
            debt = self.at("debt", end)
            cash = self.at("cash", end)
            sti = self.at("sti", end)
            d, c = (debt["v"] if debt else 0.0), (cash["v"] if cash else 0.0) + (sti["v"] if sti else 0.0)
            t, tnote = self.tax_rate(end)
            ic = d + eq["v"] - c
            if ic <= 0:
                rows.append(f"{end[:4]}: escluso (capitale investito ≤ 0)")
                continue
            assets = self.at("assets", end)
            if assets and ic < 0.05 * assets["v"]:
                rows.append(f"{end[:4]}: escluso (capitale investito {fmt_money(ic)} sotto il 5% dell'attivo: ROIC non significativo)")
                continue
            r = ebit["v"] * (1 - t) / ic * 100
            vals.append(r)
            rows.append(f"{end[:4]}: EBIT {fmt_money(ebit['v'])} × (1 − {tnote}) / (debito {fmt_money(d)} + PN "
                        f"{fmt_money(eq['v'])} − cassa {fmt_money(c)}) = {r:.1f}%")
            docs.append(ebit["doc"])
        return self._avg(vals, rows, docs, "ROIC medio 5 anni = media dei ROIC annuali; "
                         "ROIC = EBIT × (1 − aliquota) / (Debito + Patrimonio netto − Cassa), valori a fine esercizio")

    def roe_avg5(self) -> Datum:
        rows, docs, vals = [], [], []
        for end in self.last_years():
            ni, eq = self.at("ni", end), self.at("eq", end)
            if not ni or not eq:
                continue
            if eq["v"] <= 0:
                rows.append(f"{end[:4]}: escluso (patrimonio netto ≤ 0)")
                continue
            r = ni["v"] / eq["v"] * 100
            vals.append(r)
            rows.append(f"{end[:4]}: utile {fmt_money(ni['v'])} / PN {fmt_money(eq['v'])} = {r:.1f}%")
            docs.append(ni["doc"])
        return self._avg(vals, rows, docs, "ROE medio 5 anni = media dei ROE annuali; ROE = Utile netto / Patrimonio netto a fine esercizio")

    def fx_note(self) -> Datum | None:
        if self.fin_cur != self.cur:
            return missing(f"non calcolabile: il prezzo è in {self.cur} e i bilanci in {self.fin_cur}, "
                           f"il rapporto non sarebbe coerente")
        return None

    def pe_hist(self) -> Datum:
        if self.fx_note():
            return self.fx_note()
        rows, docs, vals = [], [], []
        for end in self.last_years():
            eps, px = self.at("eps", end), self.price_at(end)
            if not eps or not px:
                rows.append(f"{end[:4]}: escluso (manca {'EPS' if not eps else 'prezzo'})")
                continue
            if eps["v"] <= 0:
                rows.append(f"{end[:4]}: escluso (EPS ≤ 0)")
                continue
            pe = px[0] / eps["v"]
            vals.append(pe)
            adj = " (EPS rettificato per frazionamento)" if eps.get("adj", 1) != 1 else ""
            rows.append(f"{end[:4]}: prezzo {px[0]:,.2f} al {px[1]} / EPS diluito {eps['v']:,.2f}{adj} = {pe:.1f}x")
            docs.append(eps["doc"])
        docs.append({"label": "Yahoo Finance — storico prezzi di chiusura", "url": self.y.url("history")})
        return self._avg(vals, rows, docs, "P/E storico = media dei P/E a fine esercizio degli ultimi 5 esercizi; "
                         "P/E = prezzo di chiusura a fine esercizio / EPS diluito dell'esercizio", unit="x")

    def ev_ebitda_hist(self) -> Datum:
        if self.fx_note():
            return self.fx_note()
        rows, docs, vals = [], [], []
        has_debt_data = bool(self.series("debt"))
        for end in self.last_years():
            ebit, da, sh, px = self.at("ebit", end), self.at("da", end), self.at("shares", end), self.price_at(end)
            miss = [n for n, x in (("EBIT", ebit), ("ammortamenti", da), ("azioni", sh), ("prezzo", px)) if not x]
            debt = self.at("debt", end)
            if has_debt_data and not debt:
                miss.append("debito")
            if miss:
                rows.append(f"{end[:4]}: escluso (manca {', '.join(miss)})")
                continue
            cash, sti = self.at("cash", end), self.at("sti", end)
            d = debt["v"] if debt else 0.0
            c = (cash["v"] if cash else 0.0) + (sti["v"] if sti else 0.0)
            ebitda = ebit["v"] + abs(da["v"])
            if ebitda <= 0:
                rows.append(f"{end[:4]}: escluso (EBITDA ≤ 0)")
                continue
            mcap = px[0] * sh["v"]
            ev = mcap + d - c
            m = ev / ebitda
            vals.append(m)
            rows.append(f"{end[:4]}: EV = prezzo {px[0]:,.2f} × {sh['v'] / 1e6:,.0f} mln azioni + debito {fmt_money(d)} − cassa "
                        f"{fmt_money(c)} = {fmt_money(ev)}; EBITDA = EBIT {fmt_money(ebit['v'])} + ammortamenti "
                        f"{fmt_money(abs(da['v']))} = {fmt_money(ebitda)}; {m:.1f}x")
            docs.append(ebit["doc"])
        if not has_debt_data:
            rows.append("Nessun debito finanziario dichiarato nei bilanci SEC: considerato zero")
        docs.append({"label": "Yahoo Finance — storico prezzi di chiusura", "url": self.y.url("history")})
        return self._avg(vals, rows, docs, "EV/EBITDA storico = media degli EV/EBITDA a fine esercizio degli ultimi 5 esercizi; "
                         "EV = capitalizzazione (prezzo × azioni diluite medie) + debito − cassa e investimenti a breve; "
                         "EBITDA = utile operativo + ammortamenti", unit="x")

    @staticmethod
    def _avg(vals: list[float], rows: list[str], docs: list, definition: str, unit: str = "pct") -> Datum:
        uniq, seen = [], set()
        for d in docs:
            if d and d.get("url") not in seen:
                seen.add(d.get("url"))
                uniq.append(d)
        if len(vals) < 3:
            return Datum(src="Bilanci annuali (calcolato)", deduced=True, definition=definition, docs=uniq,
                         why="; ".join(rows), note=f"non calcolabile: servono almeno 3 esercizi validi su 5, disponibili {len(vals)}")
        v = sum(vals) / len(vals)
        why = "; ".join(rows) + f". Media di {len(vals)} esercizi = {v:.2f}{'%' if unit == 'pct' else 'x'}"
        if len(vals) < YEARS:
            why += f". ATTENZIONE: media su {len(vals)} esercizi invece di 5 (dati disponibili)"
        return Datum(v=round(v, 2), src="Bilanci annuali (calcolato)", deduced=True, definition=definition,
                     why=why, docs=uniq, series=[round(x, 2) for x in reversed(vals)],
                     period_years=len(vals) if len(vals) < YEARS else 0)
