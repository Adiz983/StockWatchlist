
import os
import re
import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QInputDialog,
    QMessageBox,
    QHeaderView,
)

# Keep JSON files in the same folder as this application.
PROJECT_DIR = Path(__file__).resolve().parent
os.chdir(PROJECT_DIR)

# Reuse your existing stock functions.
import main as core


def number_or_none(value):
    """Convert numbers such as '$339.50' or '2.5%' to floats."""
    if value is None:
        return None

    if isinstance(value, (int, float)):
        return float(value)

    cleaned = re.sub(r"[^0-9.\-]", "", str(value))

    try:
        return float(cleaned)
    except (ValueError, TypeError):
        return None


class StockWatchlistApp(QMainWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("StockWatchlist")
        self.resize(1150, 700)

        self.watchlist = core.load_watchlist()
        self.portfolio = core.load_portfolio()

        self.setStyleSheet("""
            QMainWindow, QWidget {
                background-color: #111827;
                color: #F3F4F6;
                font-size: 13px;
            }

            QLabel#heading {
                font-size: 26px;
                font-weight: bold;
            }

            QLabel#subheading {
                color: #9CA3AF;
            }

            QLabel#summary {
                background-color: #1F2937;
                border-radius: 8px;
                padding: 12px;
                font-size: 15px;
            }

            QPushButton {
                background-color: #2563EB;
                color: white;
                border: none;
                border-radius: 6px;
                padding: 9px 14px;
            }

            QPushButton:hover {
                background-color: #1D4ED8;
            }

            QTableWidget {
                background-color: #1F2937;
                alternate-background-color: #182231;
                gridline-color: #374151;
                border: 1px solid #374151;
                selection-background-color: #374151;
            }

            QHeaderView::section {
                background-color: #273449;
                color: white;
                padding: 9px;
                border: none;
                font-weight: bold;
            }
        """)

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(22, 22, 22, 22)
        layout.setSpacing(14)

        heading = QLabel("StockWatchlist")
        heading.setObjectName("heading")
        layout.addWidget(heading)

        subtitle = QLabel(
            "Your personal stock dashboard and portfolio tracker"
        )
        subtitle.setObjectName("subheading")
        layout.addWidget(subtitle)

        summary_row = QHBoxLayout()

        self.value_label = QLabel("Portfolio value: $0.00")
        self.value_label.setObjectName("summary")

        self.cost_label = QLabel("Total invested: $0.00")
        self.cost_label.setObjectName("summary")

        self.pl_label = QLabel("Unrealized P/L: $0.00")
        self.pl_label.setObjectName("summary")

        summary_row.addWidget(self.value_label)
        summary_row.addWidget(self.cost_label)
        summary_row.addWidget(self.pl_label)
        layout.addLayout(summary_row)

        button_row = QHBoxLayout()

        buttons = [
            ("Add Stock", self.add_stock),
            ("Remove Selected", self.remove_stock),
            ("Search Company", self.search_company),
            ("Record Purchase", self.record_purchase),
            ("Refresh Prices", self.refresh_data),
        ]

        for label, callback in buttons:
            button = QPushButton(label)
            button.clicked.connect(callback)
            button_row.addWidget(button)

        layout.addLayout(button_row)

        self.table = QTableWidget()
        self.table.setColumnCount(8)
        self.table.setHorizontalHeaderLabels([
            "Ticker",
            "Company",
            "Latest Price",
            "Day Change",
            "% Change",
            "Shares",
            "Market Value",
            "Unrealized P/L",
        ])
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self.table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers
        )
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        layout.addWidget(self.table)

        self.statusBar().showMessage("Ready")
        self.refresh_data()

    def show_error(self, message):
        QMessageBox.warning(self, "StockWatchlist", message)

    def add_stock(self):
        ticker, ok = QInputDialog.getText(
            self, "Add Stock", "Enter a ticker symbol:"
        )

        if not ok:
            return

        ticker = ticker.strip().upper()

        if not ticker:
            self.show_error("Enter a ticker symbol.")
            return

        if ticker in self.watchlist:
            self.show_error(f"{ticker} is already in your watchlist.")
            return

        self.watchlist.append(ticker)
        core.save_watchlist(self.watchlist)
        self.refresh_data()

    def remove_stock(self):
        row = self.table.currentRow()

        if row < 0:
            self.show_error("Select a stock row first.")
            return

        ticker_item = self.table.item(row, 0)
        if ticker_item is None:
            return

        ticker = ticker_item.text()

        if ticker in self.watchlist:
            self.watchlist.remove(ticker)
            core.save_watchlist(self.watchlist)
            self.refresh_data()
        else:
            self.show_error(
                "That ticker is not in your watchlist. "
                "It may only be in your portfolio."
            )

    def search_company(self):
        query, ok = QInputDialog.getText(
            self,
            "Search Company",
            "Enter a company name, such as Apple:",
        )

        if not ok or not query.strip():
            return

        self.statusBar().showMessage("Searching for company...")
        QApplication.processEvents()

        try:
            ticker = core.search_ticker(query.strip())
        except Exception as error:
            self.show_error(f"Search failed:\n{error}")
            return

        if not ticker:
            self.show_error(
                "No ticker was found. Check your API key, "
                "internet connection, and API request limits."
            )
            self.statusBar().showMessage("Search unsuccessful")
            return

        ticker = ticker.strip().upper()

        if ticker not in self.watchlist:
            answer = QMessageBox.question(
                self,
                "Add Search Result",
                f"Add {ticker} to your watchlist?",
            )

            if answer == QMessageBox.StandardButton.Yes:
                self.watchlist.append(ticker)
                core.save_watchlist(self.watchlist)
                self.refresh_data()

        self.statusBar().showMessage("Search complete")

    def record_purchase(self):
        ticker, ok = QInputDialog.getText(
            self, "Record Purchase", "Stock ticker:"
        )
        if not ok or not ticker.strip():
            return

        ticker = ticker.strip().upper()

        shares, ok = QInputDialog.getDouble(
            self,
            "Record Purchase",
            "Number of shares:",
            1.0,
            0.01,
            100000000,
            4,
        )
        if not ok:
            return

        purchase_price, ok = QInputDialog.getDouble(
            self,
            "Record Purchase",
            "Purchase price per share ($):",
            1.0,
            0.01,
            100000000,
            4,
        )
        if not ok:
            return

        self.portfolio.append({
            "ticker": ticker,
            "shares": shares,
            "purchase_price": purchase_price,
            "latest_price": None,
        })

        core.save_portfolio(self.portfolio)

        if ticker not in self.watchlist:
            self.watchlist.append(ticker)
            core.save_watchlist(self.watchlist)

        self.refresh_data()

        QMessageBox.information(
            self,
            "Purchase Recorded",
            f"Recorded {shares:g} shares of {ticker} "
            f"at ${purchase_price:.2f} per share.",
        )

    def refresh_data(self):
        self.statusBar().showMessage("Loading stock prices...")
        QApplication.processEvents()

        holdings_by_ticker = {}

        for holding in self.portfolio:
            ticker = holding.get("ticker", "").upper()
            holdings_by_ticker.setdefault(ticker, []).append(holding)

        tickers = list(dict.fromkeys(
            self.watchlist + list(holdings_by_ticker.keys())
        ))

        rows = []
        total_market_value = 0.0
        total_cost = 0.0
        total_pl = 0.0
        priced_cost = 0.0

        for ticker in tickers:
            try:
                data = core.get_stock_data(ticker)
            except Exception:
                data = {}

            price = number_or_none(data.get("latest_price"))
            change = number_or_none(data.get("price_change"))
            percent = number_or_none(data.get("percentage_change"))

            shares = sum(
                number_or_none(h.get("shares")) or 0
                for h in holdings_by_ticker.get(ticker, [])
            )

            cost = sum(
                (number_or_none(h.get("shares")) or 0)
                * (number_or_none(h.get("purchase_price")) or 0)
                for h in holdings_by_ticker.get(ticker, [])
            )

            market_value = price * shares if price is not None else None
            pl = market_value - cost if market_value is not None else None

            if market_value is not None:
                total_market_value += market_value
                total_cost += cost
                total_pl += pl
                priced_cost += cost

            rows.append({
                "ticker": ticker,
                "name": data.get("name", ticker),
                "price": price,
                "change": change,
                "percent": percent,
                "shares": shares,
                "market_value": market_value,
                "pl": pl,
            })

        self.table.setRowCount(len(rows))

        for row_index, stock in enumerate(rows):
            values = [
                stock["ticker"],
                stock["name"],
                self.money(stock["price"]),
                self.money(stock["change"]),
                self.percent(stock["percent"]),
                f'{stock["shares"]:g}',
                self.money(stock["market_value"]),
                self.money(stock["pl"]),
            ]

            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setTextAlignment(
                    Qt.AlignmentFlag.AlignVCenter
                    | (
                        Qt.AlignmentFlag.AlignLeft
                        if column in (0, 1)
                        else Qt.AlignmentFlag.AlignRight
                    )
                )

                if column in (3, 4, 7):
                    number = (
                        stock["change"] if column == 3
                        else stock["percent"] if column == 4
                        else stock["pl"]
                    )
                    if number is not None and number > 0:
                        item.setForeground(QColor("#34D399"))
                    elif number is not None and number < 0:
                        item.setForeground(QColor("#F87171"))

                self.table.setItem(row_index, column, item)

        self.value_label.setText(
            f"Portfolio value: {self.money(total_market_value)}"
        )
        self.cost_label.setText(
            f"Invested in priced holdings: {self.money(priced_cost)}"
        )
        self.pl_label.setText(
            f"Unrealized P/L: {self.money(total_pl)}"
        )

        # Keep the latest successfully retrieved prices in portfolio.json.
        for holding in self.portfolio:
            ticker = holding.get("ticker", "").upper()
            matching = next(
                (r for r in rows if r["ticker"] == ticker), None
            )
            if matching and matching["price"] is not None:
                holding["latest_price"] = matching["price"]

        core.save_portfolio(self.portfolio)
        self.statusBar().showMessage(
            "Prices refreshed. Quotes may be delayed by the data provider."
        )

    @staticmethod
    def money(value):
        return "N/A" if value is None else f"${value:,.2f}"

    @staticmethod
    def percent(value):
        return "N/A" if value is None else f"{value:+.2f}%"


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = StockWatchlistApp()
    window.show()
    sys.exit(app.exec())

