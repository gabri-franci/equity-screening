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
    news = [{"title": f"Notizia su {ticker}", "url": "https://example.com/n1", "source": "Test", "date": "2025-10-30T12:00",
             "summary": "Riassunto"}]
    return YahooData(ticker, info, inc_a, bal_a, cf_a, inc_q, bal_q, cf_q, cf_ttm, hist, news)


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
    # Yahoo ha solo 4 esercizi: CAGR sul periodo disponibile (3 anni), segnalato, giudizio mantenuto
    assert o["rev_cagr5"].v == pytest.approx(10.0, abs=0.01) and o["rev_cagr5"].period_years == 3
    assert scoring.rate("rev_cagr5", o["rev_cagr5"].to_dict(), "tech_ai", CFG)["label"] == "Buono"   # giudizio mantenuto
    assert o["fcf_margin"].v == pytest.approx((450 - 100) / 1340 * 100, abs=0.01)
    assert o["nd_ebitda"].v == pytest.approx(100 / 520, abs=0.01)
    assert o["de"].v == 0.30
    assert o["fwd_lt_ttm"].v is True
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
    assert q["ath"] == q["price"] and q["from_ath_pct"] == 0          # serie sempre crescente: oggi è il massimo
    y2 = fake(); y2.hist = pd.Series([100.0, 150.0, 120.0], index=pd.to_datetime(["2020-01-02", "2021-06-01", "2025-10-31"]))
    q2 = price_block(y2)
    assert q2["ath"] == 150.0 and q2["ath_date"] == "2021-06-01" and q2["from_ath_pct"] == -20.0


def test_news_normalization(monkeypatch):
    from screener.sources.yahoo import fetch_news

    class T:
        def get_news(self, count=10):
            return [{"content": {"title": "Nuovo formato", "pubDate": "2026-11-02T14:00:00Z",
                                 "provider": {"displayName": "Reuters"}, "canonicalUrl": {"url": "https://x/1"}}},
                    {"title": "Vecchio formato", "link": "https://x/2", "publisher": "AP", "providerPublishTime": 1762000000},
                    {"content": {"title": "Senza link"}}]
    n = fetch_news(T())
    assert [x["title"] for x in n] == ["Nuovo formato", "Vecchio formato"]

    class S:  # ricerca Yahoo: tiene solo le notizie collegate al titolo
        def __init__(self, *a, **k):
            self.news = [{"title": "Altro titolo", "link": "https://x/3", "relatedTickers": ["MSFT"], "providerPublishTime": 1762000000},
                         {"title": "Su Apple", "link": "https://x/4", "relatedTickers": ["AAPL"], "providerPublishTime": 1762000000}]
    import yfinance
    monkeypatch.setattr(yfinance, "Search", S)
    T.ticker = "AAPL"
    assert [x["title"] for x in fetch_news(T())] == ["Su Apple"]
    assert n[0]["source"] == "Reuters" and n[1]["url"] == "https://x/2"


def test_daily_refresh(tmp_path, monkeypatch):
    import json
    from screener import daily
    monkeypatch.setattr(run, "LATEST_JSON", tmp_path / "latest.json")
    monkeypatch.setattr(run, "DOCS_DIR", tmp_path)
    (tmp_path / "latest.json").write_text(json.dumps({"generated": "x", "macro": {}, "finviz_elite": False,
        "criteria": CFG["criteria"], "groups": CFG["groups"], "peer_medians": {},
        "records": [{"ticker": "AAPL", "name": "A", "sector_it": "Tecnologia", "group_name": "Tech", "updated": "x",
                     "memberships": [{"index": "S&P 500", "active": True, "first_seen": "x", "removed_on": ""}],
                     "metrics": {}, "currency": "USD"}]}))
    y = fake("AAPL")

    class T:
        def __init__(self, t): pass
        def history(self, **k): return pd.DataFrame({"Close": y.hist})
        def get_news(self, count=10): return [{"title": "N", "link": "https://x", "publisher": "P", "providerPublishTime": 1762000000}]
    import yfinance
    monkeypatch.setattr(yfinance, "Ticker", T)
    monkeypatch.setattr(yfinance, "Search", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("offline")))
    daily.main()
    d = json.loads((tmp_path / "latest.json").read_text())
    assert d["records"][0]["quote"]["price"] and d["records"][0]["news"][0]["title"] == "N" and d["prices_updated"]
    assert (tmp_path / "index.html").exists()


