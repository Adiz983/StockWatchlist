"""Qt pages for the Compare, Backtest and AI Assistant workspaces.

These classes never import stockwatchlist_terminal (that would be circular and
re-run the app module). Instead the main window hands over `host` (for
start_job, pools, current ticker, loaded data) and `ui` (a namespace of the
terminal's existing widgets, colours and helpers).
"""
from __future__ import annotations

import html

import numpy as np
import pandas as pd
import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDoubleSpinBox, QHBoxLayout, QHeaderView, QInputDialog, QLabel,
    QLineEdit, QListWidget, QMessageBox, QPlainTextEdit, QPushButton, QSplitter,
    QTableWidget, QTableWidgetItem, QTextBrowser, QVBoxLayout, QWidget,
)

import backtest
import compare
import notes_store
import research_assistant as ra


def _palette(ui):
    return [ui.AMBER, ui.BLUE, ui.GREEN, ui.PURPLE, ui.RED]


def _cell(text, ui, color=None, bold=False, align_right=True):
    item = QTableWidgetItem(text)
    flags = Qt.AlignmentFlag.AlignVCenter | (Qt.AlignmentFlag.AlignRight if align_right else Qt.AlignmentFlag.AlignLeft)
    item.setTextAlignment(flags)
    if color:
        item.setForeground(QColor(color))
    if bold:
        font = item.font()
        font.setBold(True)
        item.setFont(font)
    return item


def _plain_table(columns):
    table = QTableWidget(0, len(columns))
    table.setHorizontalHeaderLabels(columns)
    table.verticalHeader().setVisible(False)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setShowGrid(False)
    table.setAlternatingRowColors(True)
    return table


def _date_chart(ui):
    axis = ui.IndexDateAxis(orientation="bottom")
    chart = pg.PlotWidget(axisItems={"bottom": axis})
    ui.style_chart(chart)
    ui.add_legend(chart)
    return chart, axis


