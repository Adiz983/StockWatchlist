"""Grounded Q&A over a company's loaded data and the user's saved notes.

Pipeline:  build_facts -> retrieve -> (LLM or plain lookup) -> verify_answer

Design rules:
  * The model only ever sees numbered facts we retrieved (F1, F2, ...). It is
    told to answer from them alone and to cite the fact id beside every figure.
  * If retrieval finds nothing relevant, we say so and do NOT call the model.
  * After the model answers, citations and figures are checked against the
    facts. Problems are surfaced to the user rather than hidden.
  * The model is told never to predict or recommend, and the UI repeats that.
  * With no API key configured the assistant degrades to an extractive lookup
    (it just lists the matching facts); it never invents prose.

Qt-free. Only ask_llm() touches the network (stdlib urllib, no extra packages).
"""
from __future__ import annotations

import json
import math
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

import analysis

API_URL = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
DEFAULT_MODEL = os.environ.get("STOCKTERM_MODEL", "gemini-3.8-flash")
KEY_ENV_VARS = ("GEMINI_API_KEY", "GOOGLE_API_KEY")
MAX_FACTS = 10
SRC_STATEMENTS = "Financial statements (Yahoo Finance via yfinance)"
SRC_STATS = "Key statistics (Yahoo Finance via yfinance)"
SRC_PRICES = "Price history (Yahoo Finance via yfinance)"
SRC_NOTES = "Your saved analysis"

DISCLAIMER = ("Generated from retrieved data. May contain errors; verify the cited figures. "
              "Not a prediction, guarantee or investment advice.")


@dataclass
class Fact:
    id: str
    kind: str          # 'statement' | 'metric' | 'price' | 'note' | 'profile'
    label: str
    text: str
    source: str


# --------------------------------------------------------------------------
# Number formatting (kept local so this module stays independent of the UI)
# --------------------------------------------------------------------------
def _finite(value):
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _money(value, dollars=True):
    x = _finite(value)
    if x is None:
        return "N/A"
    mag, sign, prefix = abs(x), "-" if x < 0 else "", "$" if dollars else ""
    for scale, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M")):
        if mag >= scale:
            return f"{sign}{prefix}{mag / scale:,.2f}{suffix}"
    return f"{sign}{prefix}{mag:,.0f}"


def _by_style(value, style):
    x = _finite(value)
    if x is None:
        return "N/A"
    if style == "pct":
        return f"{x:.1f}%"
    if style == "eps":
        return f"{x:,.2f}"
    if style == "count":
        return _money(x, dollars=False)
    return _money(x)


def _pct_frac(value, signed=False):
    x = _finite(value)
    return "N/A" if x is None else (f"{x * 100:+.2f}%" if signed else f"{x * 100:.2f}%")


def _mult(value):
    x = _finite(value)
    return "N/A" if x is None else f"{x:,.2f}x"


# --------------------------------------------------------------------------
# Fact building
# --------------------------------------------------------------------------
METRICS = [
    ("Market cap", "marketCap", _money), ("Enterprise value", "enterpriseValue", _money),
    ("Trailing P/E", "trailingPE", _mult), ("Forward P/E", "forwardPE", _mult),
    ("PEG ratio", "trailingPegRatio", _mult), ("Price/Sales (TTM)", "priceToSalesTrailing12Months", _mult),
    ("Price/Book", "priceToBook", _mult), ("EV/EBITDA", "enterpriseToEbitda", _mult),
    ("Gross margin (TTM)", "grossMargins", _pct_frac), ("Operating margin (TTM)", "operatingMargins", _pct_frac),
    ("Net profit margin (TTM)", "profitMargins", _pct_frac), ("Return on equity", "returnOnEquity", _pct_frac),
    ("Return on assets", "returnOnAssets", _pct_frac),
    ("Revenue growth (most recent quarter, YoY)", "revenueGrowth", lambda v: _pct_frac(v, True)),
    ("Earnings growth (most recent quarter, YoY)", "earningsGrowth", lambda v: _pct_frac(v, True)),
    ("Free cash flow (TTM)", "freeCashflow", _money), ("Operating cash flow (TTM)", "operatingCashflow", _money),
    ("Total cash", "totalCash", _money), ("Total debt", "totalDebt", _money),
    ("Current ratio", "currentRatio", lambda v: "N/A" if _finite(v) is None else f"{_finite(v):.2f}"),
    ("Trailing EPS", "trailingEps", lambda v: "N/A" if _finite(v) is None else f"${_finite(v):,.2f}"),
]


