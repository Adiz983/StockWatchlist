"""Offline tests for compare / backtest / research_assistant (synthetic data, no network)."""
import sys, types
import numpy as np, pandas as pd

# data_service imports yfinance at module load; stub it so tests run offline.
sys.modules.setdefault("yfinance", types.ModuleType("yfinance"))

import analysis, backtest, compare, research_assistant as ra


def prices(n=1500, drift=0.0004, vol=0.012, seed=1, tz=None):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2018-01-01", periods=n, tz=tz)
    close = 100 * np.exp(np.cumsum(rng.normal(drift, vol, n)))
    return pd.DataFrame({"Open": close, "High": close, "Low": close, "Close": close,
                         "Adj Close": close, "Volume": 1e6}, index=idx)


def test_buy_hold_matches_price_when_free():
    df = prices()
    r = backtest.run_backtest(df, "Buy & hold", (), cost_bps=0, slippage_bps=0)
    expected = df["Adj Close"].iloc[-1] / df["Adj Close"].iloc[0] - 1
    assert abs(r.stats["total"] / 100 - expected) < 0.01      # 1 bar of flat start


def test_no_lookahead():
    df = prices()
    price = backtest.price_column(df)
    desired = backtest.desired_position(price, "SMA crossover", (20, 50))
    r = backtest.run_backtest(df, "SMA crossover", (20, 50), cost_bps=0, slippage_bps=0)
    # executed position is yesterday's desired position
    shifted = desired.shift(1).fillna(0).loc[r.position.index]
    assert (r.position.iloc[1:] == shifted.iloc[1:]).all()


def test_costs_reduce_returns_and_drag_is_reported():
    df = prices()
    free = backtest.run_backtest(df, "SMA crossover", (10, 30), cost_bps=0, slippage_bps=0)
    paid = backtest.run_backtest(df, "SMA crossover", (10, 30), cost_bps=25, slippage_bps=25)
    assert paid.stats["total"] < free.stats["total"]
    assert paid.summary["cost_drag"] > 0
    assert abs(free.summary["cost_drag"]) < 1e-9


def test_drawdown_known_values():
    eq = pd.Series([100, 120, 90, 110, 60.0])
    assert abs(backtest.max_drawdown(eq) - (-50.0)) < 1e-9


def test_all_strategies_run_and_split_present():
    df = prices(tz="America/New_York")
    bench = backtest.price_column(prices(seed=7))
    for name, (_, defaults, _d) in backtest.STRATEGIES.items():
        r = backtest.run_backtest(df, name, defaults, bench_price=bench, benchmark="BM")
        assert len(r.equity) == len(r.position)
        assert r.equity_bench is not None
        assert r.split and "out_of_sample" in r.split
        assert r.position.isin([0.0, 1.0]).all()


def test_start_date_tz_aware_and_naive():
    df = prices(tz="America/New_York")
    r1 = backtest.run_backtest(df, "Buy & hold", (), start="2021-01-01")
    r2 = backtest.run_backtest(df, "Buy & hold", (), start=df.index[-300])
    assert r1.start >= pd.Timestamp("2021-01-01", tz="America/New_York")
    assert len(r2.equity) == 300


def test_bad_inputs_raise():
    df = prices()
    for bad in (lambda: backtest.run_backtest(df.head(20), "Buy & hold", ()),
                lambda: backtest.run_backtest(df, "SMA crossover", (200, 50)),
                lambda: backtest.run_backtest(df, "Buy & hold", (), capital=0),
                lambda: backtest.run_backtest(df, "Buy & hold", (), cost_bps=-1)):
        try:
            bad()
        except ValueError:
            continue
        raise AssertionError("expected ValueError")


def test_trades_open_flag():
    df = prices()
    r = backtest.run_backtest(df, "Buy & hold", (), cost_bps=0, slippage_bps=0)
    assert len(r.trades) == 1 and r.trades[0]["open"]


# ---------------- compare ----------------
def fake_fund(revs, net=None):
    cols = pd.to_datetime([f"{2020 + i}-09-30" for i in range(len(revs))])
    data = {"Total Revenue": revs, "Gross Profit": [r * 0.4 for r in revs],
            "Operating Income": [r * 0.25 for r in revs],
            "Net Income": [r * 0.2 for r in revs]}
    inc = pd.DataFrame(data, index=cols).T
    return {"annual": {"income": analysis.clean_statement(inc), "balance": pd.DataFrame(),
                       "cashflow": pd.DataFrame()},
            "quarterly": {"income": pd.DataFrame(), "balance": pd.DataFrame(), "cashflow": pd.DataFrame()},
            "errors": []}


def make_bundle():
    a, b = prices(seed=1), prices(seed=2)
    data = {
        "AAA": {"symbol": "AAA", "info": {"trailingPE": 20, "currency": "USD", "shortName": "Alpha"},
                "close": a["Close"], "fund": fake_fund([100, 110, 121, 133.1]), "error": None},
        "BBB": {"symbol": "BBB", "info": {"trailingPE": 30, "grossMargins": 0.5, "currency": "EUR"},
                "close": b["Close"], "fund": fake_fund([100, 105, 110, 115]), "error": None},
        "CCC": {"symbol": "CCC", "info": {}, "close": None, "fund": None, "error": "boom"},
    }
    return {"symbols": ["AAA", "BBB", "CCC"], "data": data}


