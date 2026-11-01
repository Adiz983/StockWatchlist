"""Side-by-side comparison of 2-5 companies.

Everything except load_compare_bundle() is pure (no Qt, no network) so it can
be unit-tested with synthetic data.
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

import analysis
import data_service as ds

MIN_SYMBOLS, MAX_SYMBOLS = 2, 5
COMPARE_PERIODS = ["3mo", "6mo", "ytd", "1y", "2y", "5y"]


def parse_symbols(text: str) -> list:
    """'aapl, msft  nvda' -> ['AAPL', 'MSFT', 'NVDA'] (order kept, duplicates dropped)."""
    seen, out = set(), []
    for token in re.split(r"[\s,;]+", text.upper()):
        token = token.strip()
        if token and token not in seen:
            seen.add(token)
            out.append(token)
    return out


# --------------------------------------------------------------------------
# Loading (blocking - call from a worker thread)
# --------------------------------------------------------------------------
def _load_one(symbol: str, force: bool) -> dict:
    try:
        return {
            "symbol": symbol,
            "info": ds.get_info(symbol, force),
            "close": ds.get_history(symbol, force)["Close"].dropna(),
            "fund": ds.load_fundamentals(symbol, force),
            "error": None,
        }
    except Exception as error:  # noqa: BLE001 - one bad symbol must not sink the rest
        return {"symbol": symbol, "info": {}, "close": None, "fund": None, "error": str(error)}


def load_compare_bundle(symbols: list, force: bool = False) -> dict:
    symbols = list(symbols)
    with ThreadPoolExecutor(max_workers=min(5, max(1, len(symbols)))) as pool:
        results = list(pool.map(lambda s: _load_one(s, force), symbols))
    return {"symbols": symbols, "data": {r["symbol"]: r for r in results}}


# --------------------------------------------------------------------------
# Metric extraction
# --------------------------------------------------------------------------
def _num(value):
    return ds._finite(value)  # noqa: SLF001 - reuse the same finite-float guard


def _annual_revenue(fund) -> pd.Series | None:
    if not fund:
        return None
    income = fund.get("annual", {}).get("income")
    if income is None or income.empty:
        return None
    row = analysis.find_row(income, "Total Revenue", "Operating Revenue")
    if row is None:
        return None
    row = row.dropna()
    return row if len(row) >= 2 else None


def revenue_growth_metrics(fund, info) -> dict:
    """Latest-FY YoY, multi-year CAGR and Yahoo's most-recent-quarter YoY (percent)."""
    out = {"fy_yoy": None, "cagr": None, "cagr_years": None, "mrq_yoy": None}
    rev = _annual_revenue(fund)
    if rev is not None:
        last, prev = float(rev.iloc[-1]), float(rev.iloc[-2])
        if prev > 0:
            out["fy_yoy"] = (last / prev - 1.0) * 100.0
        first = float(rev.iloc[0])
        years = len(rev) - 1
        if first > 0 and last > 0 and years >= 2:
            out["cagr"] = ((last / first) ** (1.0 / years) - 1.0) * 100.0
            out["cagr_years"] = years
    mrq = _num(info.get("revenueGrowth"))
    out["mrq_yoy"] = None if mrq is None else mrq * 100.0
    return out


def margin_metrics(fund, info) -> dict:
    """TTM margins from Yahoo key stats; fall back to the latest annual statement."""
    out = {
        "gross": _num(info.get("grossMargins")),
        "operating": _num(info.get("operatingMargins")),
        "net": _num(info.get("profitMargins")),
    }
    out = {k: (None if v is None else v * 100.0) for k, v in out.items()}
    income = (fund or {}).get("annual", {}).get("income")
    if income is not None and not income.empty:
        rev = analysis.find_row(income, "Total Revenue", "Operating Revenue")
        if rev is not None and pd.notna(rev.iloc[-1]) and rev.iloc[-1]:
            denom = float(rev.iloc[-1])
            for key, names in (("gross", ("Gross Profit",)), ("operating", ("Operating Income",)),
                               ("net", ("Net Income", "Net Income Common Stockholders"))):
                if out[key] is None:
                    row = analysis.find_row(income, *names)
                    if row is not None and pd.notna(row.iloc[-1]):
                        out[key] = float(row.iloc[-1]) / denom * 100.0
    return out


def price_performance(close: pd.Series | None) -> dict:
    """label -> percent return, using the same windows as the main Performance table."""
    if close is None or len(close.dropna()) < 2:
        return {}
    rows = analysis.trailing_returns(close, None, "")
    return {label: stock for label, stock, _ in rows}


# --------------------------------------------------------------------------
# Table model
# --------------------------------------------------------------------------
# Each row: (label, {symbol: value}, style, highlight)
#   style: 'money' | 'pct' | 'pct_signed' | 'mult' | 'text'
#   highlight: 'high' marks the largest value as the leader; None marks nothing.
# Valuation rows are never highlighted: a lower multiple is not "better".
PERF_LABELS = ["1 month", "3 months", "6 months", "Year to date", "1 year"]


