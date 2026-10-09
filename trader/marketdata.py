"""Market data from Alpaca's free plan: daily history (SIP, split+dividend
adjusted), 15-minute bars (IEX), live snapshots, top movers and news."""
import threading
import time
from datetime import datetime, timedelta, timezone

import pandas as pd
from alpaca.data.enums import Adjustment, DataFeed
from alpaca.data.historical import CryptoHistoricalDataClient, StockHistoricalDataClient
from alpaca.data.historical.news import NewsClient
from alpaca.data.historical.screener import ScreenerClient
from alpaca.data.requests import (CryptoBarsRequest, CryptoSnapshotRequest, MarketMoversRequest, MostActivesRequest, NewsRequest,
                                  StockBarsRequest, StockSnapshotRequest)
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import AssetClass, AssetStatus
from alpaca.trading.requests import GetAssetsRequest

from . import config, db, log
from .timeutil import ET

L = log.get("data")
TF = {"1Day": TimeFrame.Day, "15Min": TimeFrame(15, TimeFrameUnit.Minute), "1Hour": TimeFrame.Hour}
_daily_lock = threading.Lock()
_intraday_lock = threading.Lock()
_crypto_lock = threading.Lock()


def is_crypto(symbol: str) -> bool:
    return "/" in symbol


def _retry(fn, tries=3, wait=5):
    for i in range(tries):
        try:
            return fn()
        except Exception as e:  # network blips, 429s
            if i == tries - 1:
                raise
            L.warning("data call failed (%s), retrying", e)
            time.sleep(wait * (i + 1))


