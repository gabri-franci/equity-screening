"""Test offline con dati simulati (nessun accesso a internet).

I numeri sono inventati: servono solo a verificare formule, giudizi e casi limite.
"""
import json

import pandas as pd
import pytest

from screener import constituents, run, scoring
from screener.config import load_config
from screener.metrics import Engine, cagr, group_for
from screener.model import Datum
from screener.sources import sec
from screener.sources.yahoo import YahooData

CFG = load_config()
YEARS = pd.to_datetime(["2025-12-31", "2024-12-31", "2023-12-31", "2022-12-31"])
QUARTERS = pd.to_datetime(["2025-09-30", "2025-06-30", "2025-03-31", "2024-12-31", "2024-09-30"])


def frame(rows: dict, cols) -> pd.DataFrame:
    return pd.DataFrame({k: list(v) for k, v in rows.items()}, index=cols).T


def fake(ticker="TEST", sector="Technology", industry="Software - Infrastructure", neg_eps=False):
    inc_a = frame({"Total Revenue": [1331, 1210, 1100, 1000], "Operating Income": [400, 350, 300, 250],
                   "Diluted EPS": [4.0, 3.6, 3.3, 3.0] if not neg_eps else [1.0, 0.5, -0.2, -0.5],
                   "Tax Rate For Calcs": [0.20, 0.20, 0.21, 0.21]}, YEARS)
    bal_a = frame({"Total Debt": [300] * 4, "Stockholders Equity": [1000, 950, 900, 850],
                   "Cash Cash Equivalents And Short Term Investments": [200] * 4}, YEARS)
    cf_a = frame({"Operating Cash Flow": [450, 400, 360, 330], "Capital Expenditure": [-100, -90, -80, -80]}, YEARS)
    inc_q = frame({"Total Revenue": [350, 340, 330, 320, 300], "Operating Income": [110, 105, 100, 95, 90],
                   "Net Income": [80, 78, 75, 70, 65], "Net Income Common Stockholders": [80, 78, 75, 70, 65],
                   "Interest Expense": [3, 3, 3, 3, 3]}, QUARTERS)
    bal_q = frame({"Total Debt": [300] * 5, "Stockholders Equity": [1000] * 5,
                   "Cash Cash Equivalents And Short Term Investments": [200] * 5,
                   "Current Assets": [600] * 5, "Current Liabilities": [400] * 5,
                   "Tangible Book Value": [700] * 5}, QUARTERS)
    cf_q = frame({"Operating Cash Flow": [120, 115, 110, 105, 100], "Capital Expenditure": [-25] * 5,
                  "Depreciation And Amortization": [30] * 5, "Free Cash Flow": [95, 90, 85, 80, 75]}, QUARTERS)
    cf_a = cf_a.T.assign(**{"Free Cash Flow": [350, 310, 280, 250]}).T
    cf_ttm = frame({"Free Cash Flow": [350]}, pd.to_datetime(["2025-09-30"]))
    days = pd.bdate_range("2021-01-04", "2025-10-31")
    hist = pd.Series([50 + i * 0.04 for i in range(len(days))], index=days)
    info = {"quoteType": "EQUITY", "longName": f"{ticker} Inc", "sector": sector, "industry": industry,
            "returnOnEquity": 0.31, "operatingMargins": 0.30, "currentRatio": 1.5, "debtToEquity": 30.0,
            "trailingPE": 28.0, "forwardPE": 24.0, "trailingEps": -1.0 if neg_eps else 3.0, "forwardEps": 3.5,
            "enterpriseToEbitda": 18.0, "enterpriseToRevenue": 6.0, "ebitda": 520.0, "totalDebt": 300.0,
            "totalCash": 200.0, "beta": 1.2, "marketCap": 9000.0, "priceToBook": 3.0, "currentPrice": 100.0}
    return YahooData(ticker, info, inc_a, bal_a, cf_a, inc_q, bal_q, cf_q, cf_ttm, hist)


MACRO = {"rf": Datum(v=4.0, docs=[]), "erp": Datum(v=5.0, docs=[])}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr(sec, "facts", lambda t: None)
    monkeypatch.setattr(sec, "cik_for", lambda t: None)


def test_cagr_edge_cases():
    assert cagr(1000, 1331, 3) == pytest.approx(0.10)
    assert cagr(-1, 5, 3) is None and cagr(5, 0, 3) is None


