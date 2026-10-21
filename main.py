
import os
import json
import requests
from urllib.parse import urlencode
import yfinance as yf



# PRICE 


def clean_price(price):
    """Convert a formatted price such as '$339.50' to a float."""
    if isinstance(price, (int, float)):
        return float(price)

    if not price or price == "N/A":
        return None

    try:
        return float(price.replace("$", "").replace(",", "").strip())
    except (ValueError, AttributeError):
        return None



# WATCHLIST 


def load_watchlist():
    try:
        with open("watchlist.json", "r") as file:
            watchlist = json.load(file)

        if not isinstance(watchlist, list):
            print("Warning: watchlist.json must contain a list.")
            return []

        return list(dict.fromkeys(
            ticker.strip().upper()
            for ticker in watchlist
            if isinstance(ticker, str) and ticker.strip()
        ))

    except FileNotFoundError:
        return []
    except json.JSONDecodeError:
        print("Warning: watchlist.json is invalid. Starting empty.")
        return []


def save_watchlist(watchlist):
    try:
        with open("watchlist.json", "w") as file:
            json.dump(watchlist, file, indent=4)
    except OSError as error:
        print(f"Could not save watchlist: {error}")



# STOCK DATA


def get_stock_data(ticker):
    try:
        stock = yf.Ticker(ticker)
        history = stock.history(period="5d")

        if history.empty or len(history) < 2:
            raise ValueError("Not enough historical price data.")

        latest_close = float(history["Close"].iloc[-1])
        previous_close = float(history["Close"].iloc[-2])

        if previous_close == 0:
            raise ValueError("Previous closing price is zero.")

        price_change = latest_close - previous_close
        percentage_change = (price_change / previous_close) * 100

        try:
            name = stock.info.get("longName") or ticker
        except Exception:
            name = ticker

        return {
            "name": name,
            "ticker": ticker,
            "latest_price": latest_close,
            "price_change": price_change,
            "percentage_change": percentage_change
        }

    except Exception as error:
        print(f"Could not retrieve data for {ticker}: {error}")

        return {
            "name": ticker,
            "ticker": ticker,
            "latest_price": None,
            "price_change": None,
            "percentage_change": None
        }


def display_watchlist(watchlist, portfolio):
    if not watchlist and not portfolio:
        print("\nYour watchlist and portfolio are empty.")
        return

    print("\nStock Watchlist")
    print("-" * 85)
    print(
        f"{'Name':<25} {'Ticker':>8} {'Latest Price':>15} "
        f"{'Price Change':>15} {'% Change':>12}"
    )
    print("-" * 85)

    # Display watchlist stocks
    for ticker in watchlist:
        data = get_stock_data(ticker)

        price = (
            f"${data['latest_price']:.2f}"
            if data["latest_price"] is not None else "N/A"
        )
        change = (
            f"${data['price_change']:.2f}"
            if data["price_change"] is not None else "N/A"
        )
        percent = (
            f"{data['percentage_change']:.2f}%"
            if data["percentage_change"] is not None else "N/A"
        )

        print(
            f"{data['name'][:25]:<25} "
            f"{ticker:>8} {price:>15} {change:>15} {percent:>12}"
        )

    print("-" * 85)

    # Display portfolio holdings
    if portfolio:
        print("\nPortfolio Holdings")
        print("-" * 85)
        print(
            f"{'Ticker':<10} {'Shares':>10} {'Cost/Share':>14} "
            f"{'Current Value':>16} {'Profit/Loss':>16}"
        )
        print("-" * 85)

        for holding in portfolio:
            ticker = holding["ticker"]
            shares = holding["shares"]
            purchase_price = holding["purchase_price"]

            data = get_stock_data(ticker)

            if data["latest_price"] is not None:
                latest_price = data["latest_price"]
                holding["latest_price"] = latest_price
            else:
                latest_price = holding["latest_price"]

            if latest_price is None:
                current_value = None
                profit_or_loss = None
            else:
                current_value = shares * latest_price
                profit_or_loss = shares * (latest_price - purchase_price)

            value_text = (
                f"${current_value:.2f}"
                if current_value is not None else "N/A"
            )
            profit_text = (
                f"${profit_or_loss:+.2f}"
                if profit_or_loss is not None else "N/A"
            )

            print(
                f"{ticker:<10} {shares:>10} "
                f"${purchase_price:>13.2f} "
                f"{value_text:>16} {profit_text:>16}"
            )

        print("-" * 85)

        # Save any refreshed prices
        save_portfolio(portfolio)



