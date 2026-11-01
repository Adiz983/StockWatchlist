# Stock Watchlist

A Python equity research terminal for tracking stocks, charting prices, comparing companies, testing trading rules on historical data, and reading company fundamentals. Built with PySide6, pyqtgraph, and yfinance.

## Features

**Research**
- Watchlist with live per-symbol price, daily change, and % change (auto-refreshes every 60 seconds)
- Multi-chart workspace: price, volume, relative performance vs a benchmark, RSI, and MACD
- Candlestick or line charts with SMA 20/50/200 overlays
- Technical snapshot with plain-language readings of RSI, MACD, and moving averages
- Financial statements (income, balance sheet, cash flow), annual or quarterly
- Valuation, profitability, and balance sheet metrics
- News feed for your watchlist
- Watchlist saved to a local JSON file

**Stock comparison**
- Compare 2-5 companies side by side: revenue growth, margins, valuation ratios, and price performance
- Rebased price chart so companies with different share prices are comparable
- Valuation multiples are shown without ranking; a lower multiple is not automatically better

**Strategy backtester**
- Test SMA crossover, RSI mean reversion, MACD cross, and breakout rules (plus buy & hold) on past prices
- Models commission and slippage, and reports drawdowns and benchmark returns
- Signals trade on the *next* bar, so the test never uses a price it couldn't have seen yet
- In-sample vs out-of-sample results shown separately to expose overfitting
- All results are **simulated**. They are labelled as such in the app and are not real-world performance

**AI research assistant**
- Ask questions about the loaded company's financials, or about your own saved notes
- Answers use only retrieved data, cite the figures they rely on, and list their sources
- Citations and figures are checked against the data, and mismatches are flagged
- Does not predict prices or give investment advice
- Optional: needs a Gemini API key. Without one it returns a plain lookup of matching data

## Project Structure

| File | Purpose |
|---|---|
| `stockwatchlist_terminal.py` | The desktop terminal (run this) |
| `features_ui.py` | Pages for comparison, backtesting, and the assistant |
| `analysis.py` | Indicator calculations (RSI, MACD, moving averages) and statement helpers |
| `data_service.py` | Quote, price history, and fundamentals fetching |
| `news.py` | News parsing, de-duplication, and merging |
| `compare.py` | Comparison metrics and rebased price data |
| `backtest.py` | Backtesting engine, strategies, and performance statistics |
| `research_assistant.py` | Fact building, retrieval, prompting, and answer verification |
| `notes_store.py` | Saved analysis notes (local JSON) |
| `watchlist_core.py` | Watchlist and portfolio storage, company search |
| `test_features.py` | Offline tests for the comparison, backtest, and assistant logic |

## How to Run

1. Clone this repository:

   ```
   git clone https://github.com/Adiz983/StockWatchlist.git
   cd StockWatchlist
   ```

2. Install the dependencies:

   ```
   pip install -r requirements.txt
   ```

3. (Optional) Set an Alpha Vantage API key to enable company-name search:

   ```
   set ALPHA_VANTAGE_API_KEY=your_key_here
   ```

4. (Optional) Enable written answers in the AI assistant with a Gemini API key. Either set an environment variable:

   ```
   set GEMINI_API_KEY=your_key_here
   ```

   or open the assistant page and click **SET API KEY**, which saves it to a local `gemini_key.txt` (ignored by git). Never commit your key.

5. Run the application:

   ```
   py stockwatchlist_terminal.py
   ```

To run the tests: `python test_features.py`

## Privacy

When written answers are enabled, the assistant sends the retrieved facts (including any matching saved notes) to Google's Gemini API. With no key set, nothing is sent anywhere. Your watchlist, notes, and settings stay in local files.

## Disclaimer

This is a research tool, not a broker, and nothing in it is investment advice. Quotes come from Yahoo Finance and may be delayed. Backtest results are hypothetical simulations of past data; they do not predict future returns. AI-generated answers can be wrong; check the cited figures.

## Contributing

Contributions are welcome! Please open an issue or submit a pull request.

## License

This project is licensed under the MIT License - see the [LICENSE](https://github.com/Adiz983/StockWatchlist/blob/main/LICENSE) file for details.