def test_core_metrics():
    o = Engine("TEST", fake(), None, MACRO, CFG).run(set(CFG["skip_by_group"]["default"]))
    # ROIC = 410 × (1−0.20) / (300 + 1000 − 200) = 29.82%
    assert o["roic"].v == pytest.approx(29.82, abs=0.01) and o["roic"].deduced
    assert o["roe"].v == 31.0 and not o["roe"].deduced           # fornito direttamente da Yahoo
    assert o["rev_cagr3"].v == pytest.approx(10.0, abs=0.01)
    assert o["fcf_margin"].v == pytest.approx((450 - 100) / 1340 * 100, abs=0.01)
    assert o["nd_ebitda"].v == pytest.approx(100 / 520, abs=0.01)
    assert o["de"].v == 0.30
    assert o["fwd_lt_ttm"].v is True
    assert o["roic_3y"].series[0] < o["roic_3y"].series[-1]
    # WACC: Ke = 4 + 1.2×5 = 10%; Kd = 12/300 = 4%; E=9000 D=300
    exp = 9000 / 9300 * 10 + 300 / 9300 * 4 * 0.8
    assert o["wacc"].v == pytest.approx(exp, abs=0.01)
    assert o["roic_vs_wacc"].v is True


def test_fcf_and_price():
    y = fake()
    o = Engine("TEST", y, None, MACRO, CFG).run(set(CFG["skip_by_group"]["default"]))
    assert o["fcf"].v == 350 and not o["fcf"].deduced and o["fcf"].series == [250, 280, 310, 350]
    y.cf_ttm = pd.DataFrame()
    assert Engine("TEST", y, None, MACRO, CFG).fcf().v == 95 + 90 + 85 + 80
    from screener.metrics import price_block
    q = price_block(fake())
    last = 50 + (len(fake().hist) - 1) * 0.04
    assert q["price"] == round(last, 2) and q["date"] == "2025-10-31"
    assert q["changes"]["1G"] == round((last / (last - 0.04) - 1) * 100, 2)
    assert q["changes"]["1A"] > 0 and q["weekly"] and 55 <= len(q["daily"]) <= 70
    assert q["weekly"][-1][0] == 20251031


def test_negative_values_no_judgment():
    o = Engine("NEG", fake(neg_eps=True), None, MACRO, CFG).run(set())
    assert o["pe_ttm"].v is None and "negativo" in o["pe_ttm"].note
    assert o["eps_cagr3"].v is None and "≤ 0" in o["eps_cagr3"].note
    assert scoring.rate("pe_ttm", o["pe_ttm"].to_dict(), "tech_ai", CFG) == {}


def test_levels_and_bands():
    assert scoring.match_level(22, CFG["criteria"]["roic"]["levels"]) == "Molto buono"
    assert scoring.match_level(12, CFG["criteria"]["roic"]["levels"]) == "Buono"
    assert scoring.match_level(8, CFG["criteria"]["roic"]["levels"]) == ""
    assert scoring.match_level(7, CFG["criteria"]["eps_cagr3"]["levels"]) == "Molto buono"
    assert scoring.match_level(-3, CFG["criteria"]["eps_cagr3"]["levels"]) == ""
    assert scoring.match_level(-0.5, CFG["criteria"]["nd_ebitda"]["levels"]) == ""   # cassa netta: nessun giudizio
    r = scoring.rate("pe_ttm", {"v": 60}, "tech_ai", CFG)
    assert r["label"] == "Sopra fascia" and r["band"]["origin"] == "documento"
    r = scoring.rate("ev_ebitda", {"v": 5}, "energy", CFG)
    assert r["label"] == "Nella fascia" and r["band"]["origin"] == "proposta" and r["band"]["why"]
    assert scoring.rate("ebit_yield_rule", {"v": 9}, "tech_ai", CFG) == {}
    assert scoring.rate("ebit_yield_rule", {"v": 9}, "industrials", CFG)["label"] == "Buono"


def test_groups():
    assert group_for("GOOGL", "Communication Services", "Internet Content & Information", CFG) == "tech_ai"
    assert group_for("JPM", "Financial", "Banks - Diversified", CFG) == "bank"
    assert group_for("V", "Financial", "Credit Services", CFG) == "financial_other"
    assert group_for("O", "Real Estate", "REIT - Retail", CFG) == "reit"
    assert group_for("PFE", "Healthcare", "Drug Manufacturers - General", CFG) == "pharma"
    assert group_for("T", "Communication Services", "Telecom Services", CFG) == "telecom"