def test_annual_fallback_without_quarters():
    y = fake("MC.PA")
    y.inc_q = pd.DataFrame(); y.cf_q = pd.DataFrame()      # conti solo semestrali/annuali
    o = Engine("MC.PA", y, None, MACRO, CFG).run(set(CFG["skip_by_group"]["default"]))
    assert o["roic"].v is not None and "ultimo esercizio annuale" in o["roic"].docs[0]["label"]


def test_roic_not_meaningful_with_tiny_capital():
    y = fake("MEDP")
    y.bal_q = y.bal_q.T.assign(**{"Cash Cash Equivalents And Short Term Investments": [1290] * 5,
                                  "Total Assets": [3000] * 5}).T          # IC = 300 + 1000 − 1290 = 10
    o = Engine("MEDP", y, None, MACRO, CFG).run(set(CFG["skip_by_group"]["default"]))
    assert o["roic"].v is None and "non significativo" in o["roic"].note


def test_negative_values_no_judgment():
    o = Engine("NEG", fake(neg_eps=True), None, MACRO, CFG).run(set())
    assert o["pe_ttm"].v is None and "negativo" in o["pe_ttm"].note
    assert scoring.rate("pe_ttm", o["pe_ttm"].to_dict(), "tech_ai", CFG) == {}


def test_levels_and_bands():
    assert scoring.match_level(22, CFG["criteria"]["roic"]["levels"]) == "Molto buono"
    assert scoring.match_level(8, CFG["criteria"]["roic"]["levels"]) == ""
    assert scoring.match_level(12, CFG["criteria"]["roic"]["levels"]) == ""          # soglia Buono ora 15%
    assert scoring.match_level(16, CFG["criteria"]["roic"]["levels"]) == "Buono"
    assert scoring.match_level(21, CFG["criteria"]["roe"]["levels"]) == "Molto buono"  # soglia ora 20%
    assert scoring.match_level(13, CFG["criteria"]["rev_cagr5"]["levels"]) == "Molto buono"
    assert scoring.match_level(9, CFG["criteria"]["rev_cagr5"]["levels"]) == "Buono"
    assert scoring.match_level(9, CFG["criteria"]["eps_cagr5"]["levels"]) == "Buono"
    assert scoring.match_level(7.5, CFG["criteria"]["fcf_cagr5"]["levels"]) == "Buono"
    assert scoring.match_level(16, CFG["criteria"]["roic_avg5"]["levels"]) == "Buono"
    assert scoring.match_level(19, CFG["criteria"]["roe_avg5"]["levels"]) == ""
    assert scoring.match_level(26, CFG["criteria"]["ebitda_margin"]["levels"]) == "Molto buono"
    assert scoring.match_level(21, CFG["criteria"]["ebitda_margin"]["levels"]) == "Buono"
    r = scoring.rate("pe_hist", {"v": 20.0}, "tech_ai", CFG, {"pe_ttm": {"v": 25.0}})
    assert r["label"] == "Sopra media" and r["vs_current"]["diff_pct"] == 25.0
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
    (tmp_path / "wl.json").write_text('{"tickers": ["AAPL", "MC.PA"]}')
    (tmp_path / "dv.json").write_text('{"tickers": []}')
    monkeypatch.setattr(run, "WATCHLIST_JSON", tmp_path / "wl.json")
    monkeypatch.setattr(run, "DA_VALUTARE_JSON", tmp_path / "dv.json")
    sp = pd.DataFrame({"ticker": ["AAPL", "JPM", "O", "GOOGL", "GOOG"], "name": list("ABCDE"), "gics_sector": [""] * 5})
    nd = pd.DataFrame({"ticker": ["AAPL", "GOOGL", "GOOG"], "name": list("ADE"), "gics_sector": [""] * 3})
    monkeypatch.setattr(constituents, "fetch_current", lambda: {"S&P 500": sp, "NASDAQ 100": nd})
    sectors = {"AAPL": ("Technology", "Consumer Electronics"), "JPM": ("Financial Services", "Banks - Diversified"),
               "O": ("Real Estate", "REIT - Retail"), "GOOGL": ("Communication Services", "Internet Content & Information"),
               "MC.PA": ("Consumer Cyclical", "Luxury Goods")}
    monkeypatch.setattr(run.yh, "fetch", lambda t: fake(t, *sectors[t]))
    monkeypatch.setattr(run.macro, "risk_free", lambda s: Datum(v=4.0, src="test"))
    run.main([])

    data = json.loads((tmp_path / "docs" / "data" / "latest.json").read_text())
    recs = {r["ticker"]: r for r in data["records"]}
    assert set(recs) == {"AAPL", "JPM", "O", "GOOGL", "MC.PA"}      # GOOG confluisce in GOOGL; MC.PA dalla watchlist
    assert recs["MC.PA"]["memberships"] == [] and data["watchlist"] == ["AAPL", "MC.PA"]
    assert recs["GOOGL"]["secondary"] == ["GOOG"] and recs["GOOGL"]["tech_ai"]
    assert {m["index"] for m in recs["AAPL"]["memberships"]} == {"S&P 500", "NASDAQ 100"}
    assert "roic" not in recs["JPM"]["metrics"] and "rote" in recs["JPM"]["metrics"]
    assert "p_ffo" in recs["O"]["metrics"] and "pe_ttm" not in recs["O"]["metrics"]
    assert recs["AAPL"]["metrics"]["roic"]["label"] == "Molto buono"
    assert "ebitda_margin" in recs["AAPL"]["metrics"] and "pe_hist" in recs["AAPL"]["metrics"]
    assert "roic_avg5" not in recs["JPM"]["metrics"] and "roe_avg5" in recs["JPM"]["metrics"]
    assert (tmp_path / "docs" / "screening.xlsx").stat().st_size > 5000
    html = (tmp_path / "docs" / "index.html").read_text()
    assert "__DATA__" not in html and "AAPL" in html
    assert list((tmp_path / "history").glob("*.csv.gz"))
    assert recs["AAPL"]["quote"]["price"] and recs["AAPL"]["metrics"]["fcf"]["v"] == 350
    assert "fcf" not in recs["JPM"]["metrics"]
    assert recs["AAPL"]["news"][0]["title"] == "Notizia su AAPL"
    assert data["repo"] and "watchlist" in data

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
    assert set(recs) == {"AAPL", "JPM", "O", "GOOGL", "MC.PA"}
    assert not recs["JPM"]["memberships"][0]["active"]
    assert recs["O"]["stale"] is True and "p_ffo" in recs["O"]["metrics"]