#  SEARCH


def search_ticker(query):
    query = query.strip()

    if not query:
        print("Please enter a company name.")
        return None

    api_key = os.getenv("ALPHA_VANTAGE_API_KEY")

    if not api_key:
        print(
            "Alpha Vantage API key not found. "
            "Set the ALPHA_VANTAGE_API_KEY environment variable."
        )
        return None

    params = {
        "function": "SYMBOL_SEARCH",
        "keywords": query,
        "apikey": api_key
    }

    url = "https://www.alphavantage.co/query?" + urlencode(params)

    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()

        matches = data.get("bestMatches", [])

        if matches:
            print(f"\nSearch results for '{query}':")

            for index, match in enumerate(matches[:5], start=1):
                symbol = match.get("1. symbol", "Unknown")
                company = match.get("2. name", "Unknown company")
                print(f"{index}. {company} ({symbol})")

            selection = input(
                "Enter a result number to add it to your watchlist, "
                "or press Enter to cancel: "
            ).strip()

            if not selection:
                return None

            if selection.isdigit() and 1 <= int(selection) <= min(5, len(matches)):
                return matches[int(selection) - 1].get("1. symbol")

            print("Invalid selection.")
            return None

        if "Note" in data or "Information" in data:
            print("Alpha Vantage may have limited this request. Try again later.")
        else:
            print(f"No ticker found for '{query}'.")

        return None

    except requests.exceptions.RequestException as error:
        print(f"Error searching for ticker: {error}")
        return None
    except ValueError as error:
        print(f"Could not read the search response: {error}")
        return None



# PORTFOLIO 


def load_portfolio():
    try:
        with open("portfolio.json", "r") as file:
            portfolio = json.load(file)

        if not isinstance(portfolio, list):
            print("Warning: portfolio.json must contain a list.")
            return []

        validated = []

        for holding in portfolio:
            if not isinstance(holding, dict):
                continue

            try:
                ticker = holding["ticker"]
                shares = int(holding["shares"])
                purchase_price = float(holding["purchase_price"])
                latest_price = holding.get("latest_price")

                if not isinstance(ticker, str) or not ticker.strip():
                    continue
                if shares <= 0 or purchase_price <= 0:
                    continue

                if latest_price is not None:
                    latest_price = float(latest_price)
                    if latest_price < 0:
                        continue

                validated.append({
                    "ticker": ticker.strip().upper(),
                    "shares": shares,
                    "purchase_price": purchase_price,
                    "latest_price": latest_price
                })

            except (KeyError, TypeError, ValueError):
                continue

        return validated

    except FileNotFoundError:
        return []
    except json.JSONDecodeError:
        print("Warning: portfolio.json is invalid. Starting empty.")
        return []


def save_portfolio(portfolio):
    try:
        with open("portfolio.json", "w") as file:
            json.dump(portfolio, file, indent=4)
    except OSError as error:
        print(f"Could not save portfolio: {error}")


def record_purchase(portfolio):
    ticker = input("Enter the stock ticker: ").strip().upper()

    if not ticker:
        print("Please enter a ticker symbol.")
        return

    shares_input = input("Enter the number of shares purchased: ").strip()
    price_input = input("Enter the purchase price per share: ").strip()

    try:
        shares = int(shares_input)
        purchase_price = float(price_input)

        if shares <= 0 or purchase_price <= 0:
            raise ValueError

    except ValueError:
        print("Invalid input. Shares and price must be positive numbers.")
        return

    data = get_stock_data(ticker)

    if data["latest_price"] is None:
        print("Could not fetch the current price.")
        confirm = input(
            "Record this purchase using the purchase price temporarily? (y/n): "
        ).strip().lower()

        if confirm != "y":
            print("Purchase cancelled.")
            return

        latest_price = purchase_price
    else:
        latest_price = data["latest_price"]

    portfolio.append({
        "ticker": ticker,
        "shares": shares,
        "purchase_price": purchase_price,
        "latest_price": latest_price
    })

    save_portfolio(portfolio)
    print(f"Purchase recorded for {ticker}.")


