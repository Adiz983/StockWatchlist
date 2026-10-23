"""Pure calculation helpers: indicators, period slicing, signals, statements.

Nothing in here touches Qt or the network, so it can be unit-tested on its own.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

PERIODS = ["1mo", "3mo", "6mo", "ytd", "1y", "2y", "5y", "max"]
MA_WINDOWS = (20, 50, 200)


# --------------------------------------------------------------------------
# Indicators
# --------------------------------------------------------------------------
def sma(close: pd.Series, window: int) -> pd.Series:
    return close.rolling(window, min_periods=window).mean()


def ema(close: pd.Series, span: int) -> pd.Series:
    return close.ewm(span=span, adjust=False, min_periods=span).mean()


def rsi(close: pd.Series, window: int = 14) -> pd.Series:
    """Wilder's RSI."""
    values = close.to_numpy(dtype=float)
    out = np.full(len(values), np.nan)
    if len(values) <= window:
        return pd.Series(out, index=close.index)
    delta = np.diff(values)
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    # Wilder smoothing, seeded with a simple average of the first `window` moves.
    avg_gain = gain[:window].mean()
    avg_loss = loss[:window].mean()

    def to_rsi(g, l):
        if l == 0:
            return 100.0 if g > 0 else 50.0
        return 100.0 - 100.0 / (1.0 + g / l)

    out[window] = to_rsi(avg_gain, avg_loss)
    for i in range(window, len(delta)):
        avg_gain = (avg_gain * (window - 1) + gain[i]) / window
        avg_loss = (avg_loss * (window - 1) + loss[i]) / window
        out[i + 1] = to_rsi(avg_gain, avg_loss)
    return pd.Series(out, index=close.index)


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    """Return (macd_line, signal_line, histogram)."""
    macd_line = ema(close, fast) - ema(close, slow)
    signal_line = macd_line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    return macd_line, signal_line, macd_line - signal_line


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Return a copy of an OHLCV frame with every indicator column attached.

    Run this on the *full* history and slice afterwards, so a 1-month view
    still has properly warmed-up SMA200 / MACD values.
    """
    out = df.copy()
    close = out["Close"].astype(float)
    for window in MA_WINDOWS:
        out[f"SMA{window}"] = sma(close, window)
    out["RSI"] = rsi(close, 14)
    out["MACD"], out["MACD_SIGNAL"], out["MACD_HIST"] = macd(close)
    return out


# --------------------------------------------------------------------------
# Period slicing / relative performance
# --------------------------------------------------------------------------
def slice_period(df: pd.DataFrame, period: str) -> pd.DataFrame:
    if df is None or df.empty or period == "max":
        return df
    last = df.index[-1]
    if period == "ytd":
        start = pd.Timestamp(year=last.year, month=1, day=1, tz=df.index.tz)
    else:
        n = int("".join(ch for ch in period if ch.isdigit()))
        unit = "".join(ch for ch in period if ch.isalpha())
        offset = pd.DateOffset(months=n) if unit == "mo" else pd.DateOffset(years=n)
        start = last - offset
    return df[df.index >= start]


def align_benchmark(stock_index: pd.Index, bench_close: pd.Series | None) -> pd.Series | None:
    """Reindex benchmark closes onto the stock's dates (forward-filled)."""
    if bench_close is None or bench_close.empty:
        return None
    b = bench_close.copy()
    # Normalise tz so the two indexes are comparable.
    if getattr(stock_index, "tz", None) is not None and b.index.tz is None:
        b.index = b.index.tz_localize(stock_index.tz)
    elif getattr(stock_index, "tz", None) is None and b.index.tz is not None:
        b.index = b.index.tz_localize(None)
    if getattr(stock_index, "tz", None) is not None:
        b.index = b.index.tz_convert(stock_index.tz)
    return b.reindex(stock_index.union(b.index)).ffill().reindex(stock_index)


def rebased_returns(close: pd.Series) -> pd.Series:
    """Cumulative % return since the first valid value in the series."""
    close = close.dropna()
    if close.empty or not close.iloc[0]:
        return close
    return (close / close.iloc[0] - 1.0) * 100.0