# ------------------------------------------------------------------ storico 5 anni con bilanci SEC simulati
def sec_facts():
    def flow(vals):
        return [{"start": f"{y}-01-01", "end": f"{y}-12-31", "val": v, "form": "10-K", "filed": f"{y + 1}-02-15",
                 "accn": f"0000000001-{y + 1 - 2000:02d}-000001", "fy": y + 1, "fp": "FY"} for y, v in vals.items()]

    def inst(vals):
        return [{"end": f"{y}-12-31", "val": v, "form": "10-K", "filed": f"{y + 1}-02-15",
                 "accn": f"0000000001-{y + 1 - 2000:02d}-000001", "fy": y + 1, "fp": "FY"} for y, v in vals.items()]
    years = range(2019, 2026)
    split_before = lambda y: y <= 2022   # frazionamento 2:1 il 2023-06-01: i bilanci fino al FY2022 sono pre-split
    eps = {y: 2 * 1.08 ** (y - 2019) * (2 if split_before(y) else 1) for y in years}
    shares = {y: 100e6 / (2 if split_before(y) else 1) for y in years}
    g = {
        "Revenues": {"USD": flow({y: 1000e6 * 1.1 ** (y - 2019) for y in years})},
        "EarningsPerShareDiluted": {"USD/shares": flow(eps)},
        "OperatingIncomeLoss": {"USD": flow({y: 300e6 for y in years})},
        "NetIncomeLoss": {"USD": flow({y: 200e6 for y in years})},
        "IncomeTaxExpenseBenefit": {"USD": flow({y: 50e6 for y in years})},
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest": {"USD": flow({y: 250e6 for y in years})},
        "NetCashProvidedByUsedInOperatingActivities": {"USD": flow({y: 400e6 * 1.07 ** (y - 2019) for y in years})},
        "PaymentsToAcquirePropertyPlantAndEquipment": {"USD": flow({y: 100e6 for y in years})},
        "DepreciationDepletionAndAmortization": {"USD": flow({y: 100e6 for y in years})},
        "WeightedAverageNumberOfDilutedSharesOutstanding": {"shares": flow(shares)},
        "StockholdersEquity": {"USD": inst({y: 1000e6 for y in years})},
        "LongTermDebt": {"USD": inst({y: 500e6 for y in years})},
        "CashAndCashEquivalentsAtCarryingValue": {"USD": inst({y: 300e6 for y in years})},
    }
    return {"facts": {"us-gaap": {k: {"units": v} for k, v in g.items()}}}