def test_compare_table():
    table = compare.build_comparison(make_bundle())
    rows = {label: (vals, hl) for _, rws in table["sections"] for label, vals, _s, hl in rws}
    vals, hl = rows["Latest FY, YoY"]
    assert abs(vals["AAA"] - 10.0) < 1e-6 and vals["CCC"] is None
    assert compare.leaders(vals, hl) == {"AAA"}
    cagr = [v for k, (v, _) in rows.items() if k.startswith("Revenue CAGR")][0]
    assert abs(cagr["AAA"] - 10.0) < 1e-6
    # BBB takes Yahoo's 50% gross margin; AAA falls back to the statement's 40%
    gm, _ = rows["Gross margin"]
    assert abs(gm["BBB"] - 50.0) < 1e-6 and abs(gm["AAA"] - 40.0) < 1e-6
    # valuation rows are never ranked
    pe, pe_hl = rows["P/E (TTM)"]
    assert compare.leaders(pe, pe_hl) == set()
    assert any("Mixed currencies" in n for n in table["notes"]) and any("CCC" in n for n in table["notes"])


def test_rebased_frame_common_start():
    reb = compare.rebased_frame({"A": prices(seed=1)["Close"], "B": prices(seed=2, tz="UTC")["Close"]}, "1y")
    assert (reb.iloc[0].abs() < 1e-9).all() and reb.shape[1] == 2


def test_parse_symbols():
    assert compare.parse_symbols("aapl, msft  AAPL;nvda") == ["AAPL", "MSFT", "NVDA"]


# ---------------- assistant ----------------
def make_facts(notes=None):
    info = {"longName": "Alpha Inc", "trailingPE": 20.5, "grossMargins": 0.44, "totalDebt": 5e9}
    return ra.build_facts("AAA", info, fake_fund([100e9, 110e9, 121e9, 133.1e9]),
                          analysis.add_indicators(prices()), notes)


def test_retrieval_finds_revenue_and_note():
    notes = [{"id": "x", "title": "Thesis", "text": "I think services margin expansion is key",
              "created": 1_700_000_000}]
    facts = make_facts(notes)
    hits = ra.retrieve("How has revenue changed?", facts)
    assert hits and "Total Revenue" in hits[0].text
    nh = ra.retrieve("What did I write in my saved notes about margin?", facts)
    assert any(f.kind == "note" for f in nh)


def test_no_match_never_calls_model():
    called = []
    out = ra.answer_question("zebra quantum helicopter", "AAA", make_facts(), caller=lambda *a: called.append(1))
    assert out.mode == "no_match" and not called


def test_verify_flags_bad_cites_and_numbers(monkeypatch=None):
    facts = make_facts()
    rev = [f for f in facts if "Total Revenue" in f.label][0]
    good = f"Revenue reached $133.10B [{rev.id}], up 10.0% [{rev.id}]."
    assert ra.verify_answer(good, facts)["ok"]
    bad = "Revenue reached $999.99B [F9999]."
    res = ra.verify_answer(bad, facts)
    assert not res["ok"] and res["unknown"] == ["F9999"] and "$999.99" in res["unmatched"]


def test_llm_path_and_fallback(monkeypatch=None):
    import os
    os.environ["GEMINI_API_KEY"] = "test"
    facts = make_facts()
    hit = ra.retrieve("revenue growth", facts)[0]
    ok = ra.answer_question("revenue growth", "AAA", facts, caller=lambda s, u: f"Revenue grew 10.0% [{hit.id}].")
    assert ok.mode == "llm" and ok.cited == [hit.id]
    def boom(s, u): raise RuntimeError("down")
    fb = ra.answer_question("revenue growth", "AAA", facts, caller=boom)
    assert fb.mode == "lookup" and "down" in fb.note
    # prompt carries the guard-rails and only retrieved facts
    msg = ra.build_user_message("q", "AAA", [hit])
    assert hit.id in msg and "Do not predict" in ra.SYSTEM_PROMPT
    del os.environ["GEMINI_API_KEY"]
    os.environ.pop("GOOGLE_API_KEY", None)
    assert ra.answer_question("revenue growth", "AAA", facts).mode == "lookup"


def test_notes_store(tmp_path=None):
    import tempfile, pathlib, notes_store as ns
    p = pathlib.Path(tempfile.mkdtemp()) / "n.json"
    n = ns.add_note("aapl", "hello", "t", path=p)
    assert ns.load_notes("AAPL", path=p)[0]["id"] == n["id"]
    assert ns.delete_note(n["id"], path=p) and not ns.load_notes(path=p)


if __name__ == "__main__":
    fails = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            try:
                fn(); print("PASS", name)
            except Exception as e:  # noqa: BLE001
                fails += 1; print("FAIL", name, "->", repr(e))
    sys.exit(1 if fails else 0)