class MarketData:
    def __init__(self):
        k, s = config.ALPACA_API_KEY, config.ALPACA_SECRET_KEY
        self.hist = StockHistoricalDataClient(k, s)
        self.screener = ScreenerClient(k, s)
        self.crypto = CryptoHistoricalDataClient(k, s)
        self.news = NewsClient(k, s)
        self.trading = TradingClient(k, s, paper=config.ALPACA_PAPER)

    # ------------------------------------------------------------ bars
    def fetch_bars(self, symbols, tf, start, end=None, feed=DataFeed.SIP,
                   adjustment=Adjustment.ALL) -> pd.DataFrame:
        frames = []
        for i in range(0, len(symbols), 100):
            chunk = symbols[i:i + 100]
            req = StockBarsRequest(symbol_or_symbols=chunk, timeframe=TF[tf], start=start, end=end,
                                   feed=feed, adjustment=adjustment)
            try:
                df = _retry(lambda: self.hist.get_stock_bars(req).df)
            except Exception as e:
                L.warning("bars fetch failed for %d symbols (%s)", len(chunk), e)
                continue
            if df is None or df.empty:
                continue
            df = df.reset_index().rename(columns={"timestamp": "ts", "open": "o", "high": "h",
                                                  "low": "l", "close": "c", "volume": "v"})
            frames.append(df[["symbol", "ts", "o", "h", "l", "c", "v"]])
        if not frames:
            return pd.DataFrame(columns=["symbol", "ts", "o", "h", "l", "c", "v"])
        out = pd.concat(frames, ignore_index=True)
        out["ts"] = pd.to_datetime(out["ts"], utc=True)
        return out

    def update_daily(self, symbols, history_days=None, force_full=False) -> int:
        """Keep daily history current. Fetches only what is missing, and redoes
        everything once a week so dividend adjustments stay consistent."""
        history_days = history_days or max(d.history_days for d in config.DESKS.values())
        with _daily_lock:
            now = datetime.now(timezone.utc)
            end = now - timedelta(minutes=20)       # free plan: SIP data must be >15 min old
            full_start = now - timedelta(days=history_days)
            week = now.strftime("%G-W%V")
            if db.kv_get("daily_full_refresh") != week:
                force_full = True
            last = {} if force_full else db.last_bar_ts("1Day")
            new = [s for s in symbols if s not in last]
            old = [s for s in symbols if s in last]
            n = 0
            if new:
                L.info("Downloading %d years of daily history for %d symbols...", history_days // 365, len(new))
                df = self.fetch_bars(new, "1Day", full_start, end)
                db.save_bars(df, "1Day")
                n += len(df)
            if old:
                since = min(pd.Timestamp(last[s]) for s in old) - pd.Timedelta(days=5)
                df = self.fetch_bars(old, "1Day", since.to_pydatetime(), end)
                db.save_bars(df, "1Day")
                n += len(df)
            if force_full:
                db.kv_set("daily_full_refresh", week)
            db.kv_set("daily_updated", db.now_iso())
            return n

    def update_intraday(self, symbols, days=None) -> int:
        """15-minute bars (IEX feed, regular session only)."""
        days = days or config.DESKS["day"].history_days
        with _intraday_lock:
            now = datetime.now(timezone.utc)
            last = db.last_bar_ts("15Min")
            new = [s for s in symbols if s not in last]
            old = [s for s in symbols if s in last]
            n = 0
            for group, start in ((new, now - timedelta(days=days)),
                                 (old, now - timedelta(days=3))):
                if not group:
                    continue
                df = self.fetch_bars(group, "15Min", start, None, feed=DataFeed.IEX,
                                     adjustment=Adjustment.SPLIT)
                if df.empty:
                    continue
                et = df["ts"].dt.tz_convert(ET)
                mins = et.dt.hour * 60 + et.dt.minute
                df = df[(mins >= 570) & (mins <= 945)]    # 09:30 .. 15:45 bar starts
                db.save_bars(df, "15Min")
                n += len(df)
            return n

    def update_crypto(self, symbols, days=None) -> int:
        """Hourly crypto bars (24/7)."""
        days = days or config.DESKS["crypto"].history_days
        with _crypto_lock:
            now = datetime.now(timezone.utc)
            last = db.last_bar_ts("1Hour")
            new = [s for s in symbols if s not in last]
            old = [s for s in symbols if s in last]
            n = 0
            for group, start in ((new, now - timedelta(days=days)), (old, now - timedelta(days=3))):
                if not group:
                    continue
                try:
                    df = _retry(lambda: self.crypto.get_crypto_bars(
                        CryptoBarsRequest(symbol_or_symbols=group, timeframe=TF["1Hour"], start=start)).df)
                except Exception as e:
                    L.warning("crypto bars failed: %s", e)
                    continue
                if df is None or df.empty:
                    continue
                df = df.reset_index().rename(columns={"timestamp": "ts", "open": "o", "high": "h", "low": "l",
                                                      "close": "c", "volume": "v"})
                df["ts"] = pd.to_datetime(df["ts"], utc=True)
                db.save_bars(df[["symbol", "ts", "o", "h", "l", "c", "v"]], "1Hour")
                n += len(df)
            return n

    # ------------------------------------------------------------ live
    def crypto_snapshots(self, symbols) -> dict:
        out = {}
        try:
            snaps = _retry(lambda: self.crypto.get_crypto_snapshot(CryptoSnapshotRequest(symbol_or_symbols=symbols)))
        except Exception as e:
            L.warning("crypto snapshot failed: %s", e)
            return out
        for sym, s in snaps.items():
            if s is None or s.latest_trade is None:
                continue
            d, p = s.daily_bar, s.previous_daily_bar
            out[sym] = {"price": float(s.latest_trade.price), "time": s.latest_trade.timestamp,
                        "day_open": float(d.open) if d else None, "day_high": float(d.high) if d else None,
                        "day_low": float(d.low) if d else None, "day_volume": float(d.volume) if d else 0.0,
                        "prev_close": float(p.close) if p else None}
        return out

    def snapshots(self, symbols) -> dict:
        coins = [s for s in symbols if is_crypto(s)]
        out = self.crypto_snapshots(coins) if coins else {}
        symbols = [s for s in symbols if not is_crypto(s)]
        for i in range(0, len(symbols), 200):
            chunk = symbols[i:i + 200]
            try:
                snaps = _retry(lambda: self.hist.get_stock_snapshot(
                    StockSnapshotRequest(symbol_or_symbols=chunk, feed=DataFeed.IEX)))
            except Exception as e:
                L.warning("snapshot failed: %s", e)
                continue
            for sym, s in snaps.items():
                if s is None or s.latest_trade is None:
                    continue
                d, p = s.daily_bar, s.previous_daily_bar
                out[sym] = {
                    "price": float(s.latest_trade.price),
                    "time": s.latest_trade.timestamp,
                    "day_open": float(d.open) if d else None,
                    "day_high": float(d.high) if d else None,
                    "day_low": float(d.low) if d else None,
                    "day_volume": float(d.volume) if d else 0.0,
                    "prev_close": float(p.close) if p else None,
                }
        return out

    def most_actives(self, top=40) -> list:
        try:
            return [x.symbol for x in self.screener.get_most_actives(MostActivesRequest(top=top)).most_actives]
        except Exception as e:
            L.warning("most actives failed: %s", e)
            return []

    def movers(self, top=25) -> list:
        try:
            m = self.screener.get_market_movers(MarketMoversRequest(top=top))
            return [{"symbol": x.symbol, "pct": float(x.percent_change), "price": float(x.price)}
                    for x in list(m.gainers) + list(m.losers)]
        except Exception as e:
            L.warning("movers failed: %s", e)
            return []

    def tradable(self) -> set:
        cached = db.kv_get("tradable_assets")
        if cached and cached.get("day") == datetime.now(timezone.utc).date().isoformat():
            return set(cached["symbols"])
        try:
            assets = self.trading.get_all_assets(GetAssetsRequest(status=AssetStatus.ACTIVE,
                                                                  asset_class=AssetClass.US_EQUITY))
            syms = sorted(a.symbol for a in assets if a.tradable)
            db.kv_set("tradable_assets", {"day": datetime.now(timezone.utc).date().isoformat(), "symbols": syms})
            return set(syms)
        except Exception as e:
            L.warning("asset list failed: %s", e)
            return set(cached["symbols"]) if cached else set()

    # ------------------------------------------------------------ news
    def fetch_news(self, symbols, hours=24) -> int:
        start = datetime.now(timezone.utc) - timedelta(hours=hours)
        rows = []
        for i in range(0, len(symbols), 25):
            chunk = symbols[i:i + 25]
            ask = {s.replace("/", ""): s for s in chunk}          # crypto news uses BTCUSD, we use BTC/USD
            try:
                res = _retry(lambda: self.news.get_news(NewsRequest(symbols=",".join(ask), start=start,
                                                                    limit=50, exclude_contentless=False)))
            except Exception as e:
                L.warning("news failed: %s", e)
                continue
            items = res.data.get("news", []) if hasattr(res, "data") else []
            for n in items:
                for sym in n.symbols or []:
                    sym = ask.get(sym, sym)
                    if sym in chunk:
                        rows.append((f"alp{n.id}:{sym}", sym, n.created_at.isoformat(), n.source or "alpaca",
                                     n.headline, (n.summary or "")[:500], n.url or ""))
        if rows:
            db.executemany("INSERT OR IGNORE INTO news VALUES (?,?,?,?,?,?,?)", rows)
        return len(rows)