# ==========================================================================
# 8. STOCK COMPARISON
# ==========================================================================
class ComparePage(QWidget):
    def __init__(self, host, ui):
        super().__init__()
        self.host, self.ui = host, ui
        self.serial = 0
        self.bundle = None
        self.closes = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        bar.addWidget(host.muted_label("SYMBOLS (2-5)"))
        self.symbols_edit = QLineEdit()
        self.symbols_edit.setPlaceholderText("e.g. AAPL, MSFT, GOOGL   [Enter to compare]")
        self.symbols_edit.setMinimumWidth(320)
        self.symbols_edit.returnPressed.connect(self.run)
        bar.addWidget(self.symbols_edit)
        add = QPushButton("+ CURRENT")
        add.setToolTip("Add the ticker loaded in the workspace")
        add.clicked.connect(self.add_current)
        bar.addWidget(add)
        watch = QPushButton("FROM WATCHLIST")
        watch.setToolTip("Fill with the first five watchlist symbols")
        watch.clicked.connect(self.fill_from_watchlist)
        bar.addWidget(watch)
        go = QPushButton("COMPARE")
        go.setObjectName("accent")
        go.clicked.connect(self.run)
        bar.addWidget(go)
        bar.addWidget(host.muted_label("CHART PERIOD"))
        self.period = QComboBox()
        self.period.addItems(compare.COMPARE_PERIODS)
        self.period.setCurrentText("1y")
        self.period.currentTextChanged.connect(self.render_chart)
        bar.addWidget(self.period)
        bar.addStretch()
        self.status = QLabel("Enter two to five tickers.")
        self.status.setObjectName("muted")
        bar.addWidget(self.status)
        layout.addLayout(bar)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)
        table_panel = ui.TerminalPanel("SIDE-BY-SIDE METRICS")
        self.table = _plain_table(["METRIC"])
        table_panel.layout_box.addWidget(self.table, 1)
        self.notes = QLabel("")
        self.notes.setObjectName("muted")
        self.notes.setWordWrap(True)
        table_panel.layout_box.addWidget(self.notes)
        legend = QLabel(f'<span style="color:{ui.AMBER}">■ bold amber</span> = highest value in the row '
                        "(growth, margins, performance only). Valuation multiples are shown without a ranking: "
                        "a lower multiple is not automatically better. Descriptive data, not a recommendation.")
        legend.setObjectName("muted")
        legend.setWordWrap(True)
        table_panel.layout_box.addWidget(legend)
        split.addWidget(table_panel)

        chart_panel = ui.TerminalPanel("PRICE PERFORMANCE · REBASED TO 0% (PRICE ONLY, EXCLUDES DIVIDENDS)")
        self.chart, self.axis = _date_chart(ui)
        chart_panel.layout_box.addWidget(self.chart, 1)
        split.addWidget(chart_panel)
        split.setSizes([700, 560])
        layout.addWidget(split, 1)

    # -- inputs ----------------------------------------------------------
    def add_current(self):
        current = compare.parse_symbols(self.symbols_edit.text())
        if self.host.current_ticker not in current:
            current.append(self.host.current_ticker)
        self.symbols_edit.setText(", ".join(current[:compare.MAX_SYMBOLS]))

    def fill_from_watchlist(self):
        self.symbols_edit.setText(", ".join(self.host.watchlist[:compare.MAX_SYMBOLS]))

    def prefill(self):
        """Called when the page is first shown: start with the loaded ticker."""
        if not self.symbols_edit.text().strip():
            self.symbols_edit.setText(self.host.current_ticker)

    def run(self, force=False):
        symbols = compare.parse_symbols(self.symbols_edit.text())
        if not (compare.MIN_SYMBOLS <= len(symbols) <= compare.MAX_SYMBOLS):
            QMessageBox.information(self, "Compare", f"Enter between {compare.MIN_SYMBOLS} and "
                                    f"{compare.MAX_SYMBOLS} different ticker symbols.")
            return
        self.symbols_edit.setText(", ".join(symbols))
        self.status.setText("LOADING " + ", ".join(symbols) + "…")
        self.serial = self.host.start_job(self.host.feature_pool, "compare", ",".join(symbols),
                                          compare.load_compare_bundle, symbols, bool(force))

    # -- results ---------------------------------------------------------
    def handle_result(self, serial, key, bundle):
        if serial != self.serial:
            return
        self.bundle = bundle
        self.closes = {s: bundle["data"][s]["close"] for s in bundle["symbols"]}
        model = compare.build_comparison(bundle)
        self.render_table(model)
        self.render_chart()
        bad = [s for s in bundle["symbols"] if bundle["data"][s]["error"]]
        self.status.setText("COMPARED " + ", ".join(bundle["symbols"]) + (f" · {len(bad)} FAILED" if bad else ""))

    def handle_error(self, serial, key, message):
        if serial == self.serial:
            self.status.setText(f"COMPARISON FAILED: {message}"[:140])

    def render_table(self, model):
        ui, symbols = self.ui, model["symbols"]
        colors = dict(zip(symbols, _palette(ui)))
        self.table.clear()
        self.table.clearSpans()
        self.table.setColumnCount(1 + len(symbols))
        self.table.setHorizontalHeaderLabels(["METRIC"] + symbols)
        for col, sym in enumerate(symbols, start=1):
            header = self.table.horizontalHeaderItem(col)
            header.setToolTip(model["names"].get(sym, sym))
            header.setForeground(QColor(colors[sym]))
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in range(1, 1 + len(symbols)):
            hh.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        total = sum(1 + len(rows) for _, rows in model["sections"])
        self.table.setRowCount(total)
        r = 0
        for title, rows in model["sections"]:
            head = _cell(title, ui, ui.AMBER, True, align_right=False)
            self.table.setItem(r, 0, head)
            self.table.setSpan(r, 0, 1, 1 + len(symbols))
            r += 1
            for label, values, style, highlight in rows:
                top = compare.leaders(values, highlight)
                self.table.setItem(r, 0, _cell(label, ui, ui.MUTED, align_right=False))
                for col, sym in enumerate(symbols, start=1):
                    text = compare.format_value(values.get(sym), style)
                    lead = sym in top
                    self.table.setItem(r, col, _cell(text, ui, ui.AMBER if lead else (ui.MUTED if text == "N/A" else None), lead))
                r += 1
        self.notes.setText("  ".join(model["notes"]))

    def render_chart(self, *_):
        ui = self.ui
        self.chart.clear()
        if not self.closes:
            return
        reb = compare.rebased_frame(self.closes, self.period.currentText())
        if reb.empty or len(reb) < 2:
            ui.show_no_data(self.chart, "Not enough overlapping price history")
            return
        self.axis.set_dates(reb.index)
        x = np.arange(len(reb))
        colors = dict(zip(self.bundle["symbols"], _palette(ui)))
        for sym in reb.columns:
            self.chart.plot(x, reb[sym].to_numpy(), pen=pg.mkPen(colors.get(sym, ui.TEXT), width=2), name=sym)
        self.chart.addItem(pg.InfiniteLine(0, angle=0, pen=pg.mkPen(ui.BORDER, style=Qt.PenStyle.DashLine)))
        self.chart.setLabel("left", "% since " + reb.index[0].strftime("%b %d, %Y"))
        self.chart.setXRange(0, len(reb) - 1, padding=0.02)
        self.chart.enableAutoRange(axis="y")