def calculate_portfolio_value_and_profit_loss(portfolio):
    if not portfolio:
        print("Your portfolio is empty.")
        return

    total_value = 0.0
    total_cost = 0.0
    priced_holdings = 0

    print("\nPortfolio Summary")
    print("-" * 60)

    for holding in portfolio:
        ticker = holding["ticker"]
        shares = holding["shares"]
        purchase_price = holding["purchase_price"]

        cost = shares * purchase_price
        total_cost += cost

        data = get_stock_data(ticker)

        if data["latest_price"] is not None:
            latest_price = data["latest_price"]
            holding["latest_price"] = latest_price
        else:
            latest_price = holding["latest_price"]

        if latest_price is None:
            print(f"{ticker}: current price unavailable; excluded from current value.")
            continue

        current_value = shares * latest_price
        profit_or_loss = current_value - cost
        profit_percentage = (profit_or_loss / cost) * 100 if cost else 0.0

        total_value += current_value
        priced_holdings += 1

        print(f"{ticker}:")
        print(f"  Shares: {shares}")
        print(f"  Purchase cost: ${cost:.2f}")
        print(f"  Current value: ${current_value:.2f}")
        print(f"  Profit/Loss: ${profit_or_loss:+.2f} ({profit_percentage:+.2f}%)")

    print("-" * 60)
    print(f"Total cost basis: ${total_cost:.2f}")

    if priced_holdings == len(portfolio):
        total_profit = total_value - total_cost
        total_percentage = (
            (total_profit / total_cost) * 100 if total_cost else 0.0
        )

        print(f"Total portfolio value: ${total_value:.2f}")
        print(f"Total Profit/Loss: ${total_profit:+.2f}")
        print(f"Total return: {total_percentage:+.2f}%")
    else:
        print("Some current prices are unavailable.")
        print(f"Known current value: ${total_value:.2f}")
        print("Overall profit/loss is incomplete until all prices are available.")

    save_portfolio(portfolio)



# MAIN MENU


def main():
    watchlist = load_watchlist()
    portfolio = load_portfolio()

    while True:
        print("\nStock Watchlist")
        print("1. Add stock to watchlist")
        print("2. Remove stock from watchlist")
        print("3. Display watchlist and portfolio holdings")
        print("4. Search for ticker by company name")
        print("5. Record a purchase")
        print("6. Calculate portfolio value and profit/loss")
        print("7. Save portfolio")
        print("8. Load portfolio")
        print("9. Exit")

        choice = input("Enter your choice: ").strip()

        if choice == "1":
            ticker = input("Enter the stock ticker: ").strip().upper()

            if not ticker:
                print("Please enter a ticker symbol.")
            elif ticker in watchlist:
                print(f"{ticker} is already in your watchlist.")
            else:
                watchlist.append(ticker)
                save_watchlist(watchlist)
                print(f"Added {ticker} to your watchlist.")

        elif choice == "2":
            ticker = input("Enter the stock ticker to remove: ").strip().upper()

            if ticker in watchlist:
                watchlist.remove(ticker)
                save_watchlist(watchlist)
                print(f"{ticker} removed from your watchlist.")
            else:
                print(f"{ticker} was not found in your watchlist.")

        elif choice == "3":
            display_watchlist(watchlist, portfolio)

        elif choice == "4":
            query = input("Enter the company name to search: ").strip()
            ticker = search_ticker(query)

            if ticker:
                print(f"Selected ticker: {ticker}")
                add_to_watchlist = input(
                    "Add this ticker to your watchlist? (y/n): "
                ).strip().lower()

                if add_to_watchlist == "y":
                    if ticker in watchlist:
                        print(f"{ticker} is already in your watchlist.")
                    else:
                        watchlist.append(ticker)
                        save_watchlist(watchlist)
                        print(f"Added {ticker} to your watchlist.")

        elif choice == "5":
            record_purchase(portfolio)

        elif choice == "6":
            calculate_portfolio_value_and_profit_loss(portfolio)

        elif choice == "7":
            save_portfolio(portfolio)
            print("Portfolio saved.")

        elif choice == "8":
            portfolio = load_portfolio()
            print(f"Loaded {len(portfolio)} portfolio holding(s).")

        elif choice == "9":
            save_watchlist(watchlist)
            save_portfolio(portfolio)
            print("Thanks for using Stock Watchlist!")
            break

        else:
            print("Invalid choice. Please enter a number from 1 to 9.")


if __name__ == "__main__":
    main()
