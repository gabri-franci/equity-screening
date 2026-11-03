"""Calcolo delle metriche con cascata delle fonti e tracciabilità.

Cascata automatica (ordine richiesto dall'utente, limitato alle fonti accessibili
in modo lecito e automatico):
    1. Finviz Elite (solo se FINVIZ_AUTH è impostato)
    2. Yahoo Finance — campo fornito direttamente
    3. Yahoo Finance — calcolato dai bilanci            -> "dato dedotto"
    4. SEC EDGAR (10-K ufficiali) — calcolato             -> "dato dedotto"
Koyfin, TradingView, Investing, Morningstar, FactSet e roic.ai non hanno un accesso
automatico consentito e restano per le verifiche manuali.
"""
from __future__ import annotations

import math
from typing import Callable

from .model import Datum, fmt_money, missing, pct
from .sources import finviz, sec
from .sources import yahoo as yh
from .sources.yahoo import YahooData

FINVIZ_SECTOR = {  # nomi Yahoo/Wikipedia -> nomi Finviz
    "Financial Services": "Financial", "Financials": "Financial",
    "Information Technology": "Technology", "Health Care": "Healthcare",
    "Consumer Discretionary": "Consumer Cyclical", "Consumer Staples": "Consumer Defensive",
    "Materials": "Basic Materials",
}
SECTOR_IT = {
    "Technology": "Tecnologia", "Communication Services": "Servizi di comunicazione",
    "Consumer Cyclical": "Beni di consumo ciclici", "Consumer Defensive": "Beni di consumo difensivi",
    "Financial": "Finanziari", "Healthcare": "Sanità", "Industrials": "Industriali",
    "Basic Materials": "Materiali", "Energy": "Energia", "Real Estate": "Immobiliare", "Utilities": "Utilities",
}


def norm_sector(s: str) -> str:
    s = (s or "").strip()
    return FINVIZ_SECTOR.get(s, s)


def group_for(ticker: str, sector: str, industry: str, cfg: dict) -> str:
    ind = (industry or "").lower()
    if ticker in cfg.get("tech_ai_extra", []) or sector == "Technology":
        return "tech_ai"
    if sector == "Financial":
        if ind.startswith("banks") or ind == "capital markets":
            return "bank"
        if ind.startswith("insurance"):
            return "insurance"
        return "financial_other"
    if sector == "Healthcare":
        return "pharma" if ind.startswith("drug manufacturers") or ind == "biotechnology" else "healthcare_other"
    if sector == "Communication Services":
        return "telecom" if ind.startswith("telecom") else "media_internet"
    if sector == "Real Estate":
        return "reit" if ind.startswith("reit") else "real_estate_other"
    return {
        "Consumer Cyclical": "consumer_cyclical", "Consumer Defensive": "consumer_defensive",
        "Utilities": "utilities", "Energy": "energy", "Basic Materials": "materials", "Industrials": "industrials",
    }.get(sector, "other")


def _f(x):
    try:
        x = float(x)
        return None if math.isnan(x) or math.isinf(x) else x
    except (TypeError, ValueError):
        return None


def cagr(start: float, end: float, years: int) -> float | None:
    if start is None or end is None or start <= 0 or end <= 0 or years <= 0:
        return None
    return (end / start) ** (1 / years) - 1


