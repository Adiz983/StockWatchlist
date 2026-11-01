"""News feed helpers: parsing, de-duplicating and merging headlines.

Parsing and merging are pure (no Qt, no network) so they can be tested alone.
Only fetch_news() touches the network, and it imports yfinance lazily.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class NewsItem:
    title: str
    publisher: str = ""
    url: str = ""
    published: datetime | None = None
    summary: str = ""
    tickers: set = field(default_factory=set)   # watchlist symbols this story is tagged to
    market: bool = False                        # also came from the general market feed

    @property
    def key(self) -> str:
        """Identity used to de-duplicate the same story arriving via several tickers."""
        if self.url:
            return self.url.split("?")[0].lower()
        return self.title.strip().lower()


def _strip_html(text) -> str:
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", str(text))
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def _parse_time(value) -> datetime | None:
    if value is None or value == "":
        return None
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (ValueError, OverflowError, OSError):
        return None


def _http_url(url) -> str:
    return url if isinstance(url, str) and url.lower().startswith(("http://", "https://")) else ""


def parse_news_item(raw) -> NewsItem | None:
    """Turn one raw yfinance news entry into a NewsItem.

    Handles both shapes yfinance has returned: the newer nested
    {"content": {...}} form and the older flat {"title", "link", ...} form.
    """
    if not isinstance(raw, dict):
        return None
    content = raw.get("content")
    if isinstance(content, dict):
        title = content.get("title")
        summary = content.get("summary") or content.get("description")
        provider = content.get("provider") or {}
        publisher = provider.get("displayName") if isinstance(provider, dict) else ""
        url = ""
        for link_key in ("canonicalUrl", "clickThroughUrl"):
            link = content.get(link_key)
            if isinstance(link, dict) and _http_url(link.get("url")):
                url = link["url"]
                break
        published = _parse_time(content.get("pubDate") or content.get("displayTime"))
    else:
        title = raw.get("title")
        summary = raw.get("summary")
        publisher = raw.get("publisher")
        url = _http_url(raw.get("link"))
        published = _parse_time(raw.get("providerPublishTime"))
    title = _strip_html(title)
    if not title:
        return None
    return NewsItem(title=title, publisher=_strip_html(publisher), url=_http_url(url),
                    published=published, summary=_strip_html(summary))


def merge_news(by_symbol: dict, watchlist: set, market_symbols: set) -> list:
    """Combine per-symbol results into one de-duplicated, newest-first feed.

    A story fetched under several symbols becomes one row carrying all of its
    watchlist tickers. Results for symbols that are no longer in the watchlist
    (and aren't market feeds) are ignored.
    """
    merged: dict[str, NewsItem] = {}
    for symbol, items in by_symbol.items():
        in_watchlist = symbol in watchlist
        is_market = symbol in market_symbols
        if not (in_watchlist or is_market):
            continue
        for item in items:
            row = merged.get(item.key)
            if row is None:
                row = NewsItem(item.title, item.publisher, item.url, item.published, item.summary)
                merged[item.key] = row
            elif item.published and (row.published is None or item.published > row.published):
                row.published = item.published
            if not row.summary and item.summary:
                row.summary = item.summary
            if in_watchlist:
                row.tickers.add(symbol)
            if is_market:
                row.market = True
    floor = datetime.min.replace(tzinfo=timezone.utc)
    return sorted(merged.values(), key=lambda n: n.published or floor, reverse=True)


def format_age(published: datetime | None, now: datetime | None = None) -> str:
    if published is None:
        return "—"
    now = now or datetime.now(timezone.utc)
    seconds = (now - published).total_seconds()
    if seconds < 60:
        return "now"
    if seconds < 3600:
        return f"{int(seconds // 60)}m"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h"
    if seconds < 7 * 86400:
        return f"{int(seconds // 86400)}d"
    return published.strftime("%b %d")


def _parse_all(raw_items) -> list:
    items = [parse_news_item(raw) for raw in (raw_items or [])]
    return [item for item in items if item is not None]


def fetch_news(symbol: str) -> list:
    """Recent headlines for one symbol (blocking; call from a worker thread).

    Tries Ticker.news first, then falls back to Yahoo search results. Raises
    with a readable reason if nothing usable came back because of an error or
    an unrecognised format, so the UI can show why instead of staying empty.
    """
    import yfinance as yf

    errors, raw = [], []
    try:
        raw = yf.Ticker(symbol).news or []
    except Exception as error:  # noqa: BLE001
        errors.append(f"Ticker.news: {error}")
    items = _parse_all(raw)

    if not items:
        try:
            found = yf.Search(symbol, news_count=10)
            items = _parse_all(getattr(found, "news", None))
        except Exception as error:  # noqa: BLE001
            errors.append(f"Search: {error}")

    if not items and errors:
        raise RuntimeError("; ".join(errors))
    if not items and raw:
        first = raw[0]
        keys = sorted(first)[:8] if isinstance(first, dict) else type(first).__name__
        raise ValueError(f"unrecognised news format (keys: {keys})")
    return items
