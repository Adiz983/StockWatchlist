
import yfinance as yf
import json


# Load the watchlist 
def load_watchlist():
    try:
        with open("watchlist.json", "r") as f:
            watchlist = json.load(f)

        if not isinstance(watchlist, list):
            print("Warning: watchlist.json must contain a list.")
            return []

        return watchlist

    except FileNotFoundError:
        return []

    except json.JSONDecodeError:
        print("Warning: watchlist.json is invalid. Starting with an empty watchlist.")
        return []


# Save the watchlist to a JSON file
def save_watchlist(watchlist):
    try:
        with open("watchlist.json", "w") as f:
            json.dump(watchlist, f, indent=4)
    except OSError as e:
        print(f"Could not save watchlist: {e}")


# stock data using yfinance
def get_stock_data(ticker):
    try:
        stock = yf.Ticker(ticker)

        #  up to five days of daily price history
        history = stock.history(period="5d")

        if history.empty or len(history) < 2:
            raise ValueError("Not enough historical price data")

        # Use the two most recent available daily closing prices
        latest_close = float(history["Close"].iloc[-1])
        previous_close = float(history["Close"].iloc[-2])

        if previous_close == 0:
            raise ValueError("Previous closing price is zero")

        # Calculate daily price and percentage changes
        price_change = latest_close - previous_close
        percentage_change = (price_change / previous_close) * 100

        # Get the company name when available
        try:
            name = stock.info.get("longName") or ticker
        except Exception:
            name = ticker

        return {
            "name": name,
            "ticker": ticker,
            "latest_price": f"${latest_close:.2f}",
            "price_change": f"${price_change:.2f}",
            "percentage_change": f"{percentage_change:.2f}%"
        }

    except Exception as e:
        print(f"Could not retrieve data for {ticker}: {e}")

        return {
            "name": ticker,
            "ticker": ticker,
            "latest_price": "N/A",
            "price_change": "N/A",
            "percentage_change": "N/A"
        }


# Display the watchlist and stock data
def display_watchlist(watchlist):
    if not watchlist:
        print("Your watchlist is empty.")
        return

    print(
        f"\n{'Name':<25} {'Ticker':>8} "
        f"{'Latest Price':>15} {'Price Change':>15} {'% Change':>12}"
    )
    print("-" * 80)

    for ticker in watchlist:
        stock_data = get_stock_data(ticker)

        print(
            f"{stock_data['name'][:25]:<25} "
            f"{stock_data['ticker']:>8} "
            f"{stock_data['latest_price']:>15} "
            f"{stock_data['price_change']:>15} "
            f"{stock_data['percentage_change']:>12}"
        )


# Main program
def main():
    watchlist = load_watchlist()

    while True:
        print("\nStock Watchlist")
        print("1. Add stock to watchlist")
        print("2. Remove stock from watchlist")
        print("3. Display watchlist with latest prices and percentage changes")
        print("4. Exit")

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
            display_watchlist(watchlist)

        elif choice == "4":
            print("Thanks for using Stock Watchlist!")
            break

        else:
            print("Invalid choice. Please enter 1, 2, 3, or 4.")


if __name__ == "__main__":
    main()