def test_membership_never_deletes(tmp_path, monkeypatch):
    monkeypatch.setattr(constituents, "MEMBERSHIP_CSV", tmp_path / "m.csv")
    cur = {"S&P 500": pd.DataFrame({"ticker": ["AAA", "BBB"], "name": ["A", "B"], "gics_sector": ["x", "y"]})}
    constituents.save_membership(constituents.update_membership(cur, "2026-01-01"))
    cur = {"S&P 500": pd.DataFrame({"ticker": ["AAA", "CCC"], "name": ["A", "C"], "gics_sector": ["x", "z"]})}
    m = constituents.update_membership(cur, "2026-01-08")
    b = m[m.ticker == "BBB"].iloc[0]
    assert len(m) == 3 and not b.active and b.removed_on == "2026-01-08"


def test_full_run_offline(tmp_path, monkeypatch):
    """Esecuzione completa con 4 titoli finti: verifica file JSON, Excel, dashboard, storico."""
    monkeypatch.setattr(constituents, "MEMBERSHIP_CSV", tmp_path / "membership.csv")
    monkeypatch.setattr(run, "LATEST_JSON", tmp_path / "docs" / "data" / "latest.json")
    monkeypatch.setattr(run, "HISTORY_DIR", tmp_path / "history")
    monkeypatch.setattr(run, "DOCS_DIR", tmp_path / "docs")
    monkeypatch.setattr(run.time, "sleep", lambda s: None)
    sp = pd.DataFrame({"ticker": ["AAPL", "JPM", "O", "GOOGL", "GOOG"], "name": list("ABCDE"), "gics_sector": [""] * 5})
    nd = pd.DataFrame({"ticker": ["AAPL", "GOOGL", "GOOG"], "name": list("ADE"), "gics_sector": [""] * 3})
    monkeypatch.setattr(constituents, "fetch_current", lambda: {"S&P 500": sp, "NASDAQ 100": nd})
    sectors = {"AAPL": ("Technology", "Consumer Electronics"), "JPM": ("Financial Services", "Banks - Diversified"),
               "O": ("Real Estate", "REIT - Retail"), "GOOGL": ("Communication Services", "Internet Content & Information")}
    monkeypatch.setattr(run.yh, "fetch", lambda t: fake(t, *sectors[t]))
    monkeypatch.setattr(run.macro, "risk_free", lambda s: Datum(v=4.0, src="test"))
    run.main([])

    data = json.loads((tmp_path / "docs" / "data" / "latest.json").read_text())
    recs = {r["ticker"]: r for r in data["records"]}
    assert set(recs) == {"AAPL", "JPM", "O", "GOOGL"}               # GOOG confluisce in GOOGL
    assert recs["GOOGL"]["secondary"] == ["GOOG"] and recs["GOOGL"]["tech_ai"]
    assert {m["index"] for m in recs["AAPL"]["memberships"]} == {"S&P 500", "NASDAQ 100"}
    assert "roic" not in recs["JPM"]["metrics"] and "rote" in recs["JPM"]["metrics"]
    assert "p_ffo" in recs["O"]["metrics"] and "pe_ttm" not in recs["O"]["metrics"]
    assert recs["AAPL"]["metrics"]["roic"]["label"] == "Molto buono"
    assert (tmp_path / "docs" / "screening.xlsx").stat().st_size > 5000
    html = (tmp_path / "docs" / "index.html").read_text()
    assert "__DATA__" not in html and "AAPL" in html
    assert list((tmp_path / "history").glob("*.csv.gz"))
    assert recs["AAPL"]["quote"]["price"] and recs["AAPL"]["metrics"]["fcf"]["v"] == 350
    assert "fcf" not in recs["JPM"]["metrics"]

    # settimana dopo: JPM esce dall'indice e Yahoo non risponde per O -> nulla viene eliminato
    sp2 = sp[sp.ticker != "JPM"]
    monkeypatch.setattr(constituents, "fetch_current", lambda: {"S&P 500": sp2, "NASDAQ 100": nd})

    def flaky(t):
        if t == "O":
            raise RuntimeError("timeout")
        return fake(t, *sectors[t])
    monkeypatch.setattr(run.yh, "fetch", flaky)
    run.main([])
    recs = {r["ticker"]: r for r in json.loads((tmp_path / "docs" / "data" / "latest.json").read_text())["records"]}
    assert set(recs) == {"AAPL", "JPM", "O", "GOOGL"}
    assert not recs["JPM"]["memberships"][0]["active"]
    assert recs["O"]["stale"] is True and "p_ffo" in recs["O"]["metrics"]