def _statement_facts(fund: dict, out: list, add) -> None:
    titles = {"income": "income statement", "balance": "balance sheet", "cashflow": "cash flow statement"}
    for freq in ("annual", "quarterly"):
        quarterly = freq == "quarterly"
        keep = 5 if quarterly else 4
        for kind, df in (fund.get(freq) or {}).items():
            if df is None or df.empty:
                continue
            where = f"{freq.capitalize()} {titles.get(kind, kind)}"
            for label in analysis.KEY_ROWS.get(kind, []):
                if label not in df.index:
                    continue
                series = df.loc[label].dropna().tail(keep)
                if series.empty:
                    continue
                style = analysis.row_style(label)
                parts = [f"{analysis.period_label(c, quarterly)}: {_by_style(v, style)}"
                         for c, v in series.items()]
                text = f"{where} - {label}: " + "; ".join(parts)
                if len(series) >= 2 and style == "money" and series.iloc[-2]:
                    chg = (series.iloc[-1] / series.iloc[-2] - 1.0) * 100.0
                    text += f". Latest period vs previous: {chg:+.1f}%"
                add("statement", f"{where} {label}", text, SRC_STATEMENTS)
            if kind == "income":
                for label, series in analysis.derived_income_rows(df, quarterly):
                    series = series.dropna().tail(keep)
                    if series.empty:
                        continue
                    parts = [f"{analysis.period_label(c, quarterly)}: {v:.1f}%" for c, v in series.items()]
                    add("statement", f"{where} {label}", f"{where} - {label} (computed): " + "; ".join(parts),
                        SRC_STATEMENTS)


def build_facts(symbol: str, info: dict | None, fund: dict | None, frame: pd.DataFrame | None,
                notes: list | None = None) -> list:
    facts: list = []

    def add(kind, label, text, source):
        facts.append(Fact(f"F{len(facts) + 1}", kind, label, text, source))

    info = info or {}
    name = info.get("longName") or info.get("shortName") or symbol
    profile = [f"{name} ({symbol})"]
    for key, title in (("sector", "Sector"), ("industry", "Industry"), ("country", "Country")):
        if info.get(key):
            profile.append(f"{title}: {info[key]}")
    add("profile", "Company profile", "Company profile - " + "; ".join(profile), SRC_STATS)
    summary = info.get("longBusinessSummary")
    if summary:
        add("profile", "Business summary", "Business summary - " + str(summary)[:500], SRC_STATS)

    for label, key, fmt in METRICS:
        raw = info.get(key)
        if key == "trailingPegRatio" and _finite(raw) is None:
            raw = info.get("pegRatio")
        if _finite(raw) is not None:
            add("metric", label, f"{label}: {fmt(raw)}", SRC_STATS)
    dy = None
    rate, price = _finite(info.get("dividendRate")), _finite(info.get("currentPrice") or info.get("regularMarketPrice"))
    if rate is not None and price:
        dy = rate / price * 100.0
    if dy is not None:
        add("metric", "Dividend yield", f"Dividend yield: {dy:.2f}%", SRC_STATS)
    de = _finite(info.get("debtToEquity"))
    if de is not None:
        add("metric", "Debt/Equity", f"Debt/Equity: {de / 100:.2f}x", SRC_STATS)

    if fund:
        _statement_facts(fund, facts, add)

    if frame is not None and not frame.empty:
        close = frame["Close"].dropna()
        if not close.empty:
            last_date = close.index[-1].strftime("%Y-%m-%d")
            add("price", "Latest close", f"Latest close ({last_date}): ${close.iloc[-1]:,.2f}", SRC_PRICES)
            year = close.tail(252)
            add("price", "52-week range",
                f"52-week range (last ~252 sessions): low ${year.min():,.2f}, high ${year.max():,.2f}", SRC_PRICES)
            if len(close) > 252 and close.iloc[-253]:
                add("price", "1-year price return",
                    f"1-year price return: {(close.iloc[-1] / close.iloc[-253] - 1) * 100:+.1f}%", SRC_PRICES)
        last = frame.iloc[-1]
        if pd.notna(last.get("RSI")):
            add("price", "RSI", f"RSI (14): {last['RSI']:.1f}", SRC_PRICES)
        if pd.notna(last.get("SMA200")) and last.get("Close"):
            add("price", "vs SMA200",
                f"Close vs 200-day SMA: {(last['Close'] / last['SMA200'] - 1) * 100:+.1f}%", SRC_PRICES)

    for note in notes or []:
        stamp = pd.Timestamp(note.get("created", 0), unit="s").strftime("%Y-%m-%d")
        title = note.get("title") or "Untitled"
        add("note", f"Note: {title}",
            f"Your saved note '{title}' ({stamp}) [your opinion, not verified data]: {note['text']}", SRC_NOTES)
    return facts


