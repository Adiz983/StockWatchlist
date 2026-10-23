"""Data access for the terminal: quotes, price history, fundamentals.

Qt-free on purpose. Every function here blocks on the network, so the UI calls
them from worker threads (see Worker in stockwatchlist_terminal.py).
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

import pandas as pd
import yfinance as yf

import analysis

CACHE_TTL_SECONDS = 15 * 60
_cache: dict = {}
_cache_lock = threading.Lock()


def _cached(key, loader, force=False, ttl=CACHE_TTL_SECONDS):
    now = time.time()
    if not force:
        with _cache_lock:
            hit = _cache.get(key)
            if hit and now - hit[0] < ttl:
                return hit[1]
    value = loader()
    with _cache_lock:
        _cache[key] = (time.time(), value)
    return value


def clear_cache(symbol: str | None = None):
    with _cache_lock:
        if symbol is None:
            _cache.clear()
        else:
            for key in [k for k in _cache if k[1] == symbol]:
                del _cache[key]


# --------------------------------------------------------------------------
# Watchlist quotes
# --------------------------------------------------------------------------
@dataclass
class Quote:
    symbol: str
    price: float | None = None
    prev_close: float | None = None
    change: float | None = None
    pct: float | None = None
    error: str | None = None
    fetched_at: float = field(default_factory=time.time)


def _finite(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None


def fetch_quote(symbol: str) -> Quote:
    """One symbol -> its own price, daily change and % change.

    Tries yfinance's lightweight fast_info first and falls back to the last two
    daily closes, so one flaky endpoint doesn't leave the row blank.
    """
    symbol = symbol.strip().upper()
    price = prev = None
    last_error = None
    ticker = yf.Ticker(symbol)

    try:
        fast = ticker.fast_info
        price = _finite(getattr(fast, "last_price", None))
        prev = _finite(getattr(fast, "previous_close", None))
    except Exception as error:  # noqa: BLE001 - any failure -> fall back
        last_error = error

    if price is None or prev is None:
        try:
            closes = ticker.history(period="7d", interval="1d", auto_adjust=False)["Close"].dropna()
            if len(closes) >= 1 and price is None:
                price = _finite(closes.iloc[-1])
            if len(closes) >= 2 and prev is None:
                prev = _finite(closes.iloc[-2])
        except Exception as error:  # noqa: BLE001
            last_error = error

    if price is None:
        return Quote(symbol, error=str(last_error) if last_error else "No quote returned")
    change = pct = None
    if prev:
        change = price - prev
        pct = change / prev * 100.0
    return Quote(symbol, price=price, prev_close=prev, change=change, pct=pct)


# --------------------------------------------------------------------------
# Price history + info
# --------------------------------------------------------------------------
def get_history(symbol: str, force=False) -> pd.DataFrame:
    """Full daily history. Fetched once and sliced per period in the UI, so
    changing the period is instant and indicators have proper warm-up data."""

    def load():
        df = yf.Ticker(symbol).history(period="max", interval="1d", auto_adjust=False)
        if df is None or df.empty:
            raise ValueError(f"No historical price data was returned for {symbol}.")
        return df.dropna(subset=["Close"])

    return _cached(("history", symbol), load, force)


def get_info(symbol: str, force=False) -> dict:
    def load():
        try:
            return yf.Ticker(symbol).info or {}
        except Exception:  # noqa: BLE001 - info is optional; history is what matters
            return {}

    return _cached(("info", symbol), load, force)


def load_research_bundle(symbol: str, benchmark: str, force=False) -> dict:
    """Everything the chart workspace needs for one symbol."""
    history = get_history(symbol, force)
    info = get_info(symbol, force)
    bench_close = None
    bench_error = None
    if benchmark and benchmark != symbol:
        try:
            bench_close = get_history(benchmark, force)["Close"]
        except Exception as error:  # noqa: BLE001
            bench_error = str(error)
    return {
        "symbol": symbol,
        "benchmark": benchmark,
        "info": info,
        "frame": analysis.add_indicators(history),
        "bench_close": bench_close,
        "bench_error": bench_error,
    }


# --------------------------------------------------------------------------
# Fundamentals
# --------------------------------------------------------------------------
_STATEMENT_ATTRS = {
    "annual": {"income": "income_stmt", "balance": "balance_sheet", "cashflow": "cashflow"},
    "quarterly": {"income": "quarterly_income_stmt", "balance": "quarterly_balance_sheet",
                  "cashflow": "quarterly_cashflow"},
}


def load_fundamentals(symbol: str, force=False) -> dict:
    """Annual + quarterly statements, cleaned and sorted oldest -> newest.

    Missing statements come back as empty frames rather than raising, because
    ETFs, ADRs and recent IPOs often lack some of them.
    """

    def load():
        ticker = yf.Ticker(symbol)
        out = {"annual": {}, "quarterly": {}, "errors": []}
        for freq, mapping in _STATEMENT_ATTRS.items():
            for kind, attr in mapping.items():
                try:
                    out[freq][kind] = analysis.clean_statement(getattr(ticker, attr))
                except Exception as error:  # noqa: BLE001
                    out[freq][kind] = pd.DataFrame()
                    out["errors"].append(f"{freq} {kind}: {error}")
        return out

    return _cached(("fundamentals", symbol), load, force)


def dividend_yield_percent(info: dict) -> float | None:
    """Dividend yield in percent, computed so it doesn't depend on whether
    Yahoo currently reports `dividendYield` as a fraction or as a percent."""
    rate = _finite(info.get("dividendRate"))
    price = _finite(info.get("currentPrice")) or _finite(info.get("regularMarketPrice"))
    if rate is not None and price:
        return rate / price * 100.0
    trailing = _finite(info.get("trailingAnnualDividendYield"))  # always a fraction
    if trailing is not None:
        return trailing * 100.0
    return None