def rebased_keep_index(series: pd.Series) -> pd.Series:
    """Like rebased_returns, but keeps the original index (NaN gaps stay put),
    so the values still line up with x positions on a chart."""
    first = series.first_valid_index()
    if first is None:
        return series
    base = series.loc[first]
    if not base:
        return series * np.nan
    return (series / base - 1.0) * 100.0


def trailing_returns(close: pd.Series, bench: pd.Series | None, selected_period: str):
    """Rows of (label, stock %, benchmark %) for the performance table."""
    close = close.dropna()
    if len(close) < 2:
        return []
    windows = [("1 day", 1), ("5 days", 5), ("1 month", 21), ("3 months", 63),
               ("6 months", 126), ("1 year", 252)]
    rows = []

    def one(base_series, base_idx):
        s = base_series.dropna()
        if len(s) <= abs(base_idx) - 1 or not s.iloc[base_idx]:
            return None
        return (float(s.iloc[-1]) / float(s.iloc[base_idx]) - 1.0) * 100.0

    for label, back in windows:
        if len(close) > back:
            stock_ret = one(close, -(back + 1))
            bench_ret = one(bench, -(back + 1)) if bench is not None else None
            rows.append((label, stock_ret, bench_ret))
    # Year to date
    last = close.index[-1]
    ytd_start = pd.Timestamp(year=last.year, month=1, day=1, tz=close.index.tz)
    before = close[close.index < ytd_start]
    if not before.empty and before.iloc[-1]:
        stock_ret = (float(close.iloc[-1]) / float(before.iloc[-1]) - 1.0) * 100.0
        bench_ret = None
        if bench is not None:
            b = bench.dropna()
            b_before = b[b.index < ytd_start]
            if not b_before.empty and b_before.iloc[-1]:
                bench_ret = (float(b.iloc[-1]) / float(b_before.iloc[-1]) - 1.0) * 100.0
        rows.append(("Year to date", stock_ret, bench_ret))
    return rows


# --------------------------------------------------------------------------
# Plain-language signal snapshot (descriptive only, not advice)
# --------------------------------------------------------------------------
def technical_snapshot(df: pd.DataFrame):
    """List of (label, text, tone) where tone is 'up', 'down' or 'flat'."""
    rows = []
    if df is None or df.empty:
        return rows
    last = df.iloc[-1]
    close = last.get("Close")

    r = last.get("RSI")
    if pd.notna(r):
        if r >= 70:
            rows.append(("RSI (14)", f"{r:.1f}  ·  overbought zone", "down"))
        elif r <= 30:
            rows.append(("RSI (14)", f"{r:.1f}  ·  oversold zone", "up"))
        else:
            rows.append(("RSI (14)", f"{r:.1f}  ·  neutral", "flat"))
    else:
        rows.append(("RSI (14)", "N/A (needs 15+ sessions)", "flat"))

    m, s, h = last.get("MACD"), last.get("MACD_SIGNAL"), last.get("MACD_HIST")
    if pd.notna(m) and pd.notna(s):
        state = "above signal  ·  bullish momentum" if h >= 0 else "below signal  ·  bearish momentum"
        # Detect a fresh cross within the last 5 sessions.
        hist = df["MACD_HIST"].dropna().tail(6)
        if len(hist) >= 2 and (np.sign(hist.iloc[:-1]) != np.sign(hist.iloc[-1])).any():
            state += "  ·  recent cross"
        rows.append(("MACD (12,26,9)", f"{m:+.2f} vs {s:+.2f}  ·  {state}", "up" if h >= 0 else "down"))
    else:
        rows.append(("MACD (12,26,9)", "N/A (needs 35+ sessions)", "flat"))

    for window in (50, 200):
        ma = last.get(f"SMA{window}")
        if pd.notna(ma) and close:
            dist = (float(close) / float(ma) - 1.0) * 100.0
            rows.append((f"vs SMA {window}", f"{dist:+.1f}%  ·  {'above' if dist >= 0 else 'below'}",
                         "up" if dist >= 0 else "down"))
        else:
            rows.append((f"vs SMA {window}", f"N/A (needs {window}+ sessions)", "flat"))

    s50, s200 = df["SMA50"].dropna(), df["SMA200"].dropna()
    if not s200.empty:
        a = float(s50.iloc[-1]) if not s50.empty else None
        b = float(s200.iloc[-1])
        if a is not None:
            rows.append(("50 / 200 trend", "SMA50 above SMA200" if a > b else "SMA50 below SMA200",
                         "up" if a > b else "down"))
    return rows