# --------------------------------------------------------------------------
# Retrieval
# --------------------------------------------------------------------------
_STOP = set("a an and are as at be been by can could did do does for from had has have how i if in into is it "
            "its me my of on or our over please should tell than that the their them there these this to was "
            "were what when which who why will with would you your about show give".split())
_SYNONYMS = {
    "sales": ["revenue"], "revenue": ["sales"], "profit": ["income", "earnings", "margin"],
    "profitable": ["income", "margin"], "profitability": ["margin", "income"], "earnings": ["income", "eps"],
    "eps": ["earnings"], "debt": ["liabilities", "leverage", "debt"], "leverage": ["debt", "equity"],
    "borrowing": ["debt"], "cash": ["cash", "flow"], "fcf": ["free", "cash"], "valuation": ["pe", "price", "sales", "book", "ev"],
    "expensive": ["pe", "valuation"], "cheap": ["pe", "valuation"], "dividend": ["dividend", "yield"],
    "growth": ["growth", "change"], "growing": ["growth", "change"], "capex": ["capital", "expenditure"],
    "buyback": ["repurchase"], "buybacks": ["repurchase"], "liquidity": ["current", "cash"],
    "margins": ["margin"], "shares": ["shares", "average"], "momentum": ["rsi", "macd", "sma"],
    "trend": ["sma", "price"], "stock": ["price"], "price": ["close"],
}
_NOTE_WORDS = {"notes", "note", "saved", "analysis", "thesis", "wrote", "wrote", "own"}


def _tokens(text: str) -> list:
    text = text.lower().replace("p/e", "pe").replace("p / e", "pe").replace("ev/ebitda", "ev ebitda")
    return [t for t in re.findall(r"[a-z0-9]+", text) if t not in _STOP]


def retrieve(question: str, facts: list, k: int = MAX_FACTS) -> list:
    """Top-k facts by a small BM25-style score. Returns [] if nothing matches."""
    if not facts:
        return []
    q = _tokens(question)
    expanded = set(q)
    for t in q:
        expanded.update(_SYNONYMS.get(t, []))
    if not expanded:
        return []
    docs = [(f, set(_tokens(f.text)), set(_tokens(f.label))) for f in facts]
    n = len(docs)
    df_count = {t: sum(1 for _, d, _l in docs if t in d) for t in expanded}
    wants_notes = bool(set(q) & _NOTE_WORDS)
    scored = []
    for fact, doc, label in docs:
        score = 0.0
        for t in expanded:
            if t in doc:
                idf = math.log(1 + (n - df_count[t] + 0.5) / (df_count[t] + 0.5))
                score += idf * (1.0 + (1.0 if t in label else 0.0)) * (1.0 if t in q else 0.5)
        if wants_notes and fact.kind == "note":
            score += 2.0
        if score > 0:
            scored.append((score, fact))
    scored.sort(key=lambda x: -x[0])
    if not scored:
        return []
    floor = scored[0][0] * 0.25            # drop weak tail matches
    return [f for s, f in scored[:k] if s >= floor]


# --------------------------------------------------------------------------
# Prompting
# --------------------------------------------------------------------------
SYSTEM_PROMPT = """You are a research assistant inside a stock research terminal. Answer questions about ONE company using ONLY the numbered facts provided.

Rules:
1. Use only the facts given. Never use outside knowledge for figures, dates or events. If the facts do not answer the question, say exactly what is missing instead of guessing.
2. Put the fact id in square brackets immediately after every figure you state, for example: Revenue grew 2.1% [F4]. Only cite ids that were provided.
3. If you calculate something (a ratio, a difference), show the inputs with their citations and say it is calculated.
4. Facts labelled "Your saved note" are the user's own opinions. Refer to them as the user's notes and never present them as verified data.
5. Do not predict prices, earnings or outcomes, and do not tell the user to buy, sell or hold. If asked, explain that you can't, then describe what the data does show.
6. Describe trends neutrally and mention limits (for example, a short history or a single period).
7. Keep the answer concise: a short paragraph or a few bullet points."""


def build_user_message(question: str, symbol: str, facts: list) -> str:
    lines = "\n".join(f"[{f.id}] ({f.source}) {f.text}" for f in facts)
    return f"Company: {symbol}\n\nFacts:\n{lines}\n\nQuestion: {question}"


KEY_FILE = Path(__file__).resolve().parent / "gemini_key.txt"
ENV_FILE = Path(__file__).resolve().parent / ".env"


def _from_dotenv(name: str) -> str:
    try:
        for line in ENV_FILE.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k.strip() == name:
                return v.strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


def _clean(value: str) -> str:
    return value.strip().strip('"').strip("'").strip()


def api_key_source() -> tuple:
    """(key or None, where it came from). Checks env vars, then .env, then gemini_key.txt."""
    for name in KEY_ENV_VARS:
        value = _clean(os.environ.get(name, ""))
        if value:
            return value, f"environment variable {name}"
    for name in KEY_ENV_VARS:
        value = _clean(_from_dotenv(name))
        if value:
            return value, f".env file ({ENV_FILE})"
    try:
        value = _clean(KEY_FILE.read_text(encoding="utf-8-sig"))
        if value:
            return value, f"key file ({KEY_FILE})"
    except OSError:
        pass
    return None, "not found"


def api_key() -> str | None:
    """Never hard-code the key in source; it comes from the environment or a local file."""
    return api_key_source()[0]


def save_key(value: str) -> None:
    value = _clean(value)
    if not value:
        raise ValueError("The key is empty.")
    KEY_FILE.write_text(value, encoding="utf-8")


REASONING_EFFORT = os.environ.get("STOCKTERM_REASONING", "minimal").strip().lower()   # minimal|low|medium|high


