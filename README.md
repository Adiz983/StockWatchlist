# Stock Watchlist

A Python equity research terminal for tracking stocks, charting prices, and reading company fundamentals. Built with PySide6, pyqtgraph, and yfinance.

## Features
- Watchlist with live per-symbol price, daily change, and % change (auto-refreshes every 60 seconds)
- Multi-chart workspace: price, volume, relative performance vs a benchmark, RSI, and MACD
- Candlestick or line charts with SMA 20/50/200 overlays
- Technical snapshot with plain-language readings of RSI, MACD, and moving averages
- Financial statements (income, balance sheet, cash flow), annual or quarterly
- Valuation, profitability, and balance sheet metrics
- Watchlist saved to a local JSON file

## Project Structure
- `main.py` - watchlist and portfolio storage, company search
- `stockwatchlist_terminal.py` - the desktop terminal (run this)
- `analysis.py` - indicator calculations (RSI, MACD, moving averages)
- `data_service.py` - quote, price history, and fundamentals fetching

## How to Run
1. Clone this repository:
```sh
   git clone https://github.com/Adiz983/StockWatchlist.git
   cd StockWatchlist
```
2. Install the dependencies:
```sh
   pip install yfinance PySide6 pyqtgraph pandas numpy requests
```
3. (Optional) Set an Alpha Vantage API key to enable company-name search:
```sh
   set ALPHA_VANTAGE_API_KEY=your_key_here
```
4. Run the application:
```sh
   py stockwatchlist_terminal.py
```

## Disclaimer
This is a research tool, not a broker, and nothing in it is investment advice. Quotes come from Yahoo Finance and may be delayed.

## Contributing
Contributions are welcome! Please open an issue or submit a pull request.

## License
This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.