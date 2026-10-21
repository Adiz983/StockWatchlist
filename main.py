
import json
import os
from pathlib import Path

import requests
import yfinance as yf



# CONFIGURATION


PROJECT_DIR = Path(__file__).resolve().parent
WATCHLIST_FILE = PROJECT_DIR / "watchlist.json"
PORTFOLIO_FILE = PROJECT_DIR / "portfolio.json"

ALPHA_VANTAGE_API_KEY = os.getenv("ALPHA_VANTAGE_API_KEY")



# FILE 


def load_watchlist():
    """Load and validate saved stock tickers."""
    try:
        with open(WATCHLIST_FILE, "r", encoding="utf-8") as file:
            watchlist = json.load(file)

        if not isinstance(watchlist, list):
            print("Warning: watchlist.json must contain a list.")
            return []

        cleaned = []

        for ticker in watchlist:
            if isinstance(ticker, str) and ticker.strip():
                cleaned.append(ticker.strip().upper())

        return list(dict.fromkeys(cleaned))

    except FileNotFoundError:
        return []

    except json.JSONDecodeError:
        print("Warning: watchlist.json is invalid.")
        return []

    except OSError as error:
        print(f"Could not load watchlist: {error}")
        return []


def save_watchlist(watchlist):
    """Save the current watchlist."""
    try:
        cleaned = list(dict.fromkeys(
            ticker.strip().upper()
            for ticker in watchlist
            if isinstance(ticker, str) and ticker.strip()
        ))

        with open(WATCHLIST_FILE, "w", encoding="utf-8") as file:
            json.dump(cleaned, file, indent=4)

    except OSError as error:
        print(f"Could not save watchlist: {error}")


def load_portfolio():
    """Load saved portfolio holdings."""
    try:
        with open(PORTFOLIO_FILE, "r", encoding="utf-8") as file:
            portfolio = json.load(file)

        if not isinstance(portfolio, list):
            print("Warning: portfolio.json must contain a list.")
            return []

        valid_holdings = []

        for holding in portfolio:
            if not isinstance(holding, dict):
                continue

            ticker = holding.get("ticker")
            shares = clean_number(holding.get("shares"))
            purchase_price = clean_number(
                holding.get("purchase_price")
            )

            if (
                isinstance(ticker, str)
                and ticker.strip()
                and shares is not None
                and shares > 0
                and purchase_price is not None
                and purchase_price > 0
            ):
                valid_holding = dict(holding)
                valid_holding["ticker"] = ticker.strip().upper()
                valid_holding["shares"] = shares
                valid_holding["purchase_price"] = purchase_price
                valid_holdings.append(valid_holding)

        return valid_holdings

    except FileNotFoundError:
        return []

    except json.JSONDecodeError:
        print("Warning: portfolio.json is invalid.")
        return []

    except OSError as error:
        print(f"Could not load portfolio: {error}")
        return []


def save_portfolio(portfolio):
    """Save portfolio holdings."""
    try:
        with open(PORTFOLIO_FILE, "w", encoding="utf-8") as file:
            json.dump(portfolio, file, indent=4)

    except OSError as error:
        print(f"Could not save portfolio: {error}")


# NUMBER CONVERSION


def clean_number(value):
    """Convert a numeric value or formatted price to a float."""
    if value is None or isinstance(value, bool):
        return None

    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip()

    if not text or text.upper() in {"N/A", "NONE", "NULL"}:
        return None

    # Handle values such as $1,234.56.
    text = text.replace("$", "").replace(",", "").replace("%", "")
    text = text.strip()

    try:
        return float(text)
    except (ValueError, TypeError):
        return None


def format_money(value):
    """Format a number for terminal display."""
    number = clean_number(value)

    if number is None:
        return "N/A"

    return f"${number:,.2f}"



# STOCK DATA


def get_stock_data(ticker):
    """
    Retrieve recent stock price information.

    Prices are returned as numeric floats, not formatted
    strings, so the desktop interface can calculate values.
    """
    ticker = ticker.strip().upper()

    try:
        stock = yf.Ticker(ticker)
        history = stock.history(period="5d", auto_adjust=False)

        if history.empty:
            raise ValueError("No historical price data was returned.")

        closes = history["Close"].dropna()

        if closes.empty:
            raise ValueError("No valid closing prices were found.")

        latest_close = float(closes.iloc[-1])

        if not latest_close > 0:
            raise ValueError("The latest closing price is invalid.")

        if len(closes) >= 2:
            previous_close = float(closes.iloc[-2])
            price_change = latest_close - previous_close

            percentage_change = (
                (price_change / previous_close) * 100
                if previous_close != 0
                else None
            )
        else:
            price_change = None
            percentage_change = None

        try:
            info = stock.info or {}
            name = (
                info.get("longName")
                or info.get("shortName")
                or ticker
            )
        except Exception:
            name = ticker

        return {
            "name": name,
            "ticker": ticker,
            "latest_price": latest_close,
            "price_change": price_change,
            "percentage_change": percentage_change,
        }

    except Exception as error:
        print(f"Could not retrieve data for {ticker}: {error}")

        return {
            "name": ticker,
            "ticker": ticker,
            "latest_price": None,
            "price_change": None,
            "percentage_change": None,
        }


# COMPANY SEARCH


def search_ticker(query, interactive=False):
    """
    Search Alpha Vantage for a company.

    Returns the selected ticker symbol, or None if no result
    is available. The desktop app uses the first match by
    default; the terminal menu can offer a selection.
    """
    if not ALPHA_VANTAGE_API_KEY:
        print(
            "Company search needs an API key. Set the "
            "ALPHA_VANTAGE_API_KEY environment variable."
        )
        return None

    try:
        response = requests.get(
            "https://www.alphavantage.co/query",
            params={
                "function": "SYMBOL_SEARCH",
                "keywords": query.strip(),
                "apikey": ALPHA_VANTAGE_API_KEY,
            },
            timeout=15,
        )
        response.raise_for_status()
        data = response.json()

    except requests.RequestException as error:
        print(f"Company search request failed: {error}")
        return None

    if "Error Message" in data or "Note" in data or "Information" in data:
        print("Search unavailable. Check the API response or usage limits.")
        return None

    matches = data.get("bestMatches", [])

    if not matches:
        print("No matching companies found.")
        return None

    matches = matches[:5]

    print(f"\nSearch results for '{query}':")

    for index, match in enumerate(matches, start=1):
        symbol = match.get("1. symbol", "Unknown")
        name = match.get("2. name", "Unknown company")
        exchange = match.get("4. region", "Unknown region")

        print(f"{index}. {symbol} - {name} ({exchange})")

    if not interactive:
        return matches[0].get("1. symbol")

    choice = input(
        "Enter a result number, or press Enter to cancel: "
    ).strip()

    if not choice.isdigit():
        return None

    index = int(choice) - 1

    if 0 <= index < len(matches):
        return matches[index].get("1. symbol")

    print("Invalid selection.")
    return None



# WATCHLIST 


def add_stock(watchlist, ticker):
    """Add a ticker if it is not already present."""
    ticker = ticker.strip().upper()

    if not ticker:
        print("Ticker cannot be empty.")
        return watchlist

    if ticker in watchlist:
        print(f"{ticker} is already in your watchlist.")
        return watchlist

    watchlist.append(ticker)
    save_watchlist(watchlist)
    print(f"Added {ticker} to your watchlist.")

    return watchlist


def remove_stock(watchlist, ticker):
    """Remove a ticker from the watchlist."""
    ticker = ticker.strip().upper()

    if ticker not in watchlist:
        print(f"{ticker} is not in your watchlist.")
        return watchlist

    watchlist.remove(ticker)
    save_watchlist(watchlist)
    print(f"Removed {ticker} from your watchlist.")

    return watchlist


def display_watchlist(watchlist, portfolio):
    """Display stock prices and portfolio holdings."""
    print("\n" + "=" * 76)
    print("STOCK WATCHLIST")
    print("=" * 76)

    if not watchlist:
        print("Your watchlist is empty.")
    else:
        for ticker in watchlist:
            data = get_stock_data(ticker)

            price = format_money(data["latest_price"])
            change = format_money(data["price_change"])

            percentage = data["percentage_change"]
            percentage_text = (
                f"{percentage:+.2f}%"
                if percentage is not None
                else "N/A"
            )

            print(
                f"{ticker:<8} "
                f"{data['name'][:24]:<25} "
                f"{price:>12} "
                f"{change:>12} "
                f"{percentage_text:>10}"
            )

    print("\nPORTFOLIO HOLDINGS")
    print("-" * 76)

    if not portfolio:
        print("No portfolio holdings recorded.")
        return

    for holding in portfolio:
        ticker = holding["ticker"]
        shares = holding["shares"]
        purchase_price = holding["purchase_price"]

        data = get_stock_data(ticker)
        current_price = data["latest_price"]

        if current_price is None:
            value_text = "N/A"
            pl_text = "N/A"
        else:
            market_value = shares * current_price
            cost = shares * purchase_price
            profit_loss = market_value - cost

            value_text = format_money(market_value)
            pl_text = format_money(profit_loss)

        print(
            f"{ticker:<8} "
            f"Shares: {shares:g}  "
            f"Purchase: {format_money(purchase_price)}  "
            f"Market value: {value_text}  "
            f"P/L: {pl_text}"
        )


# PORTFOLIO