# ==========================================================================
# 9. STRATEGY BACKTESTER
# ==========================================================================
class BacktestPage(QWidget):
    YEARS = [("2 years", 2), ("5 years", 5), ("10 years", 10), ("MAX", None)]

    def __init__(self, host, ui):
        super().__init__()
        self.host, self.ui = host, ui
        self.serial = 0
        self.result = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        # permanent banner: simulated results are always labelled as such
        banner = QLabel("◆ " + backtest.SIMULATION_NOTICE)
        banner.setWordWrap(True)
        banner.setStyleSheet(f"color: {ui.AMBER}; background: #1B1609; border: 1px solid {ui.AMBER}; padding: 6px;")
        layout.addWidget(banner)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        bar.addWidget(host.muted_label("SYMBOL"))
        self.symbol = QLineEdit()
        self.symbol.setFixedWidth(80)
        bar.addWidget(self.symbol)
        bar.addWidget(host.muted_label("STRATEGY"))
        self.strategy = QComboBox()
        self.strategy.addItems(list(backtest.STRATEGIES))
        self.strategy.setCurrentText("SMA crossover")
        self.strategy.currentTextChanged.connect(self.on_strategy_changed)
        bar.addWidget(self.strategy)
        self.param_labels, self.param_boxes = [], []
        for _ in range(3):
            label = host.muted_label("")
            box = QDoubleSpinBox()
            box.setDecimals(0)
            box.setRange(1, 1000)
            box.setFixedWidth(64)
            self.param_labels.append(label)
            self.param_boxes.append(box)
            bar.addWidget(label)
            bar.addWidget(box)
        bar.addStretch()
        layout.addLayout(bar)

        bar2 = QHBoxLayout()
        bar2.setSpacing(8)
        bar2.addWidget(host.muted_label("WINDOW"))
        self.window = QComboBox()
        for label, _ in self.YEARS:
            self.window.addItem(label)
        self.window.setCurrentText("10 years")
        bar2.addWidget(self.window)
        bar2.addWidget(host.muted_label("COMMISSION bps"))
        self.cost = QDoubleSpinBox()
        self.cost.setRange(0, 500)
        self.cost.setDecimals(1)
        self.cost.setValue(5.0)
        self.cost.setToolTip("Per side, on traded value")
        bar2.addWidget(self.cost)
        bar2.addWidget(host.muted_label("SLIPPAGE bps"))
        self.slip = QDoubleSpinBox()
        self.slip.setRange(0, 500)
        self.slip.setDecimals(1)
        self.slip.setValue(5.0)
        self.slip.setToolTip("Per side: the gap between the price you see and the price you get")
        bar2.addWidget(self.slip)
        bar2.addWidget(host.muted_label("CAPITAL $"))
        self.capital = QDoubleSpinBox()
        self.capital.setRange(100, 1e9)
        self.capital.setDecimals(0)
        self.capital.setValue(10_000)
        self.capital.setGroupSeparatorShown(True)
        bar2.addWidget(self.capital)
        bar2.addWidget(host.muted_label("BENCHMARK"))
        self.bench = QLineEdit(host.benchmark)
        self.bench.setFixedWidth(70)
        bar2.addWidget(self.bench)
        run = QPushButton("RUN SIMULATION")
        run.setObjectName("accent")
        run.clicked.connect(self.run)
        bar2.addWidget(run)
        bar2.addStretch()
        self.status = QLabel("")
        self.status.setObjectName("muted")
        bar2.addWidget(self.status)
        layout.addLayout(bar2)

        self.description = QLabel("")
        self.description.setObjectName("muted")
        self.description.setWordWrap(True)
        layout.addWidget(self.description)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)

        left = QWidget()
        left_l = QVBoxLayout(left)
        left_l.setContentsMargins(0, 0, 0, 0)
        left_l.setSpacing(7)
        stats_panel = ui.TerminalPanel("SIMULATED PERFORMANCE")
        self.stats_table = _plain_table(["", "STRATEGY (NET)", "BEFORE COSTS", "BUY & HOLD", "BENCHMARK"])
        self.stats_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        stats_panel.layout_box.addWidget(self.stats_table, 1)
        self.warn_label = QLabel("")
        self.warn_label.setWordWrap(True)
        self.warn_label.setStyleSheet(f"color: {ui.AMBER};")
        stats_panel.layout_box.addWidget(self.warn_label)
        left_l.addWidget(stats_panel, 3)
        oos_panel = ui.TerminalPanel("IN-SAMPLE vs OUT-OF-SAMPLE (NET)")
        self.oos_table = _plain_table(["", "FIRST 70%", "LAST 30%"])
        self.oos_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        oos_panel.layout_box.addWidget(self.oos_table, 1)
        note = QLabel("A rule that shines in the first segment and fades in the last is a classic sign of "
                      "overfitting to the past. Neither segment is a real-world result.")
        note.setObjectName("muted")
        note.setWordWrap(True)
        oos_panel.layout_box.addWidget(note)
        left_l.addWidget(oos_panel, 2)
        split.addWidget(left)

        right = QWidget()
        right_l = QVBoxLayout(right)
        right_l.setContentsMargins(0, 0, 0, 0)
        right_l.setSpacing(7)
        self.eq_panel = ui.TerminalPanel("SIMULATED EQUITY CURVE")
        self.eq_chart, self.eq_axis = _date_chart(ui)
        self.eq_panel.layout_box.addWidget(self.eq_chart, 1)
        right_l.addWidget(self.eq_panel, 3)
        dd_panel = ui.TerminalPanel("SIMULATED DRAWDOWN (%)")
        self.dd_chart, self.dd_axis = _date_chart(ui)
        dd_panel.layout_box.addWidget(self.dd_chart, 1)
        right_l.addWidget(dd_panel, 2)
        trade_panel = ui.TerminalPanel("SIMULATED TRADES (MOST RECENT 200)")
        self.trade_table = _plain_table(["ENTRY", "EXIT", "ENTRY PX", "EXIT PX", "RETURN (NET)", "STATUS"])
        self.trade_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        trade_panel.layout_box.addWidget(self.trade_table, 1)
        right_l.addWidget(trade_panel, 2)
        split.addWidget(right)
        split.setSizes([520, 760])
        layout.addWidget(split, 1)

        self.on_strategy_changed(self.strategy.currentText())

    # -- inputs ----------------------------------------------------------
    def prefill(self):
        if not self.symbol.text().strip():
            self.symbol.setText(self.host.current_ticker)

    def on_strategy_changed(self, name):
        labels, defaults, desc = backtest.STRATEGIES[name]
        self.description.setText(desc)
        for i, (label, box) in enumerate(zip(self.param_labels, self.param_boxes)):
            visible = i < len(labels)
            label.setVisible(visible)
            box.setVisible(visible)
            if visible:
                label.setText(labels[i].upper())
                box.setValue(defaults[i])

    def run(self):
        symbol = self.symbol.text().strip().upper()
        if not symbol:
            QMessageBox.information(self, "Backtest", "Enter a ticker symbol.")
            return
        name = self.strategy.currentText()
        count = len(backtest.STRATEGIES[name][0])
        params = tuple(int(box.value()) for box in self.param_boxes[:count])
        years = self.YEARS[self.window.currentIndex()][1]
        self.status.setText(f"SIMULATING {symbol}…")
        self.serial = self.host.start_job(
            self.host.feature_pool, "backtest", symbol, backtest.run_from_symbol, symbol, name, params, years,
            self.bench.text().strip().upper(), float(self.cost.value()), float(self.slip.value()),
            float(self.capital.value()))

    # -- results ---------------------------------------------------------
    def handle_result(self, serial, key, result):
        if serial != self.serial:
            return
        self.result = result
        self.status.setText(f"{result.symbol} · {result.start:%Y-%m-%d} → {result.end:%Y-%m-%d} · SIMULATED")
        self.render_stats(result)
        self.render_charts(result)
        self.render_trades(result)

    def handle_error(self, serial, key, message):
        if serial == self.serial:
            self.status.setText("")
            QMessageBox.warning(self, "Backtest could not run", message)

    @staticmethod
    def _fmt(v, kind):
        if v is None or (isinstance(v, float) and not np.isfinite(v)):
            return "—"
        return {"pct": f"{v:+.1f}%", "plain": f"{v:.1f}%", "num": f"{v:.2f}", "int": f"{int(v)}"}[kind]

    def render_stats(self, r):
        ui = self.ui
        s, g, h, b = r.stats, r.stats_gross, r.stats_hold, r.stats_bench
        bench_name = r.benchmark or "BENCHMARK"
        self.stats_table.setHorizontalHeaderLabels(
            ["", "STRATEGY (NET)", "BEFORE COSTS", f"{r.symbol} BUY & HOLD", f"{bench_name} BUY & HOLD"])
        f = self._fmt
        rows = [
            ("Total return", [f(x.get("total"), "pct") if x else "—" for x in (s, g, h, b)]),
            ("Annualised (CAGR)", [f(x.get("cagr"), "pct") if x else "—" for x in (s, g, h, b)]),
            ("Volatility (ann.)", [f(x.get("vol"), "plain") if x else "—" for x in (s, g, h, b)]),
            ("Sharpe (rf = 0)", [f(x.get("sharpe"), "num") if x else "—" for x in (s, g, h, b)]),
            ("Max drawdown", [f(x.get("max_dd"), "plain") if x else "—" for x in (s, g, h, b)]),
            ("Time invested", [f(r.summary["exposure"], "plain"), "", "100.0%", ""]),
            ("Trades (round trips)", [str(r.summary["trades"]), "", "1", ""]),
            ("Win rate", [f(r.summary["win_rate"], "plain"), "", "", ""]),
            ("Avg trade (net)", [f(r.summary["avg_trade"], "pct"), "", "", ""]),
            ("Cost drag (pts of return)", [f(r.summary["cost_drag"], "plain"), "", "", ""]),
            ("Ending value", [f"${r.summary['final_value']:,.0f}", f"${r.equity_gross.iloc[-1]:,.0f}",
                              f"${r.equity_hold.iloc[-1]:,.0f}",
                              f"${r.equity_bench.iloc[-1]:,.0f}" if r.equity_bench is not None else "—"]),
        ]
        self.stats_table.setRowCount(len(rows))
        for i, (label, cells) in enumerate(rows):
            self.stats_table.setItem(i, 0, _cell(label, ui, ui.MUTED, align_right=False))
            for j, text in enumerate(cells, start=1):
                self.stats_table.setItem(i, j, _cell(text, ui))
        self.warn_label.setText("\n".join("• " + w for w in r.warnings))

        self.oos_table.setRowCount(0)
        if r.split:
            a, o = r.split["in_sample"], r.split["out_of_sample"]
            lines = [("Total return", "total", "pct"), ("CAGR", "cagr", "pct"),
                     ("Sharpe (rf = 0)", "sharpe", "num"), ("Max drawdown", "max_dd", "plain")]
            self.oos_table.setRowCount(len(lines))
            for i, (label, key, kind) in enumerate(lines):
                self.oos_table.setItem(i, 0, _cell(label, ui, ui.MUTED, align_right=False))
                self.oos_table.setItem(i, 1, _cell(f(a[key], kind), ui))
                self.oos_table.setItem(i, 2, _cell(f(o[key], kind), ui))
            self.oos_table.horizontalHeaderItem(2).setText(f"LAST 30% (FROM {r.split['split_date']:%Y-%m-%d})")

    def render_charts(self, r):
        ui = self.ui
        self.eq_chart.clear()
        self.dd_chart.clear()
        self.eq_panel.title_label.setText(f"SIMULATED EQUITY CURVE · START ${r.capital:,.0f}")
        self.eq_axis.set_dates(r.equity.index)
        self.dd_axis.set_dates(r.equity.index)
        x = np.arange(len(r.equity))
        self.eq_chart.plot(x, r.equity.to_numpy(), pen=pg.mkPen(ui.AMBER, width=2), name="Strategy (net, simulated)")
        self.eq_chart.plot(x, r.equity_hold.to_numpy(), pen=pg.mkPen(ui.BLUE, width=1.5), name=f"{r.symbol} buy & hold")
        if r.equity_bench is not None:
            self.eq_chart.plot(x, r.equity_bench.to_numpy(), pen=pg.mkPen(ui.PURPLE, width=1.5),
                               name=f"{r.benchmark} buy & hold")
        self.eq_chart.setXRange(0, len(x) - 1, padding=0.01)
        dd = backtest.drawdown_series(r.equity).to_numpy()
        dd_hold = backtest.drawdown_series(r.equity_hold).to_numpy()
        self.dd_chart.plot(x, dd, pen=pg.mkPen(ui.RED, width=1.5), fillLevel=0, brush=pg.mkBrush(240, 100, 100, 50),
                           name="Strategy")
        self.dd_chart.plot(x, dd_hold, pen=pg.mkPen(ui.BLUE, width=1), name="Buy & hold")
        self.dd_chart.setXRange(0, len(x) - 1, padding=0.01)

    def render_trades(self, r):
        ui = self.ui
        trades = r.trades[-200:][::-1]
        self.trade_table.setRowCount(len(trades))
        for i, t in enumerate(trades):
            color = ui.GREEN if t["ret"] >= 0 else ui.RED
            self.trade_table.setItem(i, 0, _cell(f"{t['entry']:%Y-%m-%d}", ui, align_right=False))
            self.trade_table.setItem(i, 1, _cell(f"{t['exit']:%Y-%m-%d}", ui, align_right=False))
            self.trade_table.setItem(i, 2, _cell(f"{t['entry_px']:,.2f}", ui))
            self.trade_table.setItem(i, 3, _cell(f"{t['exit_px']:,.2f}", ui))
            self.trade_table.setItem(i, 4, _cell(f"{t['ret']:+.2f}%", ui, color, True))
            self.trade_table.setItem(i, 5, _cell("OPEN (marked to last close)" if t["open"] else "closed", ui,
                                                 ui.MUTED, align_right=False))