class Engine:
    """Calcola tutte le metriche di un titolo."""

    def __init__(self, ticker: str, y: YahooData | None, fv_row: dict | None, macro: dict, cfg: dict):
        self.t = ticker
        self.y = y or YahooData(ticker=ticker)
        self.info = self.y.info
        self.fv = fv_row
        self.macro = macro
        self.cfg = cfg
        self.out: dict[str, Datum] = {}

    # ------------------------------------------------------------- helpers
    def ydoc(self, page: str, label: str) -> dict:
        return {"label": f"Yahoo Finance — {label}", "url": self.y.url(page)}

    def yinfo(self, key: str, definition: str, scale: float = 1.0, as_pct=False) -> Datum | None:
        v = _f(self.info.get(key))
        if v is None:
            return None
        v = pct(v) if as_pct else round(v * scale, 4)
        return Datum(v=v, src=yh.SRC, definition=definition, docs=[self.ydoc("key-statistics", f"campo '{key}'")])

    @staticmethod
    def cascade(*fns: Callable[[], Datum | None], note: str = "dato non disponibile in nessuna fonte") -> Datum:
        notes = []
        for fn in fns:
            try:
                d = fn()
            except Exception as e:  # una fonte che fallisce passa alla successiva
                notes.append(str(e))
                continue
            if d is None:
                continue
            if d.ok:
                return d
            if d.note:
                notes.append(d.note)
        return missing(notes[0] if notes else note)

    def fv_get(self, metric: str):
        return lambda: finviz.get(self.fv, metric, self.t)

    # --------------------------------------------------- dati di base (TTM)
    def ttm(self, label: str, *rows: str, kind: str = "inc") -> tuple:
        df = {"inc": self.y.inc_q, "cf": self.y.cf_q}[kind]
        v, dates = yh.ttm(df, *rows)
        if v is None:
            return None, None
        page = "financials" if kind == "inc" else "cash-flow"
        doc = self.ydoc(page, f"{label}, somma 4 trimestri ({yh.d(dates[-1])} → {yh.d(dates[0])})")
        return v, doc

    def mrq(self, label: str, *rows: str) -> tuple:
        v, dt = yh.latest(self.y.bal_q, *rows)
        if v is None:
            v, dt = yh.latest(self.y.bal_a, *rows)
        if v is None:
            return None, None
        return v, self.ydoc("balance-sheet", f"{label} al {yh.d(dt)}")

    def tax_rate(self) -> tuple[float, str]:
        w = self.cfg["wacc"]
        lo, hi = w["tax_clip_pct"]
        v, dt = yh.latest(self.y.inc_a, "Tax Rate For Calcs")
        if v is None:
            return w["tax_default_pct"] / 100, f"aliquota standard {w['tax_default_pct']}% (dato non disponibile)"
        t = min(max(v, lo / 100), hi / 100)
        return t, f"aliquota effettiva {v:.1%} esercizio {yh.d(dt)} (limitata a {lo}-{hi}%)"

    # ============================================================ METRICHE
    def roic(self) -> Datum:
        def from_yahoo():
            ebit, d1 = self.ttm("Utile operativo (EBIT)", "Operating Income", "EBIT")
            debt, d2 = self.mrq("Debito totale", "Total Debt")
            eq, d3 = self.mrq("Patrimonio netto", "Stockholders Equity", "Common Stock Equity")
            cash, d4 = self.mrq("Cassa e investimenti a breve", "Cash Cash Equivalents And Short Term Investments",
                                "Cash And Cash Equivalents")
            if None in (ebit, eq):
                return None
            debt, cash = debt or 0.0, cash or 0.0
            t, tnote = self.tax_rate()
            ic = debt + eq - cash
            base = Datum(src=yh.SRC + " (calcolato)", deduced=True, docs=[x for x in (d1, d2, d3, d4) if x],
                         definition="ROIC = EBIT TTM × (1 − aliquota) / (Debito + Patrimonio netto − Cassa)")
            if ic <= 0:
                base.note = "non calcolabile: capitale investito ≤ 0"
                return base
            base.v = pct(ebit * (1 - t) / ic)
            base.why = (f"NOPAT = EBIT {fmt_money(ebit)} × (1 − {t:.1%}) = {fmt_money(ebit * (1 - t))}; "
                        f"Capitale investito = {fmt_money(debt)} + {fmt_money(eq)} − {fmt_money(cash)} = {fmt_money(ic)}; "
                        f"ROIC = {base.v}%. Tasse: {tnote}.")
            return base
        return self.cascade(self.fv_get("roic"), from_yahoo)

    def roic_annual(self) -> list[tuple[str, float, dict]]:
        """ROIC per esercizio (ultimi 3), dai bilanci annuali Yahoo, con fallback SEC."""
        out = []
        ebit = yh.annual_series(self.y.inc_a, "Operating Income", "EBIT")
        debt = yh.annual_series(self.y.bal_a, "Total Debt")
        eq = yh.annual_series(self.y.bal_a, "Stockholders Equity", "Common Stock Equity")
        cash = yh.annual_series(self.y.bal_a, "Cash Cash Equivalents And Short Term Investments", "Cash And Cash Equivalents")
        taxr = yh.annual_series(self.y.inc_a, "Tax Rate For Calcs")
        lo, hi = self.cfg["wacc"]["tax_clip_pct"]
        for dt, e in ebit.items():
            if dt not in eq:
                continue
            t = min(max(taxr.get(dt, self.cfg["wacc"]["tax_default_pct"] / 100), lo / 100), hi / 100)
            ic = debt.get(dt, 0.0) + eq[dt] - cash.get(dt, 0.0)
            if ic > 0:
                out.append((yh.d(dt), round(e * (1 - t) / ic * 100, 2),
                            self.ydoc("financials", f"bilancio annuale esercizio {yh.d(dt)}")))
        if len(out) >= 3:
            return out[:3]
        # fallback SEC
        e_s = sec.annual(self.t, ["OperatingIncomeLoss"])
        eq_s = {x[0]: x for x in sec.annual(self.t, ["StockholdersEquity"], flow=False)}
        debt_s = {x[0]: x[1] for x in sec.annual(self.t, ["LongTermDebt", "LongTermDebtNoncurrent"], flow=False)}
        cash_s = {x[0]: x[1] for x in sec.annual(self.t, ["CashAndCashEquivalentsAtCarryingValue"], flow=False)}
        cik = sec.cik_str(self.t)
        out = []
        t = self.cfg["wacc"]["tax_default_pct"] / 100
        for end, val, entry, _ in e_s:
            if end in eq_s:
                ic = debt_s.get(end, 0.0) + eq_s[end][1] - cash_s.get(end, 0.0)
                if ic > 0:
                    out.append((end, round(val * (1 - t) / ic * 100, 2), sec.doc(cik, entry)))
        return out[:3]

    def roic_3y(self) -> Datum:
        series = self.roic_annual()
        if len(series) < 3:
            return missing("servono 3 esercizi di bilancio: disponibili " + str(len(series)))
        vals = [v for _, v, _ in reversed(series)]  # dal più vecchio
        txt = "; ".join(f"{dt}: {v}%" for dt, v, _ in reversed(series))
        return Datum(v=vals[-1], src="Bilanci annuali (calcolato)", deduced=True,
                     definition="ROIC per esercizio = EBIT × (1 − aliquota) / (Debito + PN − Cassa)",
                     why=f"ROIC ultimi 3 esercizi → {txt}.", docs=[doc for _, _, doc in series],
                     series=vals)

    def wacc(self) -> Datum:
        w = self.cfg["wacc"]
        rf, erp = self.macro.get("rf"), self.macro.get("erp")
        beta = _f(self.info.get("beta"))
        mcap = _f(self.info.get("marketCap"))
        if rf is None or rf.v is None or beta is None or mcap is None:
            return missing("WACC non calcolabile: manca " + ", ".join(
                n for n, x in (("tasso risk free", rf and rf.v), ("beta", beta), ("capitalizzazione", mcap)) if x is None))
        debt, ddoc = self.mrq("Debito totale", "Total Debt")
        debt = debt or 0.0
        interest, idoc = self.ttm("Interessi passivi", "Interest Expense", "Interest Expense Non Operating")
        lo, hi = w["cost_of_debt_clip_pct"]
        if interest and debt > 0:
            kd_raw = abs(interest) / debt * 100
            kd = min(max(kd_raw, lo), hi)
            kd_note = f"interessi TTM {fmt_money(abs(interest))} / debito {fmt_money(debt)} = {kd_raw:.2f}% (limitato a {lo}-{hi}%)"
        else:
            kd = rf.v + w["cost_of_debt_spread_pct"]
            kd_note = f"dato interessi assente: risk free + {w['cost_of_debt_spread_pct']}%"
        t, tnote = self.tax_rate()
        ke = rf.v + beta * erp.v
        we, wd = mcap / (mcap + debt), debt / (mcap + debt)
        v = round(we * ke + wd * kd * (1 - t), 2)
        docs = list(rf.docs) + list(erp.docs) + [self.ydoc("key-statistics", "beta 5 anni mensile e capitalizzazione")]
        docs += [x for x in (ddoc, idoc) if x]
        return Datum(v=v, src="Calcolato (CAPM)", deduced=True,
                     definition="WACC = E/(D+E)·Ke + D/(D+E)·Kd·(1−t);  Ke = Rf + β·ERP",
                     why=(f"Rf {rf.v}% (Treasury 10 anni) + β {beta:.2f} × ERP {erp.v}% = Ke {ke:.2f}%. "
                          f"Kd: {kd_note}. Tasse: {tnote}. Pesi a valori di mercato: E {fmt_money(mcap)} ({we:.0%}), "
                          f"D {fmt_money(debt)} ({wd:.0%}). WACC = {v}%."),
                     docs=docs)

    def roe(self) -> Datum:
        def calc():
            ni, d1 = self.ttm("Utile netto", "Net Income", "Net Income Common Stockholders")
            eq, d2 = self.mrq("Patrimonio netto", "Stockholders Equity", "Common Stock Equity")
            if ni is None or not eq:
                return None
            if eq <= 0:
                return missing("non calcolabile: patrimonio netto negativo")
            v = pct(ni / eq)
            return Datum(v=v, src=yh.SRC + " (calcolato)", deduced=True, definition="Utile netto TTM / Patrimonio netto",
                         why=f"{fmt_money(ni)} / {fmt_money(eq)} = {v}%", docs=[d1, d2])
        return self.cascade(self.fv_get("roe"),
                            lambda: self.yinfo("returnOnEquity", "Utile netto TTM / Patrimonio netto medio (Yahoo)", as_pct=True),
                            calc)

    def op_margin(self) -> Datum:
        def calc():
            ebit, d1 = self.ttm("Utile operativo", "Operating Income", "EBIT")
            rev, d2 = self.ttm("Ricavi", "Total Revenue")
            if ebit is None or not rev:
                return None
            v = pct(ebit / rev)
            return Datum(v=v, src=yh.SRC + " (calcolato)", deduced=True, definition="EBIT TTM / Ricavi TTM",
                         why=f"{fmt_money(ebit)} / {fmt_money(rev)} = {v}%", docs=[d1, d2])
        return self.cascade(self.fv_get("op_margin"),
                            lambda: self.yinfo("operatingMargins", "Margine operativo TTM (Yahoo)", as_pct=True),
                            calc)

    def fcf_margin(self) -> Datum:
        def calc():
            cfo, d1 = self.ttm("Flusso di cassa operativo", "Operating Cash Flow", kind="cf")
            capex, d2 = self.ttm("Capex", "Capital Expenditure", kind="cf")
            rev, d3 = self.ttm("Ricavi", "Total Revenue")
            if cfo is None or not rev:
                return None
            capex = capex or 0.0
            fcf = cfo - abs(capex)
            v = pct(fcf / rev)
            return Datum(v=v, src=yh.SRC + " (calcolato)", deduced=True,
                         definition="(Flusso di cassa operativo − Capex) TTM / Ricavi TTM",
                         why=f"FCF = {fmt_money(cfo)} − {fmt_money(abs(capex))} = {fmt_money(fcf)}; "
                             f"/ ricavi {fmt_money(rev)} = {v}%", docs=[x for x in (d1, d2, d3) if x])
        return self.cascade(calc)

    # ------------------------------------------------------------ CRESCITA
    def _cagr3(self, label: str, y_rows: tuple, sec_concepts: list, unit="USD", fcf=False) -> Datum:
        def from_series(series: list[tuple], src: str, docs_fn):
            if len(series) < 4:
                return missing(f"{src}: servono 4 esercizi, disponibili {len(series)}")
            (e_dt, e_v), (s_dt, s_v) = series[0][:2], series[3][:2]
            base = Datum(src=src, deduced=True, definition=f"CAGR 3 anni = ({label} ultimo esercizio / {label} di 3 esercizi prima)^(1/3) − 1",
                         docs=docs_fn(series[0], series[3]))
            g = cagr(s_v, e_v, 3)
            if g is None:
                base.note = (f"non calcolabile: valore iniziale o finale ≤ 0 "
                             f"({yh.d(s_dt)}: {s_v:,.2f} → {yh.d(e_dt)}: {e_v:,.2f})")
                return base
            base.v = pct(g)
            base.why = f"{label} {yh.d(s_dt)}: {s_v:,.2f} → {yh.d(e_dt)}: {e_v:,.2f}; CAGR = {base.v}%"
            return base

        def yahoo():
            if fcf:
                ocf = yh.annual_series(self.y.cf_a, "Operating Cash Flow")
                cap = yh.annual_series(self.y.cf_a, "Capital Expenditure")
                s = {k: v - abs(cap.get(k, 0.0)) for k, v in ocf.items()}
            else:
                s = yh.annual_series(self.y.inc_a, *y_rows)
            series = sorted(s.items(), key=lambda kv: kv[0], reverse=True)
            page = "cash-flow" if fcf else "financials"
            return from_series(series, yh.SRC + " (calcolato)",
                               lambda a, b: [self.ydoc(page, f"bilancio annuale {yh.d(b[0])} e {yh.d(a[0])}")])

        def sec_calc():
            if fcf:
                ocf = {x[0]: x for x in sec.annual(self.t, ["NetCashProvidedByUsedInOperatingActivities"])}
                cap = {x[0]: x[1] for x in sec.annual(self.t, ["PaymentsToAcquirePropertyPlantAndEquipment"])}
                rows = [(k, v[1] - abs(cap.get(k, 0.0)), v[2]) for k, v in ocf.items()]
            else:
                rows = [(x[0], x[1], x[2]) for x in sec.annual(self.t, sec_concepts, unit=unit)]
            rows.sort(key=lambda r: r[0], reverse=True)
            cik = sec.cik_str(self.t)
            return from_series(rows, sec.SRC + " (calcolato)", lambda a, b: [sec.doc(cik, b[2]), sec.doc(cik, a[2])])

        return self.cascade(yahoo, sec_calc)

    def rev_cagr3(self):
        return self._cagr3("Ricavi", ("Total Revenue",),
                           ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet"])

    def eps_cagr3(self):
        return self._cagr3("EPS diluito", ("Diluted EPS",), ["EarningsPerShareDiluted"], unit="USD/shares")

    def fcf_cagr3(self):
        return self._cagr3("Free Cash Flow", (), [], fcf=True)

    # ------------------------------------------------------------ SOLIDITA'
    def nd_ebitda(self) -> Datum:
        def calc():
            debt, cash, ebitda = (_f(self.info.get(k)) for k in ("totalDebt", "totalCash", "ebitda"))
            docs = [self.ydoc("key-statistics", "campi totalDebt, totalCash, ebitda (TTM)")]
            if ebitda is None:
                e, d = self.ttm("EBITDA", "EBITDA", "Normalized EBITDA")
                ebitda, docs = e, docs + ([d] if d else [])
            if debt is None or ebitda is None:
                return None
            cash = cash or 0.0
            base = Datum(src=yh.SRC + " (calcolato)", deduced=True, docs=docs,
                         definition="(Debito totale − Cassa) / EBITDA TTM")
            if ebitda <= 0:
                base.note = "non calcolabile: EBITDA ≤ 0"
                return base
            nd = debt - cash
            base.v = round(nd / ebitda, 2)
            base.why = f"({fmt_money(debt)} − {fmt_money(cash)}) / {fmt_money(ebitda)} = {base.v}x"
            if nd < 0:
                base.note = "cassa netta (più liquidità che debiti)"
            return base
        return self.cascade(calc)

    def de(self) -> Datum:
        def calc():
            debt, d1 = self.mrq("Debito totale", "Total Debt")
            eq, d2 = self.mrq("Patrimonio netto", "Stockholders Equity", "Common Stock Equity")
            if debt is None or eq is None:
                return None
            if eq <= 0:
                return missing("non calcolabile: patrimonio netto negativo")
            v = round(debt / eq, 2)
            return Datum(v=v, src=yh.SRC + " (calcolato)", deduced=True, definition="Debito totale / Patrimonio netto",
                         why=f"{fmt_money(debt)} / {fmt_money(eq)} = {v}x", docs=[d1, d2])

        def yinfo():
            d = self.yinfo("debtToEquity", "Debito totale / Patrimonio netto (Yahoo, convertito da % a x)", scale=0.01)
            if d and d.v is not None and d.v < 0:
                return missing("non calcolabile: patrimonio netto negativo")
            return d
        return self.cascade(self.fv_get("de"), yinfo, calc)

    def current_ratio(self) -> Datum:
        def calc():
            ca, d1 = self.mrq("Attività correnti", "Current Assets")
            cl, d2 = self.mrq("Passività correnti", "Current Liabilities")
            if ca is None or not cl:
                return None
            v = round(ca / cl, 2)
            return Datum(v=v, src=yh.SRC + " (calcolato)", deduced=True, definition="Attività correnti / Passività correnti",
                         why=f"{fmt_money(ca)} / {fmt_money(cl)} = {v}x", docs=[d1, d2])
        return self.cascade(self.fv_get("current_ratio"),
                            lambda: self.yinfo("currentRatio", "Attività correnti / Passività correnti (Yahoo)"), calc)

    # ----------------------------------------------------------- VALUTAZIONE
    def pe_ttm(self) -> Datum:
        eps = _f(self.info.get("trailingEps"))
        if eps is not None and eps <= 0:
            return missing(f"non calcolabile: utile per azione TTM negativo ({eps})")
        return self.cascade(self.fv_get("pe_ttm"), lambda: self.yinfo("trailingPE", "Prezzo / EPS ultimi 12 mesi (Yahoo)"))

    def pe_fwd(self) -> Datum:
        eps = _f(self.info.get("forwardEps"))
        if eps is not None and eps <= 0:
            return missing(f"non calcolabile: EPS atteso negativo ({eps})")
        return self.cascade(self.fv_get("pe_fwd"), lambda: self.yinfo("forwardPE", "Prezzo / EPS stimato prossimi 12 mesi (Yahoo)"))

    def ev_ebitda(self) -> Datum:
        ebitda = _f(self.info.get("ebitda"))
        if ebitda is not None and ebitda <= 0:
            return missing("non calcolabile: EBITDA ≤ 0")
        return self.cascade(self.fv_get("ev_ebitda"), lambda: self.yinfo("enterpriseToEbitda", "Enterprise Value / EBITDA TTM (Yahoo)"))

    def ev_sales(self) -> Datum:
        return self.cascade(self.fv_get("ev_sales"), lambda: self.yinfo("enterpriseToRevenue", "Enterprise Value / Ricavi TTM (Yahoo)"))

    # ------------------------------------------------- FINANZIARI / REIT
    def rote(self) -> Datum:
        ni, d1 = self.ttm("Utile netto", "Net Income Common Stockholders", "Net Income")
        tbv, d2 = self.mrq("Patrimonio tangibile", "Tangible Book Value")
        if ni is None or tbv is None:
            return missing("dati per ROTE non disponibili")
        if tbv <= 0:
            return missing("non calcolabile: patrimonio tangibile ≤ 0")
        v = pct(ni / tbv)
        return Datum(v=v, src=yh.SRC + " (calcolato)", deduced=True, definition="Utile netto TTM / Patrimonio netto tangibile",
                     why=f"{fmt_money(ni)} / {fmt_money(tbv)} = {v}%", docs=[d1, d2])

    def ptbv(self) -> Datum:
        mcap = _f(self.info.get("marketCap"))
        tbv, d = self.mrq("Patrimonio tangibile", "Tangible Book Value")
        if mcap is None or tbv is None:
            return missing("dati per P/TBV non disponibili")
        if tbv <= 0:
            return missing("non calcolabile: patrimonio tangibile ≤ 0")
        v = round(mcap / tbv, 2)
        return Datum(v=v, src=yh.SRC + " (calcolato)", deduced=True, definition="Capitalizzazione / Patrimonio netto tangibile",
                     why=f"{fmt_money(mcap)} / {fmt_money(tbv)} = {v}x",
                     docs=[self.ydoc("key-statistics", "capitalizzazione"), d])

    def cet1(self) -> Datum:
        r = sec.latest_ratio(self.t, ["CommonEquityTierOne", "CommonEquityTier1", "CET1"], 0.04, 0.30)
        if not r:
            return missing("CET1 non presente nei dati XBRL SEC: verificare nel 10-K / report trimestrale della banca")
        end, val, entry, concept = r
        v = round(val * 100, 2)
        return Datum(v=v, src=sec.SRC, definition=f"Voce XBRL '{concept}' dichiarata dalla banca",
                     docs=[sec.doc(sec.cik_str(self.t), entry)])

    def pb(self) -> Datum:
        mcap = _f(self.info.get("marketCap"))
        eq, d = self.mrq("Patrimonio netto", "Stockholders Equity", "Common Stock Equity")
        calc = round(mcap / eq, 2) if mcap and eq and eq > 0 else None
        direct = self.yinfo("priceToBook", "Prezzo / Valore contabile per azione (Yahoo)")
        if direct and direct.ok and (calc is None or abs(direct.v / calc - 1) <= 0.3):
            return direct
        if calc is None:
            return missing("P/B non calcolabile")
        why = f"Capitalizzazione {fmt_money(mcap)} / Patrimonio netto {fmt_money(eq)} = {calc}x."
        if direct and direct.ok:
            why += f" Il valore fornito da Yahoo ({direct.v}x) è stato scartato perché incoerente con questo calcolo."
        return Datum(v=calc, src=yh.SRC + " (calcolato)", deduced=True, definition="Capitalizzazione / Patrimonio netto",
                     why=why, docs=[self.ydoc("key-statistics", "capitalizzazione"), d])

    def combined_ratio(self) -> Datum:
        return missing("Combined Ratio non disponibile in automatico: verificare nel 10-K o su Morningstar")

    def p_ffo(self) -> Datum:
        mcap = _f(self.info.get("marketCap"))
        ni, d1 = self.ttm("Utile netto", "Net Income Common Stockholders", "Net Income")
        da, d2 = self.ttm("Ammortamenti", "Depreciation And Amortization", "Depreciation Amortization Depletion", kind="cf")
        if None in (mcap, ni, da):
            return missing("dati per FFO non disponibili")
        ffo = ni + abs(da)
        if ffo <= 0:
            return missing("non calcolabile: FFO ≤ 0")
        v = round(mcap / ffo, 2)
        return Datum(v=v, src=yh.SRC + " (calcolato)", deduced=True,
                     definition="Capitalizzazione / FFO;  FFO ≈ Utile netto + Ammortamenti (TTM)",
                     why=(f"FFO = {fmt_money(ni)} + {fmt_money(abs(da))} = {fmt_money(ffo)}; "
                          f"{fmt_money(mcap)} / {fmt_money(ffo)} = {v}x. Approssimazione: non sottrae le plusvalenze da cessione "
                          f"immobili (il FFO ufficiale NAREIT è nel comunicato trimestrale del REIT)."),
                     docs=[self.ydoc("key-statistics", "capitalizzazione"), d1, d2])

    # ============================================================ RUN
    def run(self, skip: set[str]) -> dict[str, Datum]:
        order = ["roic", "wacc", "roe", "op_margin", "fcf_margin", "rev_cagr3", "eps_cagr3", "fcf_cagr3", "roic_3y",
                 "nd_ebitda", "de", "current_ratio", "pe_ttm", "pe_fwd", "ev_ebitda", "ev_sales",
                 "rote", "ptbv", "cet1", "pb", "combined_ratio", "p_ffo"]
        for m in order:
            if m in skip:
                continue
            try:
                self.out[m] = getattr(self, m)()
            except Exception as e:
                self.out[m] = missing(f"errore nel calcolo: {e}")
        o = self.out
        # metriche derivate
        if "roic_vs_wacc" not in skip and "roic" in o and "wacc" in o:
            if o["roic"].ok and o["wacc"].ok:
                o["roic_vs_wacc"] = Datum(v=o["roic"].v > o["wacc"].v, src="Calcolato", deduced=True,
                                          definition="ROIC TTM confrontato con WACC",
                                          why=f"ROIC {o['roic'].v}% vs WACC {o['wacc'].v}%")
            else:
                o["roic_vs_wacc"] = missing("manca ROIC o WACC")
        if "fwd_lt_ttm" not in skip and "pe_ttm" in o and "pe_fwd" in o:
            if o["pe_ttm"].ok and o["pe_fwd"].ok:
                o["fwd_lt_ttm"] = Datum(v=o["pe_fwd"].v < o["pe_ttm"].v, src="Calcolato", deduced=True,
                                        definition="P/E forward confrontato con P/E trailing",
                                        why=f"Forward {o['pe_fwd'].v}x vs Trailing {o['pe_ttm'].v}x")
            else:
                o["fwd_lt_ttm"] = missing("manca P/E trailing o forward")
        if "ebit_yield_rule" not in skip and "op_margin" in o and "ev_sales" in o:
            if o["op_margin"].ok and o["ev_sales"].ok and o["ev_sales"].v > 0:
                v = round(o["op_margin"].v / o["ev_sales"].v, 2)
                o["ebit_yield_rule"] = Datum(v=v, src="Calcolato", deduced=True,
                                             definition="EBIT Margin (%) / EV/Sales (x) — regola del documento",
                                             why=f"{o['op_margin'].v}% / {o['ev_sales'].v}x = {v}% per ogni 1x di EV/Sales")
            else:
                o["ebit_yield_rule"] = missing("manca EBIT margin o EV/Sales")
        return o
