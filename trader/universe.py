"""Which stocks each desk studies. Swing + long-term cover the S&P 500 plus
major ETFs; the day desk covers a liquid core list plus today's movers."""
import csv
import io
from datetime import datetime, timedelta, timezone

import requests

from . import config, db, log

L = log.get("universe")
SP500_CSV = "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv"
# Used only if the S&P list can't be downloaded.
FALLBACK = ("AAPL MSFT NVDA AMZN GOOGL GOOG META AVGO BRK.B LLY JPM V UNH XOM MA COST HD PG JNJ WMT "
            "NFLX ABBV BAC CRM ORCL CVX KO MRK AMD PEP ADBE TMO LIN ACN MCD CSCO ABT WFC DIS IBM GE "
            "PM INTU QCOM CAT TXN VZ AMGN NOW DHR ISRG SPGI NEE PFE UBER RTX GS T AMAT LOW BKNG HON "
            "UNP PGR BLK SYK MS ETN C TJX ELV SCHW VRTX BSX ADP LMT MDT REGN MU PLD CB MMC DE PANW "
            "ADI SBUX LRCX GILD KLAC BMY ANET CI SO MO BA FI ICE DUK INTC SHW ZTS CME MDLZ WM").split()


def sp500() -> dict:
    """{symbol: {"name", "sector", "cik", "added"}}, refreshed weekly."""
    cached = db.kv_get("sp500_v2")
    if cached and datetime.fromisoformat(cached["at"]) > datetime.now(timezone.utc) - timedelta(days=7):
        return cached["rows"]
    try:
        r = requests.get(SP500_CSV, timeout=30)
        r.raise_for_status()
        rows = {}
        for row in csv.DictReader(io.StringIO(r.text)):
            rows[row["Symbol"].strip().upper()] = {
                "name": row.get("Security", ""), "sector": row.get("GICS Sector", ""),
                "cik": row.get("CIK", ""), "added": (row.get("Date added") or "")[:10],
            }
        if len(rows) > 400:
            db.kv_set("sp500_v2", {"at": db.now_iso(), "rows": rows})
            return rows
    except Exception as e:
        L.warning("S&P 500 list download failed: %s", e)
    if cached:
        return cached["rows"]
    return {s: {"name": s, "sector": "", "cik": ""} for s in FALLBACK}


def broad(tradable: set = None) -> list:
    """Swing + long-term universe."""
    syms = set(sp500()) | set(config.ETFS) | set(config.EXTRA_SYMBOLS) | set(config.DAY_CORE)
    syms -= config.EXCLUDE_SYMBOLS
    if tradable:
        syms &= tradable
    return sorted(syms)


def info(symbol: str) -> dict:
    return sp500().get(symbol, {"name": symbol, "sector": "ETF" if symbol in config.ETFS else "", "cik": ""})
