import yfinance as yf
import json

tickers = ["AAPL", "NVDA", "MSFT", "TSLA"]

def print_watchlist():
    print("Stock Watchlist:")
    for ticker in tickers:
        print(ticker)

def add_ticker(new_ticker):
    if new_ticker.isalpha() and len(new_ticker) == 4:
        if new_ticker not in tickers:
            tickers.append(new_ticker)
            save_watchlist()
            print(f"{new_ticker} added to watchlist.")
        else:
            print(f"{new_ticker} already exists in watchlist.")
    else:
        print("Invalid ticker. Please enter a 4-letter alphabetic symbol.")

def remove_ticker(ticker_to_remove):
    if ticker_to_remove.isalpha() and len(ticker_to_remove) == 4:
        if ticker_to_remove in tickers:
            tickers.remove(ticker_to_remove)
            save_watchlist()
            print(f"{ticker_to_remove} removed from watchlist.")
        else:
            print(f"{ticker_to_remove} not found in watchlist.")
    else:
        print("Invalid ticker. Please enter a 4-letter alphabetic symbol.")

def get_stock_data(tickers):
    for ticker in tickers:
        try:
            stock = yf.Ticker(ticker)
            info = stock.info
            name = info.get('longName')
            latest_price = info.get('regularMarketPrice')
            previous_close = info.get('regularMarketPreviousClose')
            day_range = f"{info.get('dayLow')} - {info.get('dayHigh')}"
            
            print(f"Ticker: {ticker}")
            print(f"Name: {name}")
            print(f"Latest Price: {latest_price}")
            print(f"Previous Close: {previous_close}")
            print(f"Day's Range: {day_range}\n")
        except Exception as e:
            print(f"Error fetching data for {ticker}: {e}")

def save_watchlist():
    with open("watchlist.json", "w") as f:
        json.dump(tickers, f)

def load_watchlist():
    global tickers
    try:
        with open("watchlist.json", "r") as f:
            tickers = json.load(f)
    except FileNotFoundError:
        pass

if __name__ == "__main__":
    load_watchlist()
    print("\nInitial Watchlist:")
    print_watchlist()

    add_ticker("GOOGL")
    remove_ticker("NVDA")

    print("\nUpdated Watchlist:")
    print_watchlist()

    get_stock_data(tickers)
