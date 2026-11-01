"""Strategy backtester: long/flat strategies on daily prices.

Pure (no Qt, no network) apart from run_from_symbol(), which fetches history.

Modelling rules, stated plainly because they decide how honest the result is:
  * A signal is read from the close of day t and acted on at the close of day
    t+1 (positions are shifted one bar), so a strategy never trades on a price
    it could not have seen yet.
  * Every change of position pays (commission + slippage) basis points on the
    traded notional, entering and exiting each count.
  * Long-or-flat only: no shorting, leverage, margin, taxes, dividends beyond
    what the adjusted price series contains, or market-impact modelling.
  * Everything here is a SIMULATION on past prices. It says what a rule would
    have done in hindsight, not what it will do.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

import analysis

TRADING_DAYS = 252
MIN_BARS = 60

SIMULATION_NOTICE = (
    "SIMULATED RESULTS · HYPOTHETICAL · Generated from historical prices with simplified "
    "assumptions. Not real trading, not a forecast, and not investment advice. Real-world "
    "results differ (liquidity, taxes, fees, slippage, behaviour) and past performance does "
    "not predict future returns."
)

# name -> (parameter labels, default values, description)
STRATEGIES = {
    "Buy & hold": ((), (), "Always invested. The reference every other rule has to beat."),
    "SMA crossover": (("Fast SMA", "Slow SMA"), (50, 200),
                      "Long while the fast average is above the slow average, otherwise flat."),
    "RSI mean reversion": (("RSI window", "Buy below", "Exit above"), (14, 30, 55),
                           "Buy when RSI drops under the buy level, exit when it rises above the exit level."),
    "MACD signal cross": (("Fast EMA", "Slow EMA", "Signal"), (12, 26, 9),
                          "Long while the MACD line is above its signal line, otherwise flat."),
    "Breakout (N-day high)": (("Entry lookback", "Exit lookback"), (55, 20),
                              "Buy a new N-day closing high, exit on a new M-day closing low."),
}


def price_column(df: pd.DataFrame) -> pd.Series:
    """Dividend- and split-adjusted close when available, so returns are total returns."""
    col = "Adj Close" if "Adj Close" in df.columns else "Close"
    return df[col].astype(float).dropna()


# --------------------------------------------------------------------------
# Signals (desired position at the close of each day: 1 = long, 0 = flat)
# --------------------------------------------------------------------------
def _stateful(entry: pd.Series, exit_: pd.Series) -> pd.Series:
    """Hold from an entry signal until an exit signal (no whipsaw re-entries mid-trade)."""
    state, out = 0, []
    for e, x in zip(entry.to_numpy(dtype=bool), exit_.to_numpy(dtype=bool)):
        if state == 0 and e:
            state = 1
        elif state == 1 and x:
            state = 0
        out.append(state)
    return pd.Series(out, index=entry.index, dtype=float)


def desired_position(price: pd.Series, strategy: str, params: tuple) -> pd.Series:
    if strategy == "Buy & hold":
        return pd.Series(1.0, index=price.index)
    if strategy == "SMA crossover":
        fast, slow = int(params[0]), int(params[1])
        if fast >= slow:
            raise ValueError("Fast SMA must be shorter than slow SMA.")
        f, s = analysis.sma(price, fast), analysis.sma(price, slow)
        return (f > s).astype(float).where(f.notna() & s.notna(), 0.0)
    if strategy == "RSI mean reversion":
        window, buy, sell = int(params[0]), float(params[1]), float(params[2])
        if buy >= sell:
            raise ValueError("The RSI buy level must be below the exit level.")
        r = analysis.rsi(price, window)
        return _stateful((r < buy).fillna(False), (r > sell).fillna(False))
    if strategy == "MACD signal cross":
        fast, slow, sig = (int(p) for p in params)
        if fast >= slow:
            raise ValueError("Fast EMA must be shorter than slow EMA.")
        line, signal, _ = analysis.macd(price, fast, slow, sig)
        return (line > signal).astype(float).where(line.notna() & signal.notna(), 0.0)
    if strategy == "Breakout (N-day high)":
        n_in, n_out = int(params[0]), int(params[1])
        high = price.rolling(n_in, min_periods=n_in).max().shift(1)   # prior N days only
        low = price.rolling(n_out, min_periods=n_out).min().shift(1)
        return _stateful((price > high).fillna(False), (price < low).fillna(False))
    raise ValueError(f"Unknown strategy: {strategy}")


# --------------------------------------------------------------------------
# Metrics
# --------------------------------------------------------------------------
def max_drawdown(equity: pd.Series) -> float:
    """Worst peak-to-trough fall, as a negative percent."""
    if equity.empty:
        return float("nan")
    return float(((equity / equity.cummax()) - 1.0).min() * 100.0)


def drawdown_series(equity: pd.Series) -> pd.Series:
    return (equity / equity.cummax() - 1.0) * 100.0


def performance_stats(returns: pd.Series) -> dict:
    """Headline numbers for a daily return series (fractions, not percent)."""
    r = returns.dropna()
    if len(r) < 2:
        return {k: float("nan") for k in ("total", "cagr", "vol", "sharpe", "max_dd")}
    equity = (1.0 + r).cumprod()
    years = max((r.index[-1] - r.index[0]).days / 365.25, 1e-9)
    total = float(equity.iloc[-1] - 1.0)
    cagr = float(equity.iloc[-1] ** (1.0 / years) - 1.0) if equity.iloc[-1] > 0 else -1.0
    vol = float(r.std(ddof=1) * np.sqrt(TRADING_DAYS))
    sharpe = float(r.mean() / r.std(ddof=1) * np.sqrt(TRADING_DAYS)) if r.std(ddof=1) > 0 else float("nan")
    return {"total": total * 100.0, "cagr": cagr * 100.0, "vol": vol * 100.0,
            "sharpe": sharpe, "max_dd": max_drawdown(equity)}


@dataclass
class BacktestResult:
    strategy: str
    params: tuple
    symbol: str
    benchmark: str | None
    start: pd.Timestamp
    end: pd.Timestamp
    cost_bps: float
    slippage_bps: float
    capital: float
    equity: pd.Series                 # strategy, after costs
    equity_gross: pd.Series           # strategy, before costs
    equity_hold: pd.Series            # buy & hold of the same symbol
    equity_bench: pd.Series | None    # buy & hold of the benchmark
    position: pd.Series               # executed position (0/1) each day
    stats: dict = field(default_factory=dict)        # net strategy
    stats_gross: dict = field(default_factory=dict)
    stats_hold: dict = field(default_factory=dict)
    stats_bench: dict = field(default_factory=dict)
    trades: list = field(default_factory=list)
    summary: dict = field(default_factory=dict)      # exposure, trade count, win rate, cost drag
    split: dict = field(default_factory=dict)        # in-sample vs out-of-sample stats
    warnings: list = field(default_factory=list)


def extract_trades(price: pd.Series, position: pd.Series, round_trip_cost: float) -> list:
    """Round trips as dicts. The return is net of the entry+exit cost fraction."""
    trades, entry_date, entry_px = [], None, None
    pos = position.to_numpy()
    for i in range(len(pos)):
        prev = pos[i - 1] if i else 0.0
        if pos[i] > 0 and prev == 0:
            entry_date, entry_px = position.index[i], float(price.iloc[i])
        elif pos[i] == 0 and prev > 0 and entry_date is not None:
            exit_px = float(price.iloc[i])
            trades.append({"entry": entry_date, "exit": position.index[i], "entry_px": entry_px,
                           "exit_px": exit_px, "open": False,
                           "ret": ((exit_px / entry_px) * (1 - round_trip_cost) - 1.0) * 100.0})
            entry_date = None
    if entry_date is not None:
        exit_px = float(price.iloc[-1])
        trades.append({"entry": entry_date, "exit": position.index[-1], "entry_px": entry_px,
                       "exit_px": exit_px, "open": True,
                       "ret": ((exit_px / entry_px) * (1 - round_trip_cost) - 1.0) * 100.0})
    return trades


def _to_ts(value, tz):
    """Timestamp comparable with an index of the given tz (naive or aware input)."""
    ts = pd.Timestamp(value)
    if tz is not None and ts.tz is None:
        return ts.tz_localize(tz)
    if tz is None and ts.tz is not None:
        return ts.tz_localize(None)
    return ts


def run_backtest(df: pd.DataFrame, strategy: str, params: tuple, *, symbol: str = "",
                 start=None, end=None, cost_bps: float = 5.0, slippage_bps: float = 5.0,
                 capital: float = 10_000.0, bench_price: pd.Series | None = None,
                 benchmark: str | None = None, oos_fraction: float = 0.3) -> BacktestResult:
    """Run one backtest on the full history, then report on [start, end].

    Signals are computed on the *full* history first so a late start date still
    has warmed-up indicators; only the evaluation window is trimmed.
    """
    full = price_column(df)
    if len(full) < MIN_BARS:
        raise ValueError(f"Need at least {MIN_BARS} daily bars; {symbol or 'this symbol'} has {len(full)}.")
    if cost_bps < 0 or slippage_bps < 0:
        raise ValueError("Costs cannot be negative.")
    if capital <= 0:
        raise ValueError("Starting capital must be positive.")

    desired = desired_position(full, strategy, params)
    executed = desired.shift(1).fillna(0.0)                 # act one bar after the signal
    ret = full.pct_change().fillna(0.0)

    window = full.index
    if start is not None:
        window = window[window >= _to_ts(start, full.index.tz)]
    if end is not None:
        window = window[window <= _to_ts(end, full.index.tz)]
    if len(window) < MIN_BARS:
        raise ValueError(f"Only {len(window)} bars fall inside the chosen window; need {MIN_BARS}+.")

    price = full.loc[window]
    position = executed.loc[window].copy()
    position.iloc[0] = 0.0                                  # start flat inside the window
    r = ret.loc[window].copy()
    r.iloc[0] = 0.0

    one_way = (cost_bps + slippage_bps) / 1e4
    turnover = position.diff().abs().fillna(position.abs())
    gross = position * r
    net = gross - turnover * one_way

    def curve(daily):
        return capital * (1.0 + daily).cumprod()

    equity, equity_gross = curve(net), curve(gross)
    equity_hold = curve(r)
    equity_bench, stats_bench = None, {}
    if bench_price is not None and not bench_price.empty:
        b = bench_price.astype(float).dropna()
        if b.index.tz is None and price.index.tz is not None:
            b.index = b.index.tz_localize(price.index.tz)
        elif b.index.tz is not None and price.index.tz is None:
            b.index = b.index.tz_localize(None)
        b = b.reindex(price.index.union(b.index)).ffill().reindex(price.index)
        if b.notna().sum() >= MIN_BARS:
            br = b.pct_change().fillna(0.0)
            br.iloc[0] = 0.0
            equity_bench = curve(br)
            stats_bench = performance_stats(br)

    trades = extract_trades(price, position, 2 * one_way)
    closed = [t for t in trades if not t["open"]]
    wins = sum(1 for t in closed if t["ret"] > 0)
    stats, stats_gross = performance_stats(net), performance_stats(gross)
    summary = {
        "exposure": float(position.mean() * 100.0),
        "trades": len(trades),
        "closed_trades": len(closed),
        "win_rate": (wins / len(closed) * 100.0) if closed else float("nan"),
        "avg_trade": float(np.mean([t["ret"] for t in closed])) if closed else float("nan"),
        "cost_drag": stats_gross["total"] - stats["total"],
        "final_value": float(equity.iloc[-1]),
    }

    split = {}
    cut = int(len(net) * (1.0 - oos_fraction))
    if 0.0 < oos_fraction < 1.0 and cut >= MIN_BARS // 2 and len(net) - cut >= MIN_BARS // 2:
        split = {"in_sample": performance_stats(net.iloc[:cut]),
                 "out_of_sample": performance_stats(net.iloc[cut:]),
                 "split_date": net.index[cut]}

    warnings = []
    if strategy != "Buy & hold" and summary["trades"] == 0:
        warnings.append("The rule never traded in this window, so the result equals sitting in cash.")
    elif strategy != "Buy & hold" and summary["closed_trades"] < 10:
        warnings.append(f"Only {summary['closed_trades']} completed trades: far too few to say "
                        "whether the rule has any real edge.")
    warnings.append("Parameters were chosen with the benefit of hindsight on this same data. "
                    "Compare in-sample vs out-of-sample before trusting any number.")

    return BacktestResult(
        strategy=strategy, params=tuple(params), symbol=symbol, benchmark=benchmark,
        start=window[0], end=window[-1], cost_bps=cost_bps, slippage_bps=slippage_bps,
        capital=capital, equity=equity, equity_gross=equity_gross, equity_hold=equity_hold,
        equity_bench=equity_bench, position=position, stats=stats, stats_gross=stats_gross,
        stats_hold=performance_stats(r), stats_bench=stats_bench, trades=trades,
        summary=summary, split=split, warnings=warnings)


def run_from_symbol(symbol: str, strategy: str, params: tuple, years: int | None,
                    benchmark: str, cost_bps: float, slippage_bps: float, capital: float):
    """Blocking helper for the UI worker thread: fetch prices, then backtest."""
    import data_service as ds

    df = ds.get_history(symbol)
    start = None
    if years:
        start = df.index[-1] - pd.DateOffset(years=years)
    bench = None
    if benchmark and benchmark != symbol:
        try:
            hist = ds.get_history(benchmark)
            bench = price_column(hist)
        except Exception:  # noqa: BLE001 - benchmark is optional
            bench = None
    return run_backtest(df, strategy, params, symbol=symbol, start=start, cost_bps=cost_bps,
                        slippage_bps=slippage_bps, capital=capital, bench_price=bench,
                        benchmark=benchmark)