# --------------------------------------------------------------------------
# Financial statements
# --------------------------------------------------------------------------
KEY_ROWS = {
    "income": ["Total Revenue", "Cost Of Revenue", "Gross Profit", "Operating Expense",
               "Operating Income", "EBITDA", "Pretax Income", "Tax Provision",
               "Net Income", "Diluted EPS", "Basic EPS", "Diluted Average Shares"],
    "balance": ["Total Assets", "Current Assets", "Cash And Cash Equivalents",
                "Cash Cash Equivalents And Short Term Investments", "Inventory",
                "Total Liabilities Net Minority Interest", "Current Liabilities",
                "Total Debt", "Long Term Debt", "Net Debt", "Stockholders Equity",
                "Working Capital", "Ordinary Shares Number"],
    "cashflow": ["Operating Cash Flow", "Investing Cash Flow", "Financing Cash Flow",
                 "Capital Expenditure", "Free Cash Flow", "Repurchase Of Capital Stock",
                 "Cash Dividends Paid", "Depreciation And Amortization",
                 "Depreciation Amortization Depletion", "Stock Based Compensation"],
}


def clean_statement(df: pd.DataFrame | None) -> pd.DataFrame:
    """Drop empty rows/columns and sort columns oldest -> newest."""
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return pd.DataFrame()
    out = df.copy()
    out = out.apply(pd.to_numeric, errors="coerce")
    out = out.dropna(how="all").dropna(axis=1, how="all")
    try:
        out = out.reindex(sorted(out.columns), axis=1)
    except TypeError:
        pass
    return out


def find_row(df: pd.DataFrame, *names: str):
    for name in names:
        if name in df.index:
            return df.loc[name]
    return None


def derived_income_rows(df: pd.DataFrame, quarterly: bool):
    """Growth and margin rows computed from a cleaned income statement.

    Returns an ordered list of (label, Series of percentages indexed by column).
    """
    rows = []
    rev = find_row(df, "Total Revenue", "Operating Revenue")
    if rev is None:
        return rows
    growth = rev.pct_change() * 100.0
    rows.append((f"Revenue Growth ({'QoQ' if quarterly else 'YoY'}) %", growth))
    for label, names in (("Gross Margin %", ("Gross Profit",)),
                         ("Operating Margin %", ("Operating Income",)),
                         ("Net Margin %", ("Net Income", "Net Income Common Stockholders"))):
        num = find_row(df, *names)
        if num is not None:
            rows.append((label, num / rev.replace(0, np.nan) * 100.0))
    return rows


def statement_table(df: pd.DataFrame, kind: str, show_all: bool, quarterly: bool):
    """Build display rows for the statement grid.

    Returns (columns, rows). `columns` are Timestamps newest-first; each row is
    (label, values_by_column list, style) with style in
    {'money', 'eps', 'count', 'pct'}.
    """
    if df is None or df.empty:
        return [], []
    cols = list(reversed(list(df.columns)))  # newest first for reading
    rows = []
    if show_all:
        labels = list(df.index)
    else:
        labels = [name for name in KEY_ROWS.get(kind, []) if name in df.index]
        if not labels:
            labels = list(df.index[:15])
    for label in labels:
        style = row_style(label)
        rows.append((label, [df.at[label, c] for c in cols], style))
    if kind == "income":
        for label, series in derived_income_rows(df, quarterly):
            rows.append((label, [series.get(c) for c in cols], "pct"))
    return cols, rows


def row_style(label: str) -> str:
    low = label.lower()
    if "eps" in low:
        return "eps"
    if "shares" in low or "share issued" in low:
        return "count"
    return "money"


def period_label(ts, quarterly: bool) -> str:
    try:
        ts = pd.Timestamp(ts)
    except Exception:
        return str(ts)
    return ts.strftime("%b %Y") if quarterly else ts.strftime("FY %Y-%m")
