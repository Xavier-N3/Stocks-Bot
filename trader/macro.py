"""Outside-the-chart market information for the crypto desk (all free, no keys):

  - Crypto Fear & Greed index (alternative.me), daily since 2018 -> a training feature for the crypto brain
  - Perpetual-futures funding rates (Hyperliquid), live -> extra context for the AI analyst / Challenger only.
    Funding history only starts in 2023, so it is NOT a brain input: training on it would either throw
    away the 2021-22 crash years or hand the brain a fake "which era is this?" marker."""
import json
import time
import urllib.request
from datetime import datetime, timezone

import pandas as pd

from . import db, log

L = log.get("macro")
_UA = {"User-Agent": "stocksbot/1.0", "Content-Type": "application/json"}


def _get(url, payload=None, timeout=20):
    data = json.dumps(payload).encode() if payload is not None else None
    with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=_UA), timeout=timeout) as r:
        return json.loads(r.read())


def update_fng(max_age_s=6 * 3600) -> int:
    """Refresh the daily Fear & Greed history (at most every few hours)."""
    last = db.kv_get("fng_updated_at") or 0
    have = db.one("SELECT COUNT(*) n FROM macro WHERE series='fng'")["n"]
    if have and time.time() - last < max_age_s:
        return 0
    try:
        rows = _get(f"https://api.alternative.me/fng/?limit={0 if have < 100 else 30}&format=json")["data"]
    except Exception as e:
        L.warning("fear & greed update failed: %s", e)
        return 0
    recs = [("fng", datetime.fromtimestamp(int(r["timestamp"]), timezone.utc).isoformat(), float(r["value"]))
            for r in rows]
    db.executemany("INSERT OR REPLACE INTO macro (series, ts, value) VALUES (?,?,?)", recs)
    db.kv_set("fng_updated_at", time.time())
    return len(recs)


def fng_series() -> pd.DataFrame:
    """Daily Fear & Greed: ts (start of the UTC day it describes), value 0-100."""
    rows = db.query("SELECT ts, value FROM macro WHERE series='fng' ORDER BY ts")
    if not rows:
        return pd.DataFrame(columns=["ts", "value"])
    df = pd.DataFrame(rows)
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    return df


def latest_fng():
    r = db.one("SELECT ts, value FROM macro WHERE series='fng' ORDER BY ts DESC LIMIT 1")
    return r["value"] if r else None


def funding_now(max_age_s=1800) -> dict:
    """Current hourly funding rate per coin on Hyperliquid perps, e.g. {"BTC": 0.0000125}. Positive = longs pay
    shorts (crowded long). Cached for half an hour; empty dict if the source is unreachable."""
    cached = db.kv_get("funding_now") or {}
    if cached and time.time() - cached.get("t", 0) < max_age_s:
        return cached.get("rates", {})
    try:
        meta, ctxs = _get("https://api.hyperliquid.xyz/info", {"type": "metaAndAssetCtxs"})
        rates = {a["name"]: float(c["funding"]) for a, c in zip(meta["universe"], ctxs) if c.get("funding") is not None}
    except Exception as e:
        L.warning("funding rate update failed: %s", e)
        return cached.get("rates", {})
    db.kv_set("funding_now", {"t": time.time(), "rates": rates})
    return rates