def _post(body: dict, key: str, timeout: int) -> dict:
    req = urllib.request.Request(API_URL, data=json.dumps(body).encode("utf-8"), method="POST", headers={
        "Content-Type": "application/json", "Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def ask_llm(system: str, user: str, model: str = DEFAULT_MODEL, timeout: int = 90) -> str:
    key = api_key()
    if not key:
        raise RuntimeError("GEMINI_API_KEY is not set")
    body = {
        "model": model,
        "max_tokens": 2000,
        "temperature": 0.2,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
    }
    if REASONING_EFFORT in ("minimal", "low", "medium", "high"):
        body["reasoning_effort"] = REASONING_EFFORT      # less hidden "thinking" = much faster answers
    try:
        try:
            payload = _post(body, key, timeout)
        except urllib.error.HTTPError as error:
            if error.code == 400 and "reasoning_effort" in body:
                body.pop("reasoning_effort")             # this model rejected the setting: retry without it
                payload = _post(body, key, timeout)
            else:
                raise
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")[:300]
        hint = (f" (model '{model}' was rejected; set the STOCKTERM_MODEL environment variable to a "
                "model listed at ai.google.dev/gemini-api/docs/models)") if error.code == 404 else ""
        raise RuntimeError(f"API error {error.code}: {detail}{hint}") from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"Network error: {error.reason}") from error
    try:
        text = (payload["choices"][0]["message"].get("content") or "").strip()
    except (KeyError, IndexError, TypeError) as error:
        raise RuntimeError("Unexpected response structure from the Gemini API") from error
    if not text:
        raise RuntimeError("The model returned an empty answer")
    return text


# --------------------------------------------------------------------------
# Verification
# --------------------------------------------------------------------------
_CITE = re.compile(r"\[(F\d+)\]")
_NUM = re.compile(r"(?<![\w.])-?\$?\d[\d,]*(?:\.\d+)?")


def _number_forms(token: str) -> set:
    """A figure plus its coarser roundings, so '394.33' also matches '394.3' and '394'."""
    clean = token.replace("$", "").replace(",", "").lstrip("-")
    forms = {clean}
    if "." in clean:
        whole, dec = clean.split(".")
        for d in range(len(dec) - 1, -1, -1):
            forms.add(f"{round(float(clean), d):.{d}f}" if d else str(int(round(float(clean)))))
    return forms


def verify_answer(answer: str, facts: list) -> dict:
    """Check that citations exist and that figures appear in the retrieved facts."""
    known = {f.id for f in facts}
    cited = _CITE.findall(answer)
    unknown = sorted({c for c in cited if c not in known})
    body = _CITE.sub("", answer)
    corpus = set()
    for f in facts:
        for token in _NUM.findall(f.text):
            corpus |= _number_forms(token)
    unmatched = []
    for token in _NUM.findall(body):
        is_plain_small = "." not in token and len(token.replace("$", "").replace(",", "").lstrip("-")) <= 2
        if is_plain_small:
            continue
        if not (_number_forms(token) & corpus):
            unmatched.append(token)
    issues = []
    if not cited:
        issues.append("The answer cites no facts.")
    if unknown:
        issues.append("It cites facts that were not provided: " + ", ".join(unknown) + ".")
    if unmatched:
        shown = ", ".join(list(dict.fromkeys(unmatched))[:5])
        issues.append("Figures not found in the retrieved data (calculated, rounded differently, or wrong): "
                      + shown + ".")
    return {"cited": sorted(set(cited) & known), "unknown": unknown, "unmatched": unmatched,
            "issues": issues, "ok": not issues}


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------
@dataclass
class Answer:
    text: str
    mode: str                                  # 'llm' | 'lookup' | 'no_match' | 'no_data'
    facts: list = field(default_factory=list)  # facts that were retrieved
    cited: list = field(default_factory=list)  # ids actually cited
    issues: list = field(default_factory=list)
    note: str = ""


def lookup_answer(facts: list) -> str:
    lines = [f"[{f.id}] {f.text}" for f in facts]
    return "Most relevant data found:\n" + "\n".join(lines)


def answer_question(question: str, symbol: str, facts: list, caller=ask_llm) -> Answer:
    if not facts:
        return Answer("No data is loaded for this company yet. Load a ticker and wait for its "
                      "financials, then ask again.", "no_data")
    hits = retrieve(question, facts)
    if not hits:
        return Answer("I couldn't find anything in the loaded statements, key statistics, price history or "
                      "your saved notes that relates to that question, so I won't guess. Try naming a "
                      "specific item (revenue, net income, debt, margins, cash flow, P/E...).", "no_match")
    if not api_key():
        return Answer(lookup_answer(hits), "lookup", hits, [f.id for f in hits],
                      note="No GEMINI_API_KEY is set, so this is a plain data lookup rather than a written "
                           "analysis. Set the variable to enable written answers.")
    try:
        text = caller(SYSTEM_PROMPT, build_user_message(question, symbol, hits))
    except Exception as error:  # noqa: BLE001
        return Answer(lookup_answer(hits), "lookup", hits, [f.id for f in hits],
                      note=f"The language model call failed ({error}); showing the matching data instead.")
    check = verify_answer(text, hits)
    return Answer(text, "llm", hits, check["cited"], check["issues"])