# ==========================================================================
# 10. AI RESEARCH ASSISTANT
# ==========================================================================
class AssistantPage(QWidget):
    def __init__(self, host, ui):
        super().__init__()
        self.host, self.ui = host, ui
        self.serial = 0
        self.pending = False
        self.note_ids = []

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(7)

        # ---- left: conversation
        chat = ui.TerminalPanel("RESEARCH ASSISTANT")
        self.context = QLabel("")
        self.context.setObjectName("muted")
        chat.layout_box.addWidget(self.context)
        self.transcript = QTextBrowser()
        self.transcript.setOpenExternalLinks(False)
        self.transcript.setStyleSheet(f"background: {ui.PANEL}; border: none; color: {ui.TEXT};")
        self.transcript.setHtml(self._intro())
        chat.layout_box.addWidget(self.transcript, 1)
        row = QHBoxLayout()
        self.question = QLineEdit()
        self.question.setPlaceholderText("Ask about the loaded company's financials, metrics or your saved notes…")
        self.question.returnPressed.connect(self.ask)
        row.addWidget(self.question, 1)
        self.ask_btn = QPushButton("ASK")
        self.ask_btn.setObjectName("accent")
        self.ask_btn.clicked.connect(self.ask)
        row.addWidget(self.ask_btn)
        key_btn = QPushButton("SET API KEY")
        key_btn.clicked.connect(self.set_key)
        row.addWidget(key_btn)
        chat.layout_box.addLayout(row)
        self.privacy = QLabel()
        self.privacy.setObjectName("muted")
        self.privacy.setWordWrap(True)
        chat.layout_box.addWidget(self.privacy)
        layout.addWidget(chat, 3)

        # ---- right: saved analysis
        side = ui.TerminalPanel("MY SAVED ANALYSIS")
        self.notes_list = QListWidget()
        self.notes_list.itemSelectionChanged.connect(self.on_note_selected)
        side.layout_box.addWidget(self.notes_list, 2)
        self.note_view = QLabel("")
        self.note_view.setWordWrap(True)
        self.note_view.setObjectName("muted")
        self.note_view.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        side.layout_box.addWidget(self.note_view, 1)
        delete = QPushButton("DELETE SELECTED")
        delete.clicked.connect(self.delete_note)
        side.layout_box.addWidget(delete)
        side.layout_box.addWidget(host.muted_label("NEW NOTE FOR THIS TICKER"))
        self.note_title = QLineEdit()
        self.note_title.setPlaceholderText("Title (e.g. 'Margin thesis')")
        side.layout_box.addWidget(self.note_title)
        self.note_text = QPlainTextEdit()
        self.note_text.setPlaceholderText("Your own analysis, assumptions or questions…")
        self.note_text.setFixedHeight(110)
        side.layout_box.addWidget(self.note_text)
        save = QPushButton("SAVE NOTE")
        save.clicked.connect(self.save_note)
        side.layout_box.addWidget(save)
        layout.addWidget(side, 2)

        self.refresh_privacy()

    # -- helpers ---------------------------------------------------------
    def _intro(self):
        c = self.ui
        return (f'<div style="color:{c.MUTED}">Ask things like <i>"How has revenue and net margin changed over '
                'the last four years?"</i>, <i>"How much debt does it carry vs cash?"</i> or <i>"What did I write '
                'about this company?"</i><br><br>Answers use only data loaded in this terminal and your saved '
                'notes, cite the figures they rely on ([F1], [F2]…) and list those sources below each answer. '
                'The assistant does not predict prices or give investment advice.</div>')

    def refresh_privacy(self):
        key, source = ra.api_key_source()
        if key:
            self.privacy.setText("Written answers send the retrieved facts (including any matching saved notes) "
                                 f"to Google's Gemini API using model {ra.DEFAULT_MODEL}. Key found in: {source}.")
        else:
            self.privacy.setText("No GEMINI_API_KEY set: the assistant returns a plain lookup of matching data "
                                 "(nothing is sent anywhere). Click SET API KEY to enable written answers.")

    def set_key(self):
        text, ok = QInputDialog.getText(self, "Gemini API key", "Paste your Gemini API key:",
                                        QLineEdit.EchoMode.Password)
        if not ok or not text.strip():
            return
        try:
            ra.save_key(text)
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "API key", f"Could not save the key: {error}")
            return
        self.refresh_privacy()
        QMessageBox.information(self, "API key", f"Saved to {ra.KEY_FILE}.\nKeep that file private.")

    def ready(self):
        b = self.host.bundle
        return bool(b) and b.get("symbol") == self.host.current_ticker and self.host.fund_data is not None

    def refresh_context(self):
        sym = self.host.current_ticker
        state = "data loaded" if self.ready() else "loading data…"
        self.context.setText(f"CONTEXT: {sym} · {state}")
        self.reload_notes()

    # -- notes -----------------------------------------------------------
    def reload_notes(self):
        notes = notes_store.load_notes(self.host.current_ticker)
        self.notes_list.clear()
        self.note_ids = []
        for note in notes:
            stamp = pd.Timestamp(note["created"], unit="s").strftime("%Y-%m-%d")
            self.notes_list.addItem(f"{stamp} · {note.get('title') or 'Untitled'}")
            self.note_ids.append(note["id"])
        self._notes = {n["id"]: n for n in notes}
        self.note_view.setText("" if notes else "No saved notes for this ticker yet.")

    def on_note_selected(self):
        row = self.notes_list.currentRow()
        if 0 <= row < len(self.note_ids):
            self.note_view.setText(self._notes[self.note_ids[row]]["text"])

    def save_note(self):
        try:
            notes_store.add_note(self.host.current_ticker, self.note_text.toPlainText(), self.note_title.text())
        except (ValueError, OSError) as error:
            QMessageBox.warning(self, "Saved analysis", str(error))
            return
        self.note_text.clear()
        self.note_title.clear()
        self.reload_notes()

    def delete_note(self):
        row = self.notes_list.currentRow()
        if row < 0 or row >= len(self.note_ids):
            return
        if QMessageBox.question(self, "Delete note", "Delete the selected note?") == QMessageBox.StandardButton.Yes:
            notes_store.delete_note(self.note_ids[row])
            self.reload_notes()

    # -- asking ----------------------------------------------------------
    def append(self, html_block):
        self.transcript.append(html_block)
        self.transcript.verticalScrollBar().setValue(self.transcript.verticalScrollBar().maximum())

    def ask(self):
        question = self.question.text().strip()
        if not question or self.pending:
            return
        if not self.ready():
            QMessageBox.information(self, "Assistant", "Wait for the company's financials to finish loading "
                                    "(see the FINANCIALS page), then ask again.")
            return
        sym = self.host.current_ticker
        bundle = self.host.bundle
        facts = ra.build_facts(sym, bundle["info"], self.host.fund_data, bundle["frame"],
                               notes_store.load_notes(sym))
        c = self.ui
        self.append(f'<p><b style="color:{c.AMBER}">YOU ({html.escape(sym)})</b><br>{html.escape(question)}</p>')
        self.question.clear()
        self.pending = True
        self.ask_btn.setEnabled(False)
        self.ask_btn.setText("…")
        self.serial = self.host.start_job(self.host.feature_pool, "assistant", sym,
                                          ra.answer_question, question, sym, facts)

    def _finish(self):
        self.pending = False
        self.ask_btn.setEnabled(True)
        self.ask_btn.setText("ASK")

    def handle_result(self, serial, key, answer):
        if serial != self.serial:
            return
        self._finish()
        c = self.ui
        mode = {"llm": "WRITTEN ANSWER · GENERATED", "lookup": "DATA LOOKUP · NOT GENERATED",
                "no_match": "NO MATCHING DATA", "no_data": "NO DATA"}[answer.mode]
        body = html.escape(answer.text).replace("\n", "<br>")
        body = ra._CITE.sub(lambda m: f'<span style="color:{c.AMBER}">[{m.group(1)}]</span>', body)
        parts = [f'<p><b style="color:{c.BLUE}">ASSISTANT</b> '
                 f'<span style="color:{c.MUTED}">· {mode} · {html.escape(key)}</span><br>{body}</p>']
        if answer.note:
            parts.append(f'<p style="color:{c.MUTED}">{html.escape(answer.note)}</p>')
        if answer.issues:
            items = "".join(f"<li>{html.escape(i)}</li>" for i in answer.issues)
            parts.append(f'<p style="color:{c.RED}"><b>Check this answer:</b></p>'
                         f'<ul style="color:{c.RED}">{items}</ul>')
        if answer.facts:
            cited = set(answer.cited)
            lines = []
            for f in answer.facts:
                text = html.escape(f.text if len(f.text) <= 260 else f.text[:257] + "…")
                weight = "700" if f.id in cited else "400"
                lines.append(f'<div style="font-weight:{weight};color:{c.MUTED}"><span style="color:{c.AMBER}">'
                             f'[{f.id}]</span> {text} <i>— {html.escape(f.source)}</i></div>')
            parts.append(f'<p style="color:{c.MUTED}"><b>SOURCES (bold = cited):</b></p>' + "".join(lines))
        parts.append(f'<p style="color:{c.MUTED}"><i>{html.escape(ra.DISCLAIMER)}</i></p><hr>')
        self.append("".join(parts))

    def handle_error(self, serial, key, message):
        if serial != self.serial:
            return
        self._finish()
        self.append(f'<p style="color:{self.ui.RED}">The assistant failed: {html.escape(message)}</p>')