def test_history_5y(monkeypatch):
    monkeypatch.setattr(sec, "facts", lambda t: sec_facts())
    monkeypatch.setattr(sec, "cik_for", lambda t: "0000000001")
    y = fake("HIST")
    days = pd.bdate_range("2018-01-01", "2025-12-31")
    y.hist = pd.Series(40.0, index=days)
    y.splits = pd.Series([2.0], index=[pd.Timestamp("2023-06-01")])
    o = Engine("HIST", y, None, MACRO, CFG).run(set(CFG["skip_by_group"]["default"]))
    assert o["rev_cagr5"].v == pytest.approx(10.0, abs=0.01) and "10-K" in o["rev_cagr5"].docs[0]["label"]
    assert o["eps_cagr5"].v == pytest.approx(8.0, abs=0.01)          # grazie alla rettifica per il frazionamento
    assert "frazionamenti" in o["eps_cagr5"].why
    exp_fcf = ((400 * 1.07 ** 6 - 100) / (400 * 1.07 - 100)) ** 0.2 - 1
    assert o["fcf_cagr5"].v == pytest.approx(exp_fcf * 100, abs=0.01)
    assert o["roic_avg5"].v == pytest.approx(20.0, abs=0.01) and len(o["roic_avg5"].series) == 5   # 300×0,8/1200
    assert o["roe_avg5"].v == pytest.approx(20.0, abs=0.01)
    exp_pe = sum(40 / (2 * 1.08 ** (yy - 2019)) for yy in range(2021, 2026)) / 5
    assert o["pe_hist"].v == pytest.approx(exp_pe, abs=0.01) and "EPS rettificato" in o["pe_hist"].why
    assert o["ev_ebitda_hist"].v == pytest.approx(10.5, abs=0.01)   # (40×100M + 500M − 300M) / 400M
    assert o["ebitda_margin"].v is not None


# ------------------------------------------------------------------ ricerca e analisi dalla dashboard
def test_analizza_preview_and_add(tmp_path, monkeypatch):
    import json
    import yfinance
    from screener import analizza

    class S:
        def __init__(self, q, **k):
            self.quotes = [{"symbol": "RMS.PA", "longname": "Hermès International", "exchDisp": "Paris", "quoteType": "EQUITY"},
                           {"symbol": "HESAY", "shortname": "Hermes ADR", "exchDisp": "OTC", "quoteType": "EQUITY"}]
    monkeypatch.setattr(yfinance, "Search", S)
    sym, cands = analizza.resolve("Hermes")
    assert sym == "RMS.PA" and len(cands) == 2
    assert analizza.resolve("hesay")[0] == "HESAY"      # ticker scritto esatto

    latest = tmp_path / "latest.json"
    latest.write_text(json.dumps({"generated": "x", "macro": {"rf": {"v": 4.0}, "erp": {"v": 5.0}}, "finviz_elite": False,
                                  "criteria": CFG["criteria"], "groups": CFG["groups"], "peer_medians": {}, "records": []}))
    monkeypatch.setattr(run, "LATEST_JSON", latest)
    monkeypatch.setattr(run, "DOCS_DIR", tmp_path)
    monkeypatch.setattr(analizza, "PREVIEW_DIR", tmp_path / "previews")
    monkeypatch.setattr(run.yh, "fetch", lambda t: fake(t, "Consumer Cyclical", "Luxury Goods"))
    analizza.main(["preview", "--request-id", "r1", "--payload", "Hermes"])
    pv = json.loads((tmp_path / "previews" / "r1.json").read_text())
    rec = pv["results"][0]["record"]
    assert pv["results"][0]["status"] == "ok" and rec["ticker"] == "RMS.PA" and rec["memberships"] == []
    assert rec["metrics"]["roic"]["label"]                 # giudizi già applicati nell'anteprima

    analizza.main(["add", "--payload", "RMS.PA:watchlist"])
    data = json.loads(latest.read_text())
    assert [r["ticker"] for r in data["records"]] == ["RMS.PA"] and (tmp_path / "index.html").exists()
