"""Free web sources: Google News RSS (headlines from across the web) and SEC
EDGAR (official company financials). No API keys needed."""
import hashlib
import json
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus

import requests

from . import config, db, log

L = log.get("web")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) StocksBot/2.0"}


def google_news(query: str, limit=8) -> list:
    url = f"https://news.google.com/rss/search?q={quote_plus(query)}&hl=en-US&gl=US&ceid=US:en"
    try:
        r = requests.get(url, headers=UA, timeout=20)
        r.raise_for_status()
        root = ET.fromstring(r.content)
    except Exception as e:
        L.debug("google news failed for %s: %s", query, e)
        return []
    out = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        src = item.find("source")
        try:
            ts = parsedate_to_datetime(item.findtext("pubDate")).astimezone(timezone.utc)
        except Exception:
            ts = datetime.now(timezone.utc)
        out.append({"headline": title, "ts": ts, "source": src.text if src is not None else "web",
                    "url": item.findtext("link") or ""})
        if len(out) >= limit:
            break
    return out


def store_web_news(symbol: str, company: str = "") -> int:
    if "/" in symbol:
        q = company or symbol.split("/")[0] + " crypto"
    else:
        q = f'"{symbol}" stock' if not company else f'{company} {symbol} stock'
    items = google_news(q)
    rows = [("web" + hashlib.md5((i["headline"] + symbol).encode()).hexdigest()[:16], symbol,
             i["ts"].isoformat(), i["source"], i["headline"], "", i["url"]) for i in items]
    if rows:
        db.executemany("INSERT OR IGNORE INTO news VALUES (?,?,?,?,?,?,?)", rows)
    return len(rows)


def market_headlines(limit=12) -> list:
    seen, out = set(), []
    for q in ("stock market today", "Federal Reserve rates inflation", "S&P 500 futures"):
        for i in google_news(q, limit=6):
            if i["headline"] not in seen:
                seen.add(i["headline"])
                out.append(i)
    out.sort(key=lambda x: x["ts"], reverse=True)
    return out[:limit]


# ---------------------------------------------------------------- SEC EDGAR
_REV = ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet"]


def _annual(facts, names, unit="USD"):
    """Latest two fiscal-year values for the first matching XBRL concept."""
    gaap = facts.get("facts", {}).get("us-gaap", {})
    for n in names:
        units = gaap.get(n, {}).get("units", {}).get(unit)
        if not units:
            continue
        fy = {}
        for u in units:
            if u.get("form") not in ("10-K", "20-F") or u.get("fp") != "FY":
                continue
            if "start" in u:
                days = (datetime.fromisoformat(u["end"]) - datetime.fromisoformat(u["start"])).days
                if days < 300:
                    continue
            fy[u["end"]] = u["val"]
        if fy:
            ends = sorted(fy)[-2:]
            return [fy[e] for e in ends]
    return []


def sec_fundamentals(cik: str) -> dict:
    if not cik:
        return {}
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{int(cik):010d}.json"
    try:
        r = requests.get(url, headers={"User-Agent": config.SEC_USER_AGENT}, timeout=30)
        r.raise_for_status()
        f = r.json()
    except Exception as e:
        L.debug("SEC fetch failed for %s: %s", cik, e)
        return {}
    rev = _annual(f, _REV)
    ni = _annual(f, ["NetIncomeLoss"])
    op = _annual(f, ["OperatingIncomeLoss"])
    eq = _annual(f, ["StockholdersEquity"])
    liab = _annual(f, ["Liabilities"])
    ocf = _annual(f, ["NetCashProvidedByUsedInOperatingActivities"])
    capex = _annual(f, ["PaymentsToAcquirePropertyPlantAndEquipment"])
    eps = _annual(f, ["EarningsPerShareDiluted"], "USD/shares")

    def g(series):
        return (series[-1] / series[-2] - 1) if len(series) == 2 and series[-2] else None

    def ratio(a, b):
        return a[-1] / b[-1] if a and b and b[-1] else None

    out = {
        "revenue": rev[-1] if rev else None,
        "revenue_growth": g(rev),
        "net_margin": ratio(ni, rev),
        "op_margin": ratio(op, rev),
        "roe": ratio(ni, eq),
        "liab_to_equity": ratio(liab, eq),
        "eps": eps[-1] if eps else None,
        "eps_growth": g(eps),
        "fcf_margin": ((ocf[-1] - (capex[-1] if capex else 0)) / rev[-1]) if ocf and rev and rev[-1] else None,
    }
    return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in out.items()}


def update_fundamentals(symbols_ciks: dict, max_age_days=7, limit=10**6):
    """symbols_ciks: {symbol: cik}. Polite to SEC (≤ 8 requests/second)."""
    fresh = {r["symbol"] for r in db.query(
        "SELECT symbol FROM fundamentals WHERE updated > datetime('now', ?)", (f"-{max_age_days} days",))}
    n = tried = 0
    for sym, cik in symbols_ciks.items():
        if sym in fresh or not cik:
            continue
        if tried >= limit:
            break
        tried += 1
        data = sec_fundamentals(cik)       # stored even if empty, so failures aren't retried all week
        db.execute("INSERT OR REPLACE INTO fundamentals VALUES (?,?,?)",
                   (sym, datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"), json.dumps(data)))
        n += bool(data)
        time.sleep(0.15)
    return n


def get_fundamentals(symbol: str) -> dict:
    r = db.one("SELECT data FROM fundamentals WHERE symbol=?", (symbol,))
    return json.loads(r["data"]) if r else {}