def build_comparison(bundle: dict) -> dict:
    symbols = bundle["symbols"]
    data = bundle["data"]
    sections = []

    def row(label, getter, style, highlight=None):
        values = {}
        for sym in symbols:
            entry = data.get(sym)
            try:
                values[sym] = None if not entry or entry["error"] else getter(entry)
            except Exception:  # noqa: BLE001
                values[sym] = None
        return (label, values, style, highlight)

    growth = {s: revenue_growth_metrics(data[s]["fund"], data[s]["info"]) if not data[s]["error"] else {}
              for s in symbols}
    margins = {s: margin_metrics(data[s]["fund"], data[s]["info"]) if not data[s]["error"] else {}
               for s in symbols}
    perf = {s: price_performance(data[s]["close"]) for s in symbols}

    years = [g.get("cagr_years") for g in growth.values() if g.get("cagr_years")]
    cagr_label = f"Revenue CAGR (~{min(years)}-{max(years)}y, annual)" if years else "Revenue CAGR (annual)"

    sections.append(("REVENUE GROWTH", [
        row("Latest FY, YoY", lambda e: growth[e["symbol"]].get("fy_yoy"), "pct_signed", "high"),
        row(cagr_label, lambda e: growth[e["symbol"]].get("cagr"), "pct_signed", "high"),
        row("Most recent quarter, YoY (Yahoo)", lambda e: growth[e["symbol"]].get("mrq_yoy"), "pct_signed", "high"),
    ]))
    sections.append(("MARGINS", [
        row("Gross margin", lambda e: margins[e["symbol"]].get("gross"), "pct", "high"),
        row("Operating margin", lambda e: margins[e["symbol"]].get("operating"), "pct", "high"),
        row("Net margin", lambda e: margins[e["symbol"]].get("net"), "pct", "high"),
    ]))
    info_row = lambda key: (lambda e: _num(e["info"].get(key)))  # noqa: E731
    sections.append(("VALUATION (no ranking implied)", [
        row("Market cap", info_row("marketCap"), "money"),
        row("Enterprise value", info_row("enterpriseValue"), "money"),
        row("P/E (TTM)", info_row("trailingPE"), "mult"),
        row("Forward P/E", info_row("forwardPE"), "mult"),
        row("PEG", lambda e: _num(e["info"].get("trailingPegRatio")) or _num(e["info"].get("pegRatio")), "mult"),
        row("Price / Sales", info_row("priceToSalesTrailing12Months"), "mult"),
        row("Price / Book", info_row("priceToBook"), "mult"),
        row("EV / EBITDA", info_row("enterpriseToEbitda"), "mult"),
    ]))
    sections.append(("PRICE PERFORMANCE", [
        row(label, (lambda lab: lambda e: perf[e["symbol"]].get(lab))(label), "pct_signed", "high")
        for label in PERF_LABELS
    ]))

    notes = []
    failed = [s for s in symbols if data[s]["error"]]
    if failed:
        notes.append("No data for: " + ", ".join(failed))
    currencies = {data[s]["info"].get("financialCurrency") or data[s]["info"].get("currency")
                  for s in symbols if not data[s]["error"]}
    currencies.discard(None)
    if len(currencies) > 1:
        notes.append("Mixed currencies (" + ", ".join(sorted(currencies)) +
                     "): market cap and enterprise value are not directly comparable.")
    names = {s: (data[s]["info"].get("shortName") or s) for s in symbols}
    return {"symbols": symbols, "names": names, "sections": sections, "notes": notes}


def leaders(values: dict, highlight) -> set:
    """Symbols holding the highest value in a row (empty when nothing to rank)."""
    if highlight != "high":
        return set()
    finite = {s: v for s, v in values.items() if v is not None and np.isfinite(v)}
    if len(finite) < 2:
        return set()
    top = max(finite.values())
    return {s for s, v in finite.items() if v == top}


def format_value(value, style: str) -> str:
    if value is None or (isinstance(value, float) and not np.isfinite(value)):
        return "N/A"
    if style == "pct":
        return f"{value:.1f}%"
    if style == "pct_signed":
        return f"{value:+.1f}%"
    if style == "mult":
        return f"{value:,.1f}x"
    if style == "money":
        mag = abs(value)
        for scale, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M")):
            if mag >= scale:
                return f"{'-' if value < 0 else ''}${mag / scale:,.2f}{suffix}"
        return f"${value:,.0f}"
    return str(value)


# --------------------------------------------------------------------------
# Rebased price chart data
# --------------------------------------------------------------------------
def rebased_frame(closes: dict, period: str) -> pd.DataFrame:
    """Cumulative % return per symbol, all starting at 0 on the same first date.

    Only dates where every symbol has a price are used, so the lines are
    genuinely comparable (a late IPO shortens the window for everyone).
    """
    frames = {}
    for sym, close in closes.items():
        if close is None or close.empty:
            continue
        s = close.copy()
        if s.index.tz is not None:
            s.index = s.index.tz_localize(None)
        s.index = s.index.normalize()
        frames[sym] = s[~s.index.duplicated(keep="last")]
    if not frames:
        return pd.DataFrame()
    joined = pd.concat(frames, axis=1).dropna(how="any")
    if joined.empty:
        return joined
    joined = analysis.slice_period(joined, period)
    if joined.empty:
        return joined
    return (joined / joined.iloc[0] - 1.0) * 100.0
