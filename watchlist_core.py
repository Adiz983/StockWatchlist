"""Watchlist storage and company search (replaces the parts of main.py the terminal used)."""
import json
import os
from pathlib import Path

import requests

PROJECT_DIR = Path(__file__).resolve().parent
WATCHLIST_FILE = PROJECT_DIR / "watchlist.json"
ALPHA_VANTAGE_API_KEY = os.getenv("ALPHA_VANTAGE_API_KEY")


def _clean(tickers):
    return list(dict.fromkeys(
        t.strip().upper() for t in tickers if isinstance(t, str) and t.strip()
    ))


def load_watchlist():
    """Load and validate saved stock tickers."""
    try:
        with open(WATCHLIST_FILE, "r", encoding="utf-8") as file:
            watchlist = json.load(file)
        if not isinstance(watchlist, list):
            print("Warning: watchlist.json must contain a list.")
            return []
        return _clean(watchlist)
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
        with open(WATCHLIST_FILE, "w", encoding="utf-8") as file:
            json.dump(_clean(watchlist), file, indent=4)
    except OSError as error:
        print(f"Could not save watchlist: {error}")


def search_ticker(query):
    """Search Alpha Vantage for a company; returns the best-match symbol or None."""
    if not ALPHA_VANTAGE_API_KEY:
        print("Company search needs an API key. Set the ALPHA_VANTAGE_API_KEY environment variable.")
        return None
    try:
        response = requests.get(
            "https://www.alphavantage.co/query",
            params={"function": "SYMBOL_SEARCH", "keywords": query.strip(),
                    "apikey": ALPHA_VANTAGE_API_KEY},
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
    return matches[0].get("1. symbol") if matches else None