def record_purchase(portfolio, ticker, shares, purchase_price):
    """Record a stock purchase."""
    ticker = ticker.strip().upper()

    shares = clean_number(shares)
    purchase_price = clean_number(purchase_price)

    if not ticker:
        print("Ticker cannot be empty.")
        return portfolio

    if shares is None or shares <= 0:
        print("Shares must be a positive number.")
        return portfolio

    if purchase_price is None or purchase_price <= 0:
        print("Purchase price must be a positive number.")
        return portfolio

    data = get_stock_data(ticker)
    latest_price = data["latest_price"]

    holding = {
        "ticker": ticker,
        "shares": shares,
        "purchase_price": purchase_price,
        "latest_price": latest_price,
    }

    portfolio.append(holding)
    save_portfolio(portfolio)

    print(
        f"Recorded {shares:g} shares of {ticker} "
        f"at {format_money(purchase_price)} per share."
    )

    return portfolio


def calculate_portfolio_value_and_profit_loss(portfolio):
    """
    Calculate total cost, market value and unrealized P/L.

    Holdings without a current price are excluded from the
    priced market-value total. The result indicates whether
    all holdings could be priced.
    """
    total_cost = 0.0
    total_market_value = 0.0
    total_profit_loss = 0.0
    all_holdings_priced = True

    price_cache = {}

    for holding in portfolio:
        ticker = holding["ticker"]
        shares = holding["shares"]
        purchase_price = holding["purchase_price"]

        cost = shares * purchase_price
        total_cost += cost

        if ticker not in price_cache:
            data = get_stock_data(ticker)
            price_cache[ticker] = data["latest_price"]

        current_price = price_cache[ticker]

        if current_price is None:
            all_holdings_priced = False
            continue

        market_value = shares * current_price
        profit_loss = market_value - cost

        total_market_value += market_value
        total_profit_loss += profit_loss

    print("\nPORTFOLIO SUMMARY")
    print("-" * 36)
    print(f"Total purchase cost: {format_money(total_cost)}")
    print(f"Market value of priced holdings: {format_money(total_market_value)}")

    if all_holdings_priced:
        percentage_return = (
            (total_profit_loss / total_cost) * 100
            if total_cost > 0
            else 0.0
        )

        print(f"Total unrealized P/L: {format_money(total_profit_loss)}")
        print(f"Return: {percentage_return:+.2f}%")
    else:
        print("Total P/L: Incomplete; some current prices are unavailable.")

    return {
        "total_cost": total_cost,
        "market_value": total_market_value,
        "profit_loss": total_profit_loss if all_holdings_priced else None,
        "all_holdings_priced": all_holdings_priced,
    }



# TERMINAL 


def main():
    """Run the original terminal interface."""
    watchlist = load_watchlist()
    portfolio = load_portfolio()

    while True:
        print("\n" + "=" * 36)
        print("STOCKWATCHLIST")
        print("=" * 36)
        print("1. Display watchlist and holdings")
        print("2. Add stock")
        print("3. Remove stock")
        print("4. Search for a company")
        print("5. Record a purchase")
        print("6. Calculate portfolio value and P/L")
        print("7. View portfolio holdings")
        print("8. Save data")
        print("9. Exit")

        choice = input("Choose an option (1-9): ").strip()

        if choice == "1":
            display_watchlist(watchlist, portfolio)

        elif choice == "2":
            ticker = input("Enter ticker symbol: ")
            watchlist = add_stock(watchlist, ticker)

        elif choice == "3":
            ticker = input("Enter ticker to remove: ")
            watchlist = remove_stock(watchlist, ticker)

        elif choice == "4":
            query = input("Enter company name or ticker: ")
            ticker = search_ticker(query, interactive=True)

            if ticker:
                watchlist = add_stock(watchlist, ticker)

        elif choice == "5":
            ticker = input("Enter ticker symbol: ")

            try:
                shares = float(input("Number of shares: "))
                purchase_price = float(
                    input("Purchase price per share: $")
                )
            except ValueError:
                print("Enter valid numeric values.")
                continue

            portfolio = record_purchase(
                portfolio, ticker, shares, purchase_price
            )

            if ticker.strip():
                normalized_ticker = ticker.strip().upper()

                if normalized_ticker not in watchlist:
                    watchlist = add_stock(watchlist, normalized_ticker)

        elif choice == "6":
            calculate_portfolio_value_and_profit_loss(portfolio)

        elif choice == "7":
            if not portfolio:
                print("No portfolio holdings recorded.")
            else:
                for holding in portfolio:
                    print(
                        f"{holding['ticker']}: "
                        f"{holding['shares']:g} shares at "
                        f"{format_money(holding['purchase_price'])} each"
                    )

        elif choice == "8":
            save_watchlist(watchlist)
            save_portfolio(portfolio)
            print("Watchlist and portfolio saved.")

        elif choice == "9":
            save_watchlist(watchlist)
            save_portfolio(portfolio)
            print("Data saved. Goodbye!")
            break

        else:
            print("Invalid choice. Enter a number from 1 to 9.")


if __name__ == "__main__":
    main()
