
"""Data access for the terminal: quotes, price history, fundamentals.

Qt-free on purpose. Every function here blocks on the network, so the UI
calls them from worker threads (see Worker in stockwatchlist_terminal.py).
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


# ---------------------------------------------------------
# CACHE
# ---------------------------------------------------------

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


# ---------------------------------------------------------
# WATCHLIST QUOTES
# ---------------------------------------------------------

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
    """Fetch one symbol's price, daily change and percentage change."""

    symbol = symbol.strip().upper()

    price = prev = None
    last_error = None

    ticker = yf.Ticker(symbol)

    try:
        fast = ticker.fast_info

        price = _finite(getattr(fast, "last_price", None))
        prev = _finite(getattr(fast, "previous_close", None))

    except Exception as error:
        last_error = error

    # Fallback to historical closing prices.
    if price is None or prev is None:
        try:
            closes = ticker.history(
                period="7d",
                interval="1d",
                auto_adjust=False
            )["Close"].dropna()

            if len(closes) >= 1 and price is None:
                price = _finite(closes.iloc[-1])

            if len(closes) >= 2 and prev is None:
                prev = _finite(closes.iloc[-2])

        except Exception as error:
            last_error = error

    if price is None:
        return Quote(
            symbol,
            error=str(last_error) if last_error else "No quote returned"
        )

    change = None
    pct = None

    if prev:
        change = price - prev
        pct = change / prev * 100.0

    return Quote(
        symbol,
        price=price,
        prev_close=prev,
        change=change,
        pct=pct
    )


# ---------------------------------------------------------
# PRICE HISTORY
# ---------------------------------------------------------

def get_history(symbol: str, force=False) -> pd.DataFrame:
    """Fetch full daily price history for a stock.

    History is cached so changing chart periods does not require
    another network request.
    """

    symbol = symbol.strip().upper()

    def load():
        df = yf.Ticker(symbol).history(
            period="max",
            interval="1d",
            auto_adjust=False
        )

        if df is None or df.empty:
            raise ValueError(
                f"No historical price data was returned for {symbol}."
            )

        return df.dropna(subset=["Close"])

    return _cached(("history", symbol), load, force)


# ---------------------------------------------------------
# COMPANY INFORMATION
# ---------------------------------------------------------

def get_info(symbol: str, force=False) -> dict:
    """Retrieve company information from Yahoo Finance."""

    symbol = symbol.strip().upper()

    def load():
        try:
            return yf.Ticker(symbol).info or {}
        except Exception:
            return {}

    return _cached(("info", symbol), load, force)


# ---------------------------------------------------------
# RESEARCH BUNDLE
# ---------------------------------------------------------

def load_research_bundle(
    symbol: str,
    benchmark: str,
    force=False
) -> dict:
    """Load everything needed for the chart workspace."""

    symbol = symbol.strip().upper()
    benchmark = benchmark.strip().upper() if benchmark else ""

    history = get_history(symbol, force)
    info = get_info(symbol, force)

    bench_close = None
    bench_error = None

    if benchmark and benchmark != symbol:
        try:
            bench_close = get_history(benchmark, force)["Close"]
        except Exception as error:
            bench_error = str(error)

    return {
        "symbol": symbol,
        "benchmark": benchmark,
        "info": info,
        "frame": analysis.add_indicators(history),
        "bench_close": bench_close,
        "bench_error": bench_error,
    }


# ---------------------------------------------------------
# FINANCIAL STATEMENTS
# ---------------------------------------------------------

_STATEMENT_ATTRS = {
    "annual": {
        "income": "income_stmt",
        "balance": "balance_sheet",
        "cashflow": "cashflow"
    },

    "quarterly": {
        "income": "quarterly_income_stmt",
        "balance": "quarterly_balance_sheet",
        "cashflow": "quarterly_cashflow"
    }
}


def load_fundamentals(symbol: str, force=False) -> dict:
    """Load annual and quarterly financial statements.

    Missing statements return empty DataFrames instead of
    crashing the application.
    """

    symbol = symbol.strip().upper()

    def load():
        ticker = yf.Ticker(symbol)

        out = {
            "annual": {},
            "quarterly": {},
            "errors": []
        }

        for freq, mapping in _STATEMENT_ATTRS.items():

            for kind, attr in mapping.items():

                try:
                    statement = getattr(ticker, attr)

                    out[freq][kind] = analysis.clean_statement(
                        statement
                    )

                except Exception as error:

                    out[freq][kind] = pd.DataFrame()

                    out["errors"].append(
                        f"{freq} {kind}: {error}"
                    )

        return out

    return _cached(("fundamentals", symbol), load, force)


# ---------------------------------------------------------
# DIVIDEND YIELD
# ---------------------------------------------------------

def dividend_yield_percent(info: dict) -> float | None:
    """Calculate dividend yield as a percentage."""

    rate = _finite(info.get("dividendRate"))

    price = (
        _finite(info.get("currentPrice"))
        or _finite(info.get("regularMarketPrice"))
    )

    if rate is not None and price:
        return rate / price * 100.0

    trailing = _finite(
        info.get("trailingAnnualDividendYield")
    )

    if trailing is not None:
        return trailing * 100.0

    return None
