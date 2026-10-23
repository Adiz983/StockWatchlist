import itertools
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import pyqtgraph as pg
from PySide6.QtCore import QObject, QPointF, QRectF, QRunnable, QThreadPool, QTimer, Qt, Signal, Slot
from PySide6.QtGui import QColor, QFont, QPainter, QPicture
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QButtonGroup, QCheckBox, QComboBox, QFrame,
    QGridLayout, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit,
    QMainWindow, QMessageBox, QPushButton, QScrollArea, QSplitter, QStackedWidget,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

PROJECT_DIR = Path(__file__).resolve().parent
os.chdir(PROJECT_DIR)
import main as core  # noqa: E402  
import analysis  # noqa: E402
import data_service as ds  # noqa: E402

SETTINGS_FILE = PROJECT_DIR / "terminal_settings.json"

# Color
BG = "#080A0D"
PANEL = "#0D1117"
PANEL2 = "#111821"
BORDER = "#252D38"
TEXT = "#D7DEE8"
MUTED = "#778596"
AMBER = "#F0A23A"
GREEN = "#42C995"
RED = "#F06464"
BLUE = "#72A7D8"
PURPLE = "#B07CE8"

MA_COLORS = {20: AMBER, 50: BLUE, 200: PURPLE}
MAX_CANDLES = 1500          
QUOTE_REFRESH_MS = 60_000
DEFAULT_BENCHMARK = "SPY"

# name 
LAYOUTS = {
    "RESEARCH · 5 PANELS": {
        "cells": [("PRICE", 0, 0, 2, 1), ("RELATIVE", 0, 1, 1, 1), ("VOLUME", 1, 1, 1, 1),
                  ("RSI", 2, 0, 1, 1), ("MACD", 2, 1, 1, 1)],
        "rows": [3, 3, 2], "cols": [3, 2],
    },
    "STACKED · 4 PANELS": {
        "cells": [("PRICE", 0, 0, 1, 1), ("VOLUME", 1, 0, 1, 1), ("RSI", 2, 0, 1, 1), ("MACD", 3, 0, 1, 1)],
        "rows": [6, 2, 2, 2], "cols": [1],
    },
    "2 × 2": {
        "cells": [("PRICE", 0, 0, 1, 1), ("RELATIVE", 0, 1, 1, 1), ("RSI", 1, 0, 1, 1), ("MACD", 1, 1, 1, 1)],
        "rows": [3, 2], "cols": [1, 1],
    },
    "PRICE + VOLUME": {
        "cells": [("PRICE", 0, 0, 1, 1), ("VOLUME", 1, 0, 1, 1)],
        "rows": [4, 1], "cols": [1],
    },
    "PRICE ONLY": {
        "cells": [("PRICE", 0, 0, 1, 1)],
        "rows": [1], "cols": [1],
    },
}
DEFAULT_LAYOUT = "RESEARCH · 5 PANELS"

KIND_LABELS = {
    "PRICE": "PRICE",
    "VOLUME": "VOLUME",
    "RELATIVE": "REL. PERFORMANCE",
    "RSI": "RSI (14)",
    "MACD": "MACD (12,26,9)",
}



# Formatting helpers

def val_num(value):
    try:
        if value is None:
            return None
        number = float(value)
        return number if number == number and abs(number) != float("inf") else None
    except (TypeError, ValueError):
        return None


def fmt_money(value, decimals=2):
    number = val_num(value)
    return "N/A" if number is None else f"${number:,.{decimals}f}"


def fmt_large(value, dollars=True):
    number = val_num(value)
    if number is None:
        return "N/A"
    sign = "-" if number < 0 else ""
    prefix = "$" if dollars else ""
    mag = abs(number)
    for scale, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M")):
        if mag >= scale:
            return f"{sign}{prefix}{mag / scale:,.2f}{suffix}"
    return f"{sign}{prefix}{mag:,.0f}"


def fmt_pct(value, signed=False):
    """`value` is already in percent units (12.3 -> 12.30%)."""
    number = val_num(value)
    if number is None:
        return "N/A"
    return f"{number:+.2f}%" if signed else f"{number:.2f}%"


def fmt_frac(value, signed=False):
    """`value` is a fraction (0.123 -> 12.30%)."""
    number = val_num(value)
    return "N/A" if number is None else fmt_pct(number * 100.0, signed)


def fmt_mult(value):
    number = val_num(value)
    return "N/A" if number is None else f"{number:,.2f}x"


def fmt_statement_value(value, style):
    number = val_num(value)
    if number is None:
        return "—"
    if style == "pct":
        return f"{number:.1f}%"
    if style == "eps":
        return f"{number:,.2f}"
    if style == "count":
        return fmt_large(number, dollars=False)
    return fmt_large(number)


def clear_layout(layout):
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.deleteLater()
        elif item.layout() is not None:
            clear_layout(item.layout())


def right_item(text, color=None, bold=False):
    item = QTableWidgetItem(text)
    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    if color:
        item.setForeground(QColor(color))
    if bold:
        font = item.font()
        font.setBold(True)
        item.setFont(font)
    return item


def tone_color(tone):
    return {"up": GREEN, "down": RED}.get(tone, TEXT)



# Background work

class WorkerSignals(QObject):
    done = Signal(object, object)    
    failed = Signal(object, str)     


class Worker(QRunnable):
    """Runs a blocking function off the UI thread; reports back by token."""

    def __init__(self, fn, args, token):
        super().__init__()
        self.fn, self.args, self.token = fn, args, token
        self.signals = WorkerSignals()
        self.setAutoDelete(False)  

    def run(self):
        try:
            result = self.fn(*self.args)
        except Exception as error:  
            self.signals.failed.emit(self.token, str(error))
            return
        self.signals.done.emit(self.token, result)



# Chart building blocks

class IndexDateAxis(pg.AxisItem):
    """Bottom axis for charts plotted by bar index; labels show session dates,
    which keeps weekends/holidays from leaving gaps between candles."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.dates = []

    def set_dates(self, dates):
        self.dates = list(dates)

    def tickStrings(self, values, scale, spacing):
        n = len(self.dates)
        if n == 0:
            return ["" for _ in values]
        span = (self.dates[-1] - self.dates[0]).days if n > 1 else 0
        fmt = "%b %d" if span <= 120 else ("%b '%y" if span <= 900 else "%Y")
        labels = []
        for value in values:
            i = int(round(value))
            labels.append(self.dates[i].strftime(fmt) if abs(value - i) < 1e-3 and 0 <= i < n else "")
        return labels


class CandlestickItem(pg.GraphicsObject):
    def __init__(self, x, o, h, l, c):
        super().__init__()
        self.rows = list(zip(x, o, h, l, c))
        self.picture = QPicture()
        self._generate()

    def _generate(self):
        painter = QPainter(self.picture)
        half = 0.35
        for x, o, h, l, c in self.rows:
            if not all(np.isfinite(v) for v in (o, h, l, c)):
                continue
            color = QColor(GREEN if c >= o else RED)
            painter.setPen(pg.mkPen(color))
            painter.setBrush(pg.mkBrush(color))
            painter.drawLine(QPointF(float(x), float(l)), QPointF(float(x), float(h)))
            painter.drawRect(QRectF(float(x) - half, float(o), half * 2, float(c) - float(o)))
        painter.end()

    def paint(self, painter, *args):
        painter.drawPicture(0, 0, self.picture)

    def boundingRect(self):
        return QRectF(self.picture.boundingRect())


@dataclass
class ChartContext:
    ticker: str
    benchmark: str
    view: pd.DataFrame            
    bench: pd.Series | None       
    bench_error: str | None
    candles: bool
    mas: dict
    period: str


class TerminalPanel(QFrame):
    def __init__(self, title):
        super().__init__()
        self.setObjectName("terminalPanel")
        self.layout_box = QVBoxLayout(self)
        self.layout_box.setContentsMargins(10, 8, 10, 10)
        self.layout_box.setSpacing(7)
        self.header_box = QHBoxLayout()
        self.title_label = QLabel(title.upper())
        self.title_label.setObjectName("panelTitle")
        self.header_box.addWidget(self.title_label)
        self.header_box.addStretch()
        self.layout_box.addLayout(self.header_box)


def style_chart(chart):
    chart.setBackground(PANEL)
    chart.showGrid(x=True, y=True, alpha=0.14)
    chart.getAxis("bottom").setTextPen(MUTED)
    chart.getAxis("left").setTextPen(MUTED)
    chart.getAxis("bottom").setPen(BORDER)
    chart.getAxis("left").setPen(BORDER)
    chart.setMinimumHeight(90)
    chart.setMenuEnabled(False)
    chart.getViewBox().setBorder(pg.mkPen(BORDER))


def add_legend(chart):
    try:
        return chart.addLegend(offset=(8, 6), labelTextColor=TEXT)
    except TypeError:  # older pyqtgraph without labelTextColor
        return chart.addLegend(offset=(8, 6))


def show_no_data(chart, message):
    chart.setXRange(0, 1)
    chart.setYRange(0, 1)
    note = pg.TextItem(message, color=MUTED, anchor=(0.5, 0.5))
    chart.addItem(note)
    note.setPos(0.5, 0.5)


class ChartPanel(TerminalPanel):
    """One cell of the chart grid. The dropdown picks what it displays."""

    kind_changed = Signal(object)   
    hovered = Signal(int)        

    def __init__(self, kind):
        super().__init__(KIND_LABELS[kind])
        self.kind = kind
        self.combo = QComboBox()
        for key, label in KIND_LABELS.items():
            self.combo.addItem(label, key)
        self.combo.setCurrentIndex(list(KIND_LABELS).index(kind))
        self.combo.setFixedWidth(150)
        self.combo.currentIndexChanged.connect(self._on_combo)
        self.header_box.insertWidget(0, self.combo)
        self.header_box.insertSpacing(1, 10)
        self.title_label.setText("")

        self.axis = IndexDateAxis(orientation="bottom")
        self.plot = pg.PlotWidget(axisItems={"bottom": self.axis})
        style_chart(self.plot)
        self.plot.getAxis("left").setWidth(62)   # keeps stacked panels visually aligned
        add_legend(self.plot)
        self.vline = pg.InfiniteLine(angle=90, movable=False,
                                     pen=pg.mkPen(MUTED, width=1, style=Qt.PenStyle.DashLine))
        self.plot.addItem(self.vline, ignoreBounds=True)
        self.layout_box.addWidget(self.plot, 1)
        self._proxy = pg.SignalProxy(self.plot.scene().sigMouseMoved, rateLimit=60, slot=self._on_mouse)

    def _on_combo(self, _index):
        self.kind = self.combo.currentData()
        self.kind_changed.emit(self)

    def _on_mouse(self, event):
        pos = event[0]
        vb = self.plot.getViewBox()
        if vb.sceneBoundingRect().contains(pos):
            self.hovered.emit(int(round(vb.mapSceneToView(pos).x())))

    # rendering
    def render(self, ctx): 
        plot = self.plot
        plot.clear()
        plot.addItem(self.vline, ignoreBounds=True)
        vb = plot.getViewBox()
        vb.setLimits(yMin=None, yMax=None)
        vb.enableAutoRange(axis=pg.ViewBox.YAxis, enable=True)
        vb.setAutoVisible(y=True)
        n = len(ctx.view)
        self.axis.set_dates(ctx.view.index)
        self.title_label.setText(f"{ctx.ticker}  ·  {ctx.period.upper()}")
        if n == 0:
            show_no_data(plot, "NO DATA")
            return
        vb.setLimits(xMin=-2, xMax=n + 1, minXRange=min(10, n))
        getattr(self, f"_draw_{self.kind.lower()}")(ctx)

    def _draw_price(self, ctx):
        view, plot = ctx.view, self.plot
        n = len(view)
        x = np.arange(n, dtype=float)
        o, h, l, c = (view[k].to_numpy(float) for k in ("Open", "High", "Low", "Close"))
        if ctx.candles and n <= MAX_CANDLES:
            plot.addItem(CandlestickItem(x, o, h, l, c))
        else:
            if ctx.candles:
                self.title_label.setText(self.title_label.text() + "  ·  LINE (TOO MANY BARS FOR CANDLES)")
            plot.plot(x, c, pen=pg.mkPen(GREEN if c[-1] >= c[0] else RED, width=2), connect="finite")
        for window in analysis.MA_WINDOWS:
            if ctx.mas.get(window):
                ma = view[f"SMA{window}"].to_numpy(float)
                if np.isfinite(ma).any():
                    plot.plot(x, ma, pen=pg.mkPen(MA_COLORS[window], width=1.3),
                              name=f"SMA {window}", connect="finite")
        plot.setLabel("left", "PRICE ($)", color=MUTED)

    def _draw_volume(self, ctx):
        view, plot = ctx.view, self.plot
        x = np.arange(len(view), dtype=float)
        volume = view["Volume"].fillna(0).to_numpy(float) / 1e6
        rising = view["Close"].to_numpy(float) >= view["Open"].to_numpy(float)
        for mask, color in ((rising, GREEN), (~rising, RED)):
            if mask.any():
                fill = QColor(color)
                fill.setAlpha(170)
                plot.addItem(pg.BarGraphItem(x=x[mask], height=volume[mask], width=0.7,
                                             brush=pg.mkBrush(fill), pen=pg.mkPen(None)))
        avg = (view["Volume"].rolling(20, min_periods=5).mean() / 1e6).to_numpy(float)
        if np.isfinite(avg).any():
            plot.plot(x, avg, pen=pg.mkPen(AMBER, width=1.3), name="20-SESSION AVG", connect="finite")
        plot.setLabel("left", "VOLUME (M)", color=MUTED)

    def _draw_relative(self, ctx):
        plot = self.plot
        x = np.arange(len(ctx.view), dtype=float)
        if ctx.bench is None:
            if ctx.benchmark == ctx.ticker:
                message = "PICK A BENCHMARK OTHER THAN THE SYMBOL ITSELF"
            else:
                message = ctx.bench_error or f"BENCHMARK {ctx.benchmark} UNAVAILABLE"
            show_no_data(plot, message)
            return
        stock = analysis.rebased_keep_index(ctx.view["Close"]).to_numpy(float)
        bench = analysis.rebased_keep_index(ctx.bench).to_numpy(float)
        plot.addItem(pg.InfiniteLine(pos=0, angle=0, pen=pg.mkPen(BORDER, width=1)))
        plot.plot(x, stock, pen=pg.mkPen(AMBER, width=2), name=ctx.ticker, connect="finite")
        plot.plot(x, bench, pen=pg.mkPen(BLUE, width=1.5), name=ctx.benchmark, connect="finite")
        plot.setLabel("left", "RETURN (%)", color=MUTED)

    def _draw_rsi(self, ctx):
        plot = self.plot
        x = np.arange(len(ctx.view), dtype=float)
        for level, color in ((70, RED), (50, BORDER), (30, GREEN)):
            plot.addItem(pg.InfiniteLine(pos=level, angle=0,
                                         pen=pg.mkPen(color, width=1, style=Qt.PenStyle.DashLine)))
        values = ctx.view["RSI"].to_numpy(float)
        plot.plot(x, values, pen=pg.mkPen(BLUE, width=1.6), connect="finite")
        vb = plot.getViewBox()
        vb.enableAutoRange(axis=pg.ViewBox.YAxis, enable=False)
        plot.setYRange(0, 100, padding=0.03)
        plot.setLabel("left", "RSI", color=MUTED)

    def _draw_macd(self, ctx):
        view, plot = ctx.view, self.plot
        x = np.arange(len(view), dtype=float)
        hist = view["MACD_HIST"].to_numpy(float)
        finite = np.isfinite(hist)
        plot.addItem(pg.InfiniteLine(pos=0, angle=0, pen=pg.mkPen(BORDER, width=1)))
        for mask, color in ((finite & (hist >= 0), GREEN), (finite & (hist < 0), RED)):
            if mask.any():
                fill = QColor(color)
                fill.setAlpha(170)
                plot.addItem(pg.BarGraphItem(x=x[mask], height=hist[mask], width=0.7,
                                             brush=pg.mkBrush(fill), pen=pg.mkPen(None)))
        plot.plot(x, view["MACD"].to_numpy(float), pen=pg.mkPen(BLUE, width=1.5), name="MACD", connect="finite")
        plot.plot(x, view["MACD_SIGNAL"].to_numpy(float), pen=pg.mkPen(AMBER, width=1.5),
                  name="SIGNAL", connect="finite")
        plot.setLabel("left", "MACD", color=MUTED)



# Main window

class StockResearchTerminal(QMainWindow):
    STATEMENTS = [("INCOME STATEMENT", "income"), ("BALANCE SHEET", "balance"), ("CASH FLOW", "cashflow")]

    def __init__(self):
        super().__init__()
        self.setWindowTitle("STOCKWATCHLIST // EQUITY RESEARCH TERMINAL")
        self.resize(1600, 940)
        self.setMinimumSize(1180, 740)

        self.settings = self.load_settings()
        self.watchlist = core.load_watchlist()
        self.current_ticker = "AAPL"
        self.benchmark = self.settings.get("benchmark", DEFAULT_BENCHMARK)

        self.bundle = None           
        self.ctx = None             
        self.fund_data = None       
        self.quotes = {}           
        self.quote_inflight = set()
        self.panels = []

        self._serial = itertools.count(1)
        self._jobs = {}              
        self.research_serial = 0
        self.fund_serial = 0
        self.research_pool = QThreadPool()
        self.research_pool.setMaxThreadCount(2)
        self.quote_pool = QThreadPool()
        self.quote_pool.setMaxThreadCount(6)

        self.apply_stylesheet()
        self.build_ui()

        self.quote_timer = QTimer(self)
        self.quote_timer.setInterval(QUOTE_REFRESH_MS)
        self.quote_timer.timeout.connect(self.refresh_quotes)
        if self.auto_refresh.isChecked():
            self.quote_timer.start()

        self.populate_watchlist()
        self.refresh_quotes()
        self.load_ticker(self.current_ticker)

    # settings 
    @staticmethod
    def load_settings():
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as file:
                data = json.load(file)
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def save_settings(self):
        data = {
            "layout": self.layout_combo.currentText(),
            "chart_type": self.type_combo.currentText(),
            "mas": {str(w): box.isChecked() for w, box in self.ma_boxes.items()},
            "benchmark": self.benchmark,
            "period": self.period.currentText(),
        }
        try:
            with open(SETTINGS_FILE, "w", encoding="utf-8") as file:
                json.dump(data, file, indent=4)
        except OSError:
            pass

    #  styling 
    def apply_stylesheet(self):
        self.setStyleSheet(f"""
            QMainWindow, QWidget {{ background-color: {BG}; color: {TEXT}; font-size: 11px; }}
            QLabel {{ color: {TEXT}; }}
            QLabel#brand {{ color: {AMBER}; font-size: 16px; font-weight: 900; letter-spacing: 1px; }}
            QLabel#muted {{ color: {MUTED}; }}
            QLabel#panelTitle {{ color: {AMBER}; font-size: 10px; font-weight: 800; letter-spacing: 1px; }}
            QLabel#tickerName {{ font-size: 21px; font-weight: 800; }}
            QLabel#lastPrice {{ font-size: 24px; font-weight: 800; }}
            QLabel#smallMetric {{ color: {MUTED}; font-size: 9px; font-weight: 700; }}
            QLabel#metricValue {{ font-size: 13px; font-weight: 700; }}
            QFrame#topbar {{ background-color: #0B0E12; border-bottom: 1px solid {BORDER}; }}
            QFrame#leftRail {{ background-color: #090C10; border-right: 1px solid {BORDER}; }}
            QFrame#terminalPanel {{ background-color: {PANEL}; border: 1px solid {BORDER}; }}
            QPushButton {{ background-color: #171E27; color: {TEXT}; border: 1px solid #303B49; border-radius: 2px; padding: 5px 9px; }}
            QPushButton:hover {{ background-color: #263341; border-color: {AMBER}; }}
            QPushButton#accent {{ color: #111111; background-color: {AMBER}; border-color: {AMBER}; font-weight: 800; }}
            QPushButton#railButton {{ text-align: left; background: transparent; border: none; color: {MUTED}; padding: 8px 7px; }}
            QPushButton#railButton:checked {{ color: {AMBER}; background: #1A1B1B; border-left: 2px solid {AMBER}; }}
            QLineEdit, QComboBox {{ background: #080B0F; color: {TEXT}; border: 1px solid #303B49; border-radius: 2px; padding: 5px; }}
            QCheckBox {{ color: {TEXT}; spacing: 5px; background: transparent; }}
            QScrollArea {{ border: none; background: transparent; }}
            QTableWidget {{ background: {PANEL}; alternate-background-color: #0A0E13; gridline-color: #202833; border: none; selection-background-color: #293746; }}
            QHeaderView::section {{ background: #171E27; color: {MUTED}; padding: 6px; border: none; border-right: 1px solid {BORDER}; border-bottom: 1px solid {BORDER}; font-size: 9px; font-weight: 800; }}
            QTableWidget::item {{ padding: 3px; }}
            QComboBox QAbstractItemView {{ background: {PANEL}; selection-background-color: #293746; }}
            QStatusBar {{ background: #090C10; color: {MUTED}; border-top: 1px solid {BORDER}; }}
        """)

    #  UI 
    def build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        root_layout.addWidget(self.build_topbar())

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        root_layout.addLayout(body, 1)
        body.addWidget(self.build_rail())

        workspace = QWidget()
        work_layout = QVBoxLayout(workspace)
        work_layout.setContentsMargins(8, 8, 8, 8)
        work_layout.setSpacing(7)
        body.addWidget(workspace, 1)

        work_layout.addWidget(self.build_quote_panel())
        work_layout.addWidget(self.build_stats_panel())

        self.pages = QStackedWidget()
        self.pages.addWidget(self.build_charts_page())
        self.pages.addWidget(self.build_fundamentals_page())
        work_layout.addWidget(self.pages, 1)

        bottom = QHBoxLayout()
        self.status_text = QLabel("READY")
        self.status_text.setObjectName("muted")
        bottom.addWidget(self.status_text)
        bottom.addStretch()
        note = QLabel("RESEARCH TOOL ONLY · NOT A BROKER · NOT INVESTMENT ADVICE")
        note.setObjectName("muted")
        bottom.addWidget(note)
        work_layout.addLayout(bottom)
        self.statusBar().showMessage("Ready · No order entry or portfolio tracking")

        # Restore saved choices
        self.apply_saved_settings()

    def build_topbar(self):
        top = QFrame()
        top.setObjectName("topbar")
        layout = QHBoxLayout(top)
        layout.setContentsMargins(12, 7, 12, 7)
        layout.setSpacing(10)
        brand = QLabel("SW  STOCKWATCHLIST")
        brand.setObjectName("brand")
        layout.addWidget(brand)
        tag = QLabel("EQUITY RESEARCH TERMINAL")
        tag.setObjectName("muted")
        layout.addWidget(tag)
        layout.addSpacing(20)
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Ticker / company symbol  [Enter to load]")
        self.search_box.setMaximumWidth(330)
        self.search_box.returnPressed.connect(self.load_ticker_from_search)
        layout.addWidget(self.search_box)
        go = QPushButton("LOAD")
        go.setObjectName("accent")
        go.clicked.connect(self.load_ticker_from_search)
        layout.addWidget(go)
        layout.addWidget(QLabel("PERIOD"))
        self.period = QComboBox()
        self.period.addItems([p.lower() for p in analysis.PERIODS])
        self.period.setCurrentText("1y")
        self.period.currentTextChanged.connect(self.on_period_changed)
        layout.addWidget(self.period)
        refresh = QPushButton("⟳  REFRESH ALL")
        refresh.clicked.connect(self.refresh_all)
        layout.addWidget(refresh)
        layout.addStretch()
        self.clock_status = QLabel("● DATA CONNECTION")
        self.clock_status.setStyleSheet(f"color: {GREEN}; font-weight: 800;")
        layout.addWidget(self.clock_status)
        return top

    def build_rail(self):
        rail = QFrame()
        rail.setObjectName("leftRail")
        rail.setFixedWidth(300)
        layout = QVBoxLayout(rail)
        layout.setContentsMargins(8, 10, 8, 8)
        layout.setSpacing(4)
        title = QLabel("WORKSPACE")
        title.setObjectName("panelTitle")
        layout.addWidget(title)

        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        for index, text in enumerate(("RESEARCH OVERVIEW", "FINANCIALS")):
            btn = QPushButton("▸  " + text)
            btn.setObjectName("railButton")
            btn.setCheckable(True)
            btn.setChecked(index == 0)
            self.nav_group.addButton(btn, index)
            layout.addWidget(btn)
        self.nav_group.idClicked.connect(self.pages_set_index)
        layout.addSpacing(12)

        header = QHBoxLayout()
        label = QLabel("WATCHLIST")
        label.setObjectName("panelTitle")
        header.addWidget(label)
        header.addStretch()
        refresh = QPushButton("⟳")
        refresh.setFixedWidth(28)
        refresh.setToolTip("Refresh all watchlist quotes")
        refresh.clicked.connect(lambda: self.refresh_quotes())
        header.addWidget(refresh)
        add = QPushButton("+")
        add.setFixedWidth(28)
        add.setToolTip("Add ticker")
        add.clicked.connect(self.add_ticker)
        header.addWidget(add)
        layout.addLayout(header)

        self.watch_table = QTableWidget(0, 4)
        self.watch_table.setHorizontalHeaderLabels(["SYMBOL", "LAST", "CHG", "%"])
        self.watch_table.verticalHeader().setVisible(False)
        self.watch_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.watch_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.watch_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.watch_table.setShowGrid(False)
        hh = self.watch_table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.watch_table.cellDoubleClicked.connect(self.open_watchlist_row)
        layout.addWidget(self.watch_table, 1)

        remove = QPushButton("−  REMOVE SELECTED")
        remove.clicked.connect(self.remove_ticker)
        layout.addWidget(remove)

        refresh_row = QHBoxLayout()
        self.auto_refresh = QCheckBox("AUTO 60s")
        self.auto_refresh.setChecked(True)
        self.auto_refresh.toggled.connect(self.on_auto_refresh_toggled)
        refresh_row.addWidget(self.auto_refresh)
        refresh_row.addStretch()
        self.quote_time = QLabel("QUOTES —")
        self.quote_time.setObjectName("muted")
        refresh_row.addWidget(self.quote_time)
        layout.addLayout(refresh_row)

        source = QLabel("SOURCE: YAHOO FINANCE\nSEARCH: ALPHA VANTAGE\nQuotes may be delayed.")
        source.setObjectName("muted")
        source.setWordWrap(True)
        layout.addWidget(source)
        return rail

    def build_quote_panel(self):
        panel = TerminalPanel("SECURITY SNAPSHOT")
        row = QHBoxLayout()
        left = QVBoxLayout()
        self.company_name = QLabel("Apple Inc.")
        self.company_name.setObjectName("tickerName")
        self.ticker_label = QLabel("AAPL · EQUITY · US")
        self.ticker_label.setObjectName("muted")
        left.addWidget(self.company_name)
        left.addWidget(self.ticker_label)
        row.addLayout(left, 2)
        right_mid = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        self.price_label = QLabel("—")
        self.price_label.setObjectName("lastPrice")
        row.addWidget(self.price_label, 1, right_mid)
        self.change_label = QLabel("Loading quote…")
        self.change_label.setMinimumWidth(135)
        row.addWidget(self.change_label, 1, right_mid)
        self.market_state = QLabel("LATEST AVAILABLE DATA")
        self.market_state.setObjectName("muted")
        row.addWidget(self.market_state, 1, right_mid)
        panel.layout_box.addLayout(row)
        return panel

    def build_stats_panel(self):
        panel = TerminalPanel("KEY MARKET DATA")
        grid = QGridLayout()
        grid.setHorizontalSpacing(22)
        grid.setVerticalSpacing(9)
        self.stat_values = {}
        keys = ["Market Cap", "Volume", "Avg Volume", "P/E (TTM)",
                "52W High", "52W Low", "Day Range", "Dividend Yield"]
        for idx, key in enumerate(keys):
            cell = QVBoxLayout()
            name = QLabel(key.upper())
            name.setObjectName("smallMetric")
            value = QLabel("—")
            value.setObjectName("metricValue")
            cell.addWidget(name)
            cell.addWidget(value)
            grid.addLayout(cell, idx // 4, idx % 4)
            self.stat_values[key] = value
        panel.layout_box.addLayout(grid)
        return panel

    # charts page 
    def build_charts_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        bar.addWidget(self.muted_label("LAYOUT"))
        self.layout_combo = QComboBox()
        self.layout_combo.addItems(list(LAYOUTS))
        self.layout_combo.currentTextChanged.connect(self.on_layout_changed)
        bar.addWidget(self.layout_combo)
        bar.addWidget(self.muted_label("PRICE"))
        self.type_combo = QComboBox()
        self.type_combo.addItems(["CANDLES", "LINE"])
        self.type_combo.currentTextChanged.connect(self.on_chart_option_changed)
        bar.addWidget(self.type_combo)
        self.ma_boxes = {}
        for window in analysis.MA_WINDOWS:
            box = QCheckBox(f"SMA {window}")
            box.setChecked(window in (50, 200))
            box.setStyleSheet(f"QCheckBox {{ color: {MA_COLORS[window]}; font-weight: 700; }}")
            box.toggled.connect(self.on_chart_option_changed)
            self.ma_boxes[window] = box
            bar.addWidget(box)
        bar.addWidget(self.muted_label("BENCHMARK"))
        self.bench_box = QLineEdit(self.benchmark)
        self.bench_box.setFixedWidth(80)
        self.bench_box.setToolTip("Symbol used in the relative-performance panel (e.g. SPY, QQQ, ^GSPC). Press Enter.")
        self.bench_box.returnPressed.connect(self.on_benchmark_entered)
        bar.addWidget(self.bench_box)
        reset = QPushButton("RESET ZOOM")
        reset.clicked.connect(self.reset_view)
        bar.addWidget(reset)
        self.readout = QLabel("Hover a chart for values")
        self.readout.setObjectName("muted")
        bar.addWidget(self.readout, 1)
        layout.addLayout(bar)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)
        self.grid_host = QWidget()
        self.grid = QGridLayout(self.grid_host)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(7)
        split.addWidget(self.grid_host)
        split.addWidget(self.build_side_column())
        split.setSizes([1000, 360])
        layout.addWidget(split, 1)
        return page

    def build_side_column(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setMinimumWidth(330)
        inner = QWidget()
        col = QVBoxLayout(inner)
        col.setContentsMargins(0, 0, 6, 0)
        col.setSpacing(7)

        profile = TerminalPanel("COMPANY PROFILE")
        self.profile_labels = {}
        for key in ["Sector", "Industry", "Exchange", "Country", "Employees", "Website"]:
            row = QHBoxLayout()
            label = QLabel(key.upper())
            label.setObjectName("muted")
            value = QLabel("—")
            value.setWordWrap(True)
            value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            row.addWidget(label)
            row.addStretch()
            row.addWidget(value)
            profile.layout_box.addLayout(row)
            self.profile_labels[key] = value
        profile.layout_box.addWidget(self.muted_label("BUSINESS SUMMARY"))
        self.summary = QLabel("Company description will appear here.")
        self.summary.setWordWrap(True)
        self.summary.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.summary.setObjectName("muted")
        profile.layout_box.addWidget(self.summary)
        col.addWidget(profile)

        signals = TerminalPanel("TECHNICAL SNAPSHOT")
        self.signal_box = QVBoxLayout()
        self.signal_box.setSpacing(5)
        signals.layout_box.addLayout(self.signal_box)
        hint = QLabel("Descriptive readings of standard indicators. Not a recommendation.")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        signals.layout_box.addWidget(hint)
        col.addWidget(signals)

        perf = TerminalPanel("PERFORMANCE")
        self.performance_table = QTableWidget(0, 4)
        self.performance_table.setHorizontalHeaderLabels(["PERIOD", "STOCK", "BENCH", "EXCESS"])
        self.performance_table.verticalHeader().setVisible(False)
        self.performance_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.performance_table.setShowGrid(False)
        self.performance_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.performance_table.setMinimumHeight(230)
        perf.layout_box.addWidget(self.performance_table)
        col.addWidget(perf)
        col.addStretch()
        scroll.setWidget(inner)
        return scroll

    # fundamentals page 
    def build_fundamentals_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.stmt_combo = QComboBox()
        for label, _ in self.STATEMENTS:
            self.stmt_combo.addItem(label)
        self.stmt_combo.currentIndexChanged.connect(self.render_fundamentals_view)
        bar.addWidget(self.stmt_combo)
        self.freq_combo = QComboBox()
        self.freq_combo.addItems(["ANNUAL", "QUARTERLY"])
        self.freq_combo.currentIndexChanged.connect(self.render_fundamentals_view)
        bar.addWidget(self.freq_combo)
        self.show_all = QCheckBox("ALL LINE ITEMS")
        self.show_all.toggled.connect(self.render_fundamentals_view)
        bar.addWidget(self.show_all)
        bar.addStretch()
        self.fund_status = QLabel("")
        self.fund_status.setObjectName("muted")
        bar.addWidget(self.fund_status)
        layout.addLayout(bar)

        metrics_row = QHBoxLayout()
        metrics_row.setSpacing(7)
        self.metric_labels = {}
        self.metric_specs = {
            "VALUATION": [
                ("Market Cap", lambda i: fmt_large(i.get("marketCap"))),
                ("Enterprise Value", lambda i: fmt_large(i.get("enterpriseValue"))),
                ("P/E (TTM)", lambda i: fmt_mult(i.get("trailingPE"))),
                ("Forward P/E", lambda i: fmt_mult(i.get("forwardPE"))),
                ("PEG", lambda i: fmt_mult(i.get("trailingPegRatio") or i.get("pegRatio"))),
                ("Price / Sales", lambda i: fmt_mult(i.get("priceToSalesTrailing12Months"))),
                ("Price / Book", lambda i: fmt_mult(i.get("priceToBook"))),
                ("EV / EBITDA", lambda i: fmt_mult(i.get("enterpriseToEbitda"))),
            ],
            "PROFITABILITY & GROWTH": [
                ("Gross Margin", lambda i: fmt_frac(i.get("grossMargins"))),
                ("Operating Margin", lambda i: fmt_frac(i.get("operatingMargins"))),
                ("Net Margin", lambda i: fmt_frac(i.get("profitMargins"))),
                ("EBITDA Margin", lambda i: fmt_frac(i.get("ebitdaMargins"))),
                ("Return on Equity", lambda i: fmt_frac(i.get("returnOnEquity"))),
                ("Return on Assets", lambda i: fmt_frac(i.get("returnOnAssets"))),
                ("Revenue Growth", lambda i: fmt_frac(i.get("revenueGrowth"), signed=True)),
                ("Earnings Growth", lambda i: fmt_frac(i.get("earningsGrowth"), signed=True)),
            ],
            "CASH FLOW & BALANCE SHEET": [
                ("Free Cash Flow", lambda i: fmt_large(i.get("freeCashflow"))),
                ("Operating Cash Flow", lambda i: fmt_large(i.get("operatingCashflow"))),
                ("Total Cash", lambda i: fmt_large(i.get("totalCash"))),
                ("Total Debt", lambda i: fmt_large(i.get("totalDebt"))),
                # Yahoo reports debtToEquity as a percentage (150 = 1.5x).
                ("Debt / Equity", lambda i: "N/A" if val_num(i.get("debtToEquity")) is None
                 else f"{val_num(i.get('debtToEquity')) / 100:.2f}x"),
                ("Current Ratio", lambda i: "N/A" if val_num(i.get("currentRatio")) is None
                 else f"{val_num(i.get('currentRatio')):.2f}"),
                ("EPS (TTM)", lambda i: fmt_money(i.get("trailingEps"))),
                ("Dividend Yield", lambda i: fmt_pct(ds.dividend_yield_percent(i))),
            ],
        }
        for title, specs in self.metric_specs.items():
            panel = TerminalPanel(title)
            grid = QGridLayout()
            grid.setHorizontalSpacing(10)
            grid.setVerticalSpacing(4)
            for idx, (name, _fn) in enumerate(specs):
                r, c = idx % 4, (idx // 4) * 3
                label = QLabel(name.upper())
                label.setObjectName("smallMetric")
                value = QLabel("—")
                value.setObjectName("metricValue")
                value.setAlignment(Qt.AlignmentFlag.AlignRight)
                grid.addWidget(label, r, c)
                grid.addWidget(value, r, c + 1)
                self.metric_labels[(title, name)] = value
            grid.setColumnMinimumWidth(2, 14)
            grid.setColumnStretch(0, 1)
            grid.setColumnStretch(3, 1)
            panel.layout_box.addLayout(grid)
            metrics_row.addWidget(panel, 1)
        layout.addLayout(metrics_row)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)
        table_panel = TerminalPanel("FINANCIAL STATEMENT")
        self.stmt_title = table_panel.title_label
        self.fund_table = QTableWidget(0, 1)
        self.fund_table.verticalHeader().setVisible(False)
        self.fund_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.fund_table.setAlternatingRowColors(True)
        self.fund_table.setShowGrid(False)
        table_panel.layout_box.addWidget(self.fund_table, 1)
        split.addWidget(table_panel)

        charts = QWidget()
        charts_layout = QVBoxLayout(charts)
        charts_layout.setContentsMargins(0, 0, 0, 0)
        charts_layout.setSpacing(7)
        fin_panel = TerminalPanel("REVENUE / NET INCOME / FREE CASH FLOW")
        self.fin_legend = QLabel(
            f'<span style="color:{AMBER}">■ REVENUE</span>&nbsp;&nbsp;'
            f'<span style="color:{BLUE}">■ NET INCOME</span>&nbsp;&nbsp;'
            f'<span style="color:{GREEN}">■ FREE CASH FLOW</span>')
        fin_panel.layout_box.addWidget(self.fin_legend)
        self.fin_chart = pg.PlotWidget()
        style_chart(self.fin_chart)
        fin_panel.layout_box.addWidget(self.fin_chart, 1)
        charts_layout.addWidget(fin_panel, 3)
        margin_panel = TerminalPanel("MARGINS (%)")
        self.margin_chart = pg.PlotWidget()
        style_chart(self.margin_chart)
        add_legend(self.margin_chart)
        margin_panel.layout_box.addWidget(self.margin_chart, 1)
        charts_layout.addWidget(margin_panel, 2)
        split.addWidget(charts)
        split.setSizes([760, 520])
        layout.addWidget(split, 1)
        return page

    @staticmethod
    def muted_label(text):
        label = QLabel(text)
        label.setObjectName("muted")
        return label

    def apply_saved_settings(self):
        s = self.settings
        layout_name = s.get("layout") if s.get("layout") in LAYOUTS else DEFAULT_LAYOUT
        for widget in (self.layout_combo, self.type_combo, *self.ma_boxes.values(), self.period):
            widget.blockSignals(True)
        self.layout_combo.setCurrentText(layout_name)
        if s.get("chart_type") in ("CANDLES", "LINE"):
            self.type_combo.setCurrentText(s["chart_type"])
        for window, box in self.ma_boxes.items():
            saved = s.get("mas", {}).get(str(window))
            if isinstance(saved, bool):
                box.setChecked(saved)
        if str(s.get("period", "")).lower() in [p.lower() for p in analysis.PERIODS]:
            self.period.setCurrentText(str(s["period"]).lower())
        for widget in (self.layout_combo, self.type_combo, *self.ma_boxes.values(), self.period):
            widget.blockSignals(False)
        self.rebuild_grid(layout_name)

    # navigation 
    def pages_set_index(self, index):
        self.pages.setCurrentIndex(index)

    #  background 
    def start_job(self, pool, kind, key, fn, *args):
        serial = next(self._serial)
        worker = Worker(fn, args, (kind, key, serial))
        worker.signals.done.connect(self.on_job_done)
        worker.signals.failed.connect(self.on_job_failed)
        self._jobs[serial] = worker
        pool.start(worker)
        return serial

    @Slot(object, object)
    def on_job_done(self, token, result):
        kind, key, serial = token
        self._jobs.pop(serial, None)
        if kind == "research":
            self.handle_research(serial, key, result)
        elif kind == "fund":
            self.handle_fundamentals(serial, key, result)
        elif kind == "quote":
            self.handle_quote(key, result)

    @Slot(object, str)
    def on_job_failed(self, token, message):
        kind, key, serial = token
        self._jobs.pop(serial, None)
        if kind == "research":
            self.handle_research_error(serial, key, message)
        elif kind == "fund":
            if serial == self.fund_serial:
                self.fund_data = None
                self.fund_status.setText(f"FUNDAMENTALS UNAVAILABLE: {message}"[:140])
                self.render_fundamentals_view()
        elif kind == "quote":
            self.handle_quote(key, ds.Quote(key, error=message))

    #  loading a ticker 
    def load_ticker_from_search(self):
        query = self.search_box.text().strip()
        if not query:
            return
        ticker = query.upper()
        
        if " " in query or len(query) > 6:
            self.status_text.setText(f"SEARCHING: {query}")
            QApplication.processEvents()
            try:
                found = core.search_ticker(query)
            except Exception as error:  
                QMessageBox.warning(self, "Company search failed", str(error))
                return
            if not found:
                QMessageBox.information(
                    self, "No result",
                    "No company match found. Check the Alpha Vantage API key and usage limits, "
                    "or enter a ticker symbol directly."
                )
                return
            ticker = found.strip().upper()
        self.load_ticker(ticker)

    def load_ticker(self, ticker, force=False):
        ticker = ticker.strip().upper()
        if not ticker:
            return
        self.current_ticker = ticker
        self.search_box.setText(ticker)
        self.status_text.setText(f"LOADING {ticker}…")
        self.clock_status.setText("● LOADING")
        self.clock_status.setStyleSheet(f"color: {AMBER}; font-weight: 800;")
        self.mark_active_row()
        self.research_serial = self.start_job(
            self.research_pool, "research", ticker,
            ds.load_research_bundle, ticker, self.benchmark, force)
        self.fund_data = None
        self.fund_status.setText("LOADING FUNDAMENTALS…")
        self.render_fundamentals_view()
        self.fund_serial = self.start_job(
            self.research_pool, "fund", ticker, ds.load_fundamentals, ticker, force)

    def handle_research(self, serial, ticker, bundle):
        if serial != self.research_serial:
            return  
        self.bundle = bundle
        self.apply_bundle()
        self.render_view()
        frame = bundle["frame"]
        self.clock_status.setText("● DATA RECEIVED")
        self.clock_status.setStyleSheet(f"color: {GREEN}; font-weight: 800;")
        self.status_text.setText(f"{ticker} LOADED · {len(self.ctx.view)} SESSIONS IN VIEW · {len(frame)} IN HISTORY")

    def handle_research_error(self, serial, ticker, message):
        if serial != self.research_serial:
            return
        self.clock_status.setText("● DATA UNAVAILABLE")
        self.clock_status.setStyleSheet(f"color: {RED}; font-weight: 800;")
        self.status_text.setText(f"DATA UNAVAILABLE: {ticker}")
        QMessageBox.warning(
            self, "Research data unavailable",
            f"Could not load {ticker}.\n\n{message}\n\n"
            "Verify the symbol and internet connection. Some symbols or metrics may not be supported."
        )

    def handle_fundamentals(self, serial, ticker, data):
        if serial != self.fund_serial:
            return
        self.fund_data = data
        problems = len(data.get("errors", []))
        has_any = any(not df.empty for freq in ("annual", "quarterly") for df in data[freq].values())
        if not has_any:
            self.fund_status.setText("NO FINANCIAL STATEMENTS AVAILABLE (ETF, FUND OR NEW LISTING?)")
        else:
            self.fund_status.setText(f"{ticker} · FINANCIALS LOADED" + (f" · {problems} PARTIAL ERROR(S)" if problems else ""))
        self.render_fundamentals_view()

    def refresh_all(self):
        ds.clear_cache()
        self.refresh_quotes()
        self.load_ticker(self.current_ticker, force=True)

    #  header 
    def apply_bundle(self):
        info, frame = self.bundle["info"], self.bundle["frame"]
        self.update_quote(info, frame)
        self.update_stats(info, frame)
        self.update_profile(info)
        self.update_signals(frame)
        self.update_metric_cards(info)

    def update_quote(self, info, frame):
        ticker = self.current_ticker
        name = info.get("longName") or info.get("shortName") or ticker
        closes = frame["Close"].dropna()
        price = val_num(info.get("currentPrice"))
        if price is None:
            price = val_num(info.get("regularMarketPrice"))
        if price is None and not closes.empty:
            price = val_num(closes.iloc[-1])
        prev = val_num(info.get("regularMarketPreviousClose"))
        if prev is None:
            prev = val_num(info.get("previousClose"))
        if prev is None and len(closes) >= 2:
            prev = val_num(closes.iloc[-2])
        change = price - prev if price is not None and prev is not None else None
        pct = change / prev * 100 if change is not None and prev else None

        self.company_name.setText(str(name))
        self.ticker_label.setText(
            f"{ticker}  ·  {info.get('quoteType', 'EQUITY')}  ·  {info.get('exchange', 'EXCHANGE N/A')}")
        self.price_label.setText(fmt_money(price))
        if change is None:
            self.change_label.setText("CHANGE N/A")
            self.change_label.setStyleSheet(f"color: {MUTED};")
        else:
            self.change_label.setText(f"{change:+.2f}  ({pct:+.2f}%)")
            self.change_label.setStyleSheet(f"color: {GREEN if change >= 0 else RED}; font-weight: 800;")
        self.market_state.setText("LATEST AVAILABLE QUOTE\nMAY BE DELAYED")

    def update_stats(self, info, frame):
        last_year = frame.tail(252)
        volume = val_num(info.get("volume"))
        if volume is None and not frame.empty:
            volume = val_num(frame["Volume"].iloc[-1])
        avg_volume = val_num(info.get("averageVolume"))
        if avg_volume is None and len(frame) >= 20:
            avg_volume = val_num(frame["Volume"].tail(63).mean())
        high = val_num(info.get("fiftyTwoWeekHigh"))
        if high is None and not last_year.empty:
            high = val_num(last_year["High"].max())
        low = val_num(info.get("fiftyTwoWeekLow"))
        if low is None and not last_year.empty:
            low = val_num(last_year["Low"].min())
        day_low, day_high = val_num(info.get("dayLow")), val_num(info.get("dayHigh"))
        if (day_low is None or day_high is None) and not frame.empty:
            day_low, day_high = val_num(frame["Low"].iloc[-1]), val_num(frame["High"].iloc[-1])
        stats = {
            "Market Cap": fmt_large(info.get("marketCap")),
            "Volume": "N/A" if volume is None else f"{volume:,.0f}",
            "Avg Volume": "N/A" if avg_volume is None else f"{avg_volume:,.0f}",
            "P/E (TTM)": fmt_mult(info.get("trailingPE")),
            "52W High": fmt_money(high),
            "52W Low": fmt_money(low),
            "Day Range": f"{fmt_money(day_low)} – {fmt_money(day_high)}" if day_low is not None and day_high is not None else "N/A",
            "Dividend Yield": fmt_pct(ds.dividend_yield_percent(info)),
        }
        for key, value in stats.items():
            self.stat_values[key].setText(value)

    def update_profile(self, info):
        employees = val_num(info.get("fullTimeEmployees"))
        profile = {
            "Sector": info.get("sector", "N/A"),
            "Industry": info.get("industry", "N/A"),
            "Exchange": info.get("fullExchangeName") or info.get("exchange") or "N/A",
            "Country": info.get("country", "N/A"),
            "Employees": f"{int(employees):,}" if employees is not None else "N/A",
            "Website": info.get("website", "N/A"),
        }
        for key, value in profile.items():
            self.profile_labels[key].setText(str(value))
        summary = info.get("longBusinessSummary") or "Business summary is not available from the current data source."
        if len(summary) > 1250:
            summary = summary[:1247].rsplit(" ", 1)[0] + "…"
        self.summary.setText(summary)

    def update_signals(self, frame):
        clear_layout(self.signal_box)
        for label, text, tone in analysis.technical_snapshot(frame):
            row = QHBoxLayout()
            name = QLabel(label.upper())
            name.setObjectName("smallMetric")
            value = QLabel(text)
            value.setWordWrap(True)
            value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            value.setStyleSheet(f"color: {tone_color(tone)}; font-weight: 700;")
            row.addWidget(name)
            row.addWidget(value, 1)
            self.signal_box.addLayout(row)

    def update_metric_cards(self, info):
        for title, specs in self.metric_specs.items():
            for name, fn in specs:
                try:
                    text = fn(info)
                except Exception:  
                    text = "N/A"
                self.metric_labels[(title, name)].setText(text)

    #  chart workspace 
    def rebuild_grid(self, name):
        preset = LAYOUTS[name]
        for panel in self.panels:
            self.grid.removeWidget(panel)
            panel.setParent(None)
            panel.deleteLater()
        self.panels = []
        for i in range(8):
            self.grid.setRowStretch(i, 0)
            self.grid.setColumnStretch(i, 0)
        for kind, row, col, row_span, col_span in preset["cells"]:
            panel = ChartPanel(kind)
            panel.kind_changed.connect(self.on_panel_kind_changed)
            panel.hovered.connect(self.on_hover)
            self.grid.addWidget(panel, row, col, row_span, col_span)
            self.panels.append(panel)
        for i, weight in enumerate(preset["rows"]):
            self.grid.setRowStretch(i, weight)
        for i, weight in enumerate(preset["cols"]):
            self.grid.setColumnStretch(i, weight)
        for panel in self.panels[1:]:
            panel.plot.setXLink(self.panels[0].plot)

    def on_layout_changed(self, name):
        if name not in LAYOUTS:
            return
        self.rebuild_grid(name)
        self.render_view()
        self.save_settings()

    def on_panel_kind_changed(self, panel):
        if self.ctx is not None:
            panel.render(self.ctx)
        self.save_settings()

    def on_chart_option_changed(self, *_):
        self.save_settings()
        self.render_view()

    def on_period_changed(self, *_):
        self.save_settings()
        self.render_view()

    def on_benchmark_entered(self):
        symbol = self.bench_box.text().strip().upper()
        if not symbol:
            return
        self.benchmark = symbol
        self.bench_box.setText(symbol)
        self.save_settings()
        self.load_ticker(self.current_ticker)

    def build_context(self):
        bundle = self.bundle
        frame = bundle["frame"]
        view = analysis.slice_period(frame, self.period.currentText())
        bench_full = analysis.align_benchmark(frame.index, bundle["bench_close"])
        bench_view = bench_full.loc[view.index] if bench_full is not None else None
        return ChartContext(
            ticker=bundle["symbol"], benchmark=bundle["benchmark"], view=view, bench=bench_view,
            bench_error=bundle["bench_error"],
            candles=self.type_combo.currentText() == "CANDLES",
            mas={w: box.isChecked() for w, box in self.ma_boxes.items()},
            period=self.period.currentText(),
        ), bench_full

    def render_view(self):
        """Redraw everything that depends on period / chart options (no network)."""
        if not self.bundle:
            return
        self.ctx, bench_full = self.build_context()
        for panel in self.panels:
            panel.render(self.ctx)
        self.reset_view()
        self.update_performance(self.bundle["frame"], bench_full)

    def reset_view(self):
        if not self.panels or self.ctx is None:
            return
        n = len(self.ctx.view)
        self.panels[0].plot.setXRange(-1, n, padding=0.0)
        for panel in self.panels:
            if panel.kind != "RSI":
                vb = panel.plot.getViewBox()
                vb.enableAutoRange(axis=pg.ViewBox.YAxis, enable=True)
                vb.setAutoVisible(y=True)

    def on_hover(self, index):
        if self.ctx is None:
            return
        view = self.ctx.view
        if not 0 <= index < len(view):
            return
        for panel in self.panels:
            panel.vline.setPos(index)
        row = view.iloc[index]
        parts = [view.index[index].strftime("%a %d %b %Y"),
                 f"O {row['Open']:.2f}  H {row['High']:.2f}  L {row['Low']:.2f}  C {row['Close']:.2f}",
                 f"VOL {row['Volume'] / 1e6:.1f}M"]
        if pd.notna(row["RSI"]):
            parts.append(f"RSI {row['RSI']:.1f}")
        if pd.notna(row["MACD"]):
            parts.append(f"MACD {row['MACD']:+.2f}")
        self.readout.setText("   ·   ".join(parts))

    def update_performance(self, frame, bench_full):
        table = self.performance_table
        table.setRowCount(0)
        close = frame["Close"].dropna()
        if len(close) < 2:
            return
        bench_close = bench_full.dropna() if bench_full is not None else None
        table.setHorizontalHeaderLabels(["PERIOD", "STOCK", self.benchmark[:8], "EXCESS"])

        rows = []
        view = self.ctx.view["Close"].dropna()
        if len(view) >= 2 and view.iloc[0]:
            stock_ret = (float(view.iloc[-1]) / float(view.iloc[0]) - 1.0) * 100.0
            bench_ret = None
            if self.ctx.bench is not None:
                b = self.ctx.bench.dropna()
                if len(b) >= 2 and b.iloc[0]:
                    bench_ret = (float(b.iloc[-1]) / float(b.iloc[0]) - 1.0) * 100.0
            rows.append((f"Selected ({self.period.currentText()})", stock_ret, bench_ret))
        rows.extend(analysis.trailing_returns(close, bench_close, self.period.currentText()))

        table.setRowCount(len(rows))
        for r, (label, stock_ret, bench_ret) in enumerate(rows):
            table.setItem(r, 0, QTableWidgetItem(label))
            table.setItem(r, 1, right_item(fmt_pct(stock_ret, True),
                                           GREEN if (stock_ret or 0) >= 0 else RED))
            if bench_ret is None or stock_ret is None:
                table.setItem(r, 2, right_item("—", MUTED))
                table.setItem(r, 3, right_item("—", MUTED))
            else:
                excess = stock_ret - bench_ret
                table.setItem(r, 2, right_item(fmt_pct(bench_ret, True), MUTED))
                table.setItem(r, 3, right_item(fmt_pct(excess, True), GREEN if excess >= 0 else RED))

    #  fundamental
    def render_fundamentals_view(self, *_):
        self.render_statement_table()
        self.render_fundamental_charts()

    def current_statement(self):
        quarterly = self.freq_combo.currentText() == "QUARTERLY"
        kind = self.STATEMENTS[self.stmt_combo.currentIndex()][1]
        if self.fund_data is None:
            return quarterly, kind, None
        return quarterly, kind, self.fund_data["quarterly" if quarterly else "annual"].get(kind)

    def render_statement_table(self):
        table = self.fund_table
        quarterly, kind, df = self.current_statement()
        self.stmt_title.setText(
            f"{self.STATEMENTS[self.stmt_combo.currentIndex()][0]}  ·  "
            f"{'QUARTERLY' if quarterly else 'ANNUAL'}  ·  {self.current_ticker}")
        table.setRowCount(0)
        if self.fund_data is None:
            table.setColumnCount(1)
            table.setHorizontalHeaderLabels(["LOADING FUNDAMENTALS…"])
            table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
            return
        cols, rows = analysis.statement_table(df, kind, self.show_all.isChecked(), quarterly)
        if not rows:
            table.setColumnCount(1)
            table.setHorizontalHeaderLabels(["NO DATA AVAILABLE FOR THIS STATEMENT / FREQUENCY"])
            table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
            return
        table.setColumnCount(1 + len(cols))
        table.setHorizontalHeaderLabels(
            ["LINE ITEM"] + [analysis.period_label(c, quarterly).upper() for c in cols])
        hh = table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for c in range(1, 1 + len(cols)):
            hh.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        table.setRowCount(len(rows))
        for r, (label, values, style) in enumerate(rows):
            name_item = QTableWidgetItem(label)
            if style == "pct":
                name_item.setForeground(QColor(MUTED))
                font = name_item.font()
                font.setItalic(True)
                name_item.setFont(font)
            table.setItem(r, 0, name_item)
            for c, value in enumerate(values, start=1):
                number = val_num(value)
                color = None
                if number is not None and number < 0:
                    color = RED
                elif style == "pct" and number is not None:
                    color = GREEN
                table.setItem(r, c, right_item(fmt_statement_value(value, style), color,
                                               bold=label in ("Total Revenue", "Net Income", "Free Cash Flow",
                                                              "Total Assets", "Operating Cash Flow")))

    def render_fundamental_charts(self):
        self.fin_chart.clear()
        self.margin_chart.clear()
        quarterly = self.freq_combo.currentText() == "QUARTERLY"
        data = self.fund_data["quarterly" if quarterly else "annual"] if self.fund_data else None
        income = data["income"] if data else pd.DataFrame()
        if income is None or income.empty:
            message = "LOADING…" if self.fund_data is None else "NO INCOME STATEMENT DATA"
            show_no_data(self.fin_chart, message)
            show_no_data(self.margin_chart, message)
            return
        income = income.iloc[:, -8:]
        labels = [pd.Timestamp(c).strftime("%b %y") for c in income.columns]
        xs = np.arange(len(labels), dtype=float)
        ticks = [list(zip(xs.tolist(), labels))]

        revenue = analysis.find_row(income, "Total Revenue", "Operating Revenue")
        net = analysis.find_row(income, "Net Income", "Net Income Common Stockholders")
        cash = data["cashflow"]
        fcf = analysis.find_row(cash, "Free Cash Flow") if not cash.empty else None
        if fcf is None and not cash.empty:
            ocf = analysis.find_row(cash, "Operating Cash Flow")
            capex = analysis.find_row(cash, "Capital Expenditure")
            if ocf is not None and capex is not None:
                fcf = ocf + capex      # Yahoo reports capex as a negative number
        if fcf is not None:
            fcf = fcf.reindex(income.columns)

        series = [(revenue, AMBER), (net, BLUE), (fcf, GREEN)]
        magnitudes = [np.nanmax(np.abs(s.to_numpy(float))) for s, _ in series
                      if s is not None and np.isfinite(s.to_numpy(float)).any()]
        top = max(magnitudes) if magnitudes else 0
        scale, unit = (1e9, "B") if top >= 1e9 else ((1e6, "M") if top >= 1e6 else (1.0, ""))
        offsets = (-0.27, 0.0, 0.27)
        for (s, color), offset in zip(series, offsets):
            if s is None:
                continue
            values = s.to_numpy(float) / scale
            mask = np.isfinite(values)
            if mask.any():
                self.fin_chart.addItem(pg.BarGraphItem(
                    x=xs[mask] + offset, height=values[mask], width=0.25,
                    brush=pg.mkBrush(color), pen=pg.mkPen(None)))
        self.fin_chart.addItem(pg.InfiniteLine(pos=0, angle=0, pen=pg.mkPen(BORDER)))
        self.fin_chart.getAxis("bottom").setTicks(ticks)
        self.fin_chart.setLabel("left", f"${unit}" if unit else "$", color=MUTED)

        colors = {"Gross Margin %": GREEN, "Operating Margin %": BLUE, "Net Margin %": AMBER}
        drew = False
        for label, margin in analysis.derived_income_rows(income, quarterly)[1:]:
            values = margin.to_numpy(float)
            if np.isfinite(values).any():
                self.margin_chart.plot(xs, values, pen=pg.mkPen(colors.get(label, TEXT), width=2),
                                       symbol="o", symbolSize=6, symbolBrush=colors.get(label, TEXT),
                                       symbolPen=None, name=label.replace(" %", ""), connect="finite")
                drew = True
        self.margin_chart.getAxis("bottom").setTicks(ticks)
        self.margin_chart.setLabel("left", "%", color=MUTED)
        if not drew:
            show_no_data(self.margin_chart, "MARGINS UNAVAILABLE")

    #  watchlist and quotes
    def populate_watchlist(self):
        self.watch_table.setRowCount(len(self.watchlist))
        for row, ticker in enumerate(self.watchlist):
            self.set_watch_row(row, ticker)
        self.mark_active_row()

    def set_watch_row(self, row, ticker):
        quote = self.quotes.get(ticker)
        last = chg = pct = "—"
        color, tooltip = None, ""
        if quote is not None and quote.price is not None:
            last = fmt_money(quote.price)
            if quote.change is not None:
                chg = f"{quote.change:+.2f}"
                pct = f"{quote.pct:+.2f}%"
                color = GREEN if quote.change >= 0 else RED
        elif quote is not None and quote.error:
            pct, color, tooltip = "ERR", MUTED, quote.error
        elif ticker in self.quote_inflight:
            pct, color = "…", MUTED
        self.watch_table.setItem(row, 0, QTableWidgetItem(ticker))
        self.watch_table.setItem(row, 1, right_item(last))
        self.watch_table.setItem(row, 2, right_item(chg, color))
        self.watch_table.setItem(row, 3, right_item(pct, color, bold=True))
        if tooltip:
            for col in range(4):
                self.watch_table.item(row, col).setToolTip(tooltip)

    def row_for(self, ticker):
        for row in range(self.watch_table.rowCount()):
            item = self.watch_table.item(row, 0)
            if item and item.text() == ticker:
                return row
        return -1

    def mark_active_row(self):
        for row in range(self.watch_table.rowCount()):
            item = self.watch_table.item(row, 0)
            if item:
                active = item.text() == self.current_ticker
                item.setForeground(QColor(AMBER if active else TEXT))
                font = item.font()
                font.setBold(active)
                item.setFont(font)

    def refresh_quotes(self, symbols=None):
        symbols = symbols if isinstance(symbols, (list, tuple)) else self.watchlist
        for symbol in symbols:
            if symbol in self.quote_inflight:
                continue
            self.quote_inflight.add(symbol)
            self.start_job(self.quote_pool, "quote", symbol, ds.fetch_quote, symbol)
        self.quote_time.setText("QUOTES …")

    def handle_quote(self, symbol, quote):
        self.quote_inflight.discard(symbol)
        self.quotes[symbol] = quote
        row = self.row_for(symbol)
        if row >= 0:
            self.set_watch_row(row, symbol)
            self.mark_active_row()
        if not self.quote_inflight:
            self.quote_time.setText("QUOTES " + time.strftime("%H:%M:%S"))

    def on_auto_refresh_toggled(self, checked):
        if checked:
            self.quote_timer.start()
            self.refresh_quotes()
        else:
            self.quote_timer.stop()

    def open_watchlist_row(self, row, column):
        item = self.watch_table.item(row, 0)
        if item:
            self.load_ticker(item.text())

    def add_ticker(self):
        ticker, ok = QInputDialog.getText(self, "Add to Watchlist", "Ticker symbol:")
        if not ok:
            return
        ticker = ticker.strip().upper()
        if not ticker:
            return
        if ticker in self.watchlist:
            QMessageBox.information(self, "Watchlist", f"{ticker} is already in your watchlist.")
            return
        self.watchlist.append(ticker)
        core.save_watchlist(self.watchlist)
        self.populate_watchlist()
        self.refresh_quotes([ticker])
        self.status_text.setText(f"ADDED {ticker} TO WATCHLIST")

    def remove_ticker(self):
        row = self.watch_table.currentRow()
        if row < 0:
            QMessageBox.information(self, "Watchlist", "Select a ticker in the left watchlist first.")
            return
        item = self.watch_table.item(row, 0)
        if not item:
            return
        ticker = item.text()
        self.watchlist = [symbol for symbol in self.watchlist if symbol != ticker]
        self.quotes.pop(ticker, None)
        core.save_watchlist(self.watchlist)
        self.populate_watchlist()
        self.status_text.setText(f"REMOVED {ticker} FROM WATCHLIST")

    def closeEvent(self, event):
        self.quote_timer.stop()
        self.quote_pool.clear()
        self.research_pool.clear()
        self.quote_pool.waitForDone(1500)
        self.research_pool.waitForDone(1500)
        super().closeEvent(event)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setFont(QFont("Consolas", 10))
    pg.setConfigOptions(antialias=False)
    window = StockResearchTerminal()
    window.show()
    sys.exit(app.exec())
