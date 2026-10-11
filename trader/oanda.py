"""OANDA connection for the forex desk (Alpaca has no forex). Uses OANDA's free PRACTICE account by default.

How the rest of the bot sees forex: every pair is two long-only instruments - "EUR_USD" (buy euros) and
"EUR_USD_SHORT" (EUR/USD flipped upside down, i.e. 1 / price, so buying it = selling euros). That way the
simulator, the brains, the shadow test and the manager reviews all work exactly as for stocks and crypto.
Only this file translates "_SHORT" into a real sell order and flips the prices back.

Rules enforced here:
  - every order goes in with its stop-loss and take-profit attached at OANDA (protected even if this PC is off)
  - forex trades Sunday 5pm -> Friday 5pm New York time: no new trades after Friday noon, and every
    position is closed by Friday 4pm, so nothing is ever held through the weekend gap
  - a US OANDA account can't hold a long and a short on the same pair at once, so the desk never tries
  - practice account unless OANDA_ENV=live AND I_UNDERSTAND_LIVE_TRADING_RISK=true"""
import json
import threading
import time
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests

from . import config, db, log
from .timeutil import now_et

L = log.get("oanda")
_lock = threading.RLock()
URLS = {"practice": "https://api-fxpractice.oanda.com/v3", "live": "https://api-fxtrade.oanda.com/v3"}


# ---------------------------------------------------------------- instruments
def pair_of(sym: str) -> str:
    return sym[:-6] if sym.endswith("_SHORT") else sym


def is_short(sym: str) -> bool:
    return sym.endswith("_SHORT")


def _digits(pair):
    return 3 if "JPY" in pair else 5


def usd_notional(sym, qty, bot_price) -> float:
    """Dollar size of a position. USD_xxx pairs are counted in dollars already; xxx_USD pairs in the other currency."""
    pair = pair_of(sym)
    if pair.startswith("USD_"):
        return float(qty)
    pair_price = 1 / bot_price if is_short(sym) else bot_price
    return float(qty) * pair_price


# ---------------------------------------------------------------- trading hours (New York time)
def market_open(t=None) -> bool:
    t = t or now_et()
    wd, h = t.weekday(), t.hour + t.minute / 60
    return not ((wd == 4 and h >= 17) or wd == 5 or (wd == 6 and h < 17))


def entries_allowed(t=None) -> bool:
    """No new positions from Friday noon: they'd be cut short by the Friday 4pm flatten anyway."""
    t = t or now_et()
    return market_open(t) and not (t.weekday() == 4 and t.hour >= 12)


def flatten_now(t=None) -> bool:
    t = t or now_et()
    return t.weekday() == 4 and t.hour >= 16


# ---------------------------------------------------------------- REST client
class Oanda:
    def __init__(self):
        if not config.OANDA_TOKEN:
            raise RuntimeError("No OANDA_API_TOKEN in .env - the forex desk needs a free OANDA practice account")
        live = config.OANDA_ENV == "live"
        if live and not config.ALLOW_LIVE:
            raise SystemExit("OANDA_ENV=live but I_UNDERSTAND_LIVE_TRADING_RISK is not true - refusing real money.")
        self.base = URLS["live" if live else "practice"]
        self.s = requests.Session()
        self.s.headers.update({"Authorization": f"Bearer {config.OANDA_TOKEN}", "Content-Type": "application/json",
                               "Accept-Datetime-Format": "RFC3339"})
        self._acct = config.OANDA_ACCOUNT_ID or None

    def _req(self, method, path, **kw):
        last = None
        for attempt in range(4):
            try:
                r = self.s.request(method, self.base + path, timeout=30, **kw)
            except requests.RequestException as e:
                last = e
                time.sleep(2 * (attempt + 1))
                continue
            if r.status_code == 429 or r.status_code >= 500:
                last = RuntimeError(f"OANDA {r.status_code}")
                time.sleep(2 * (attempt + 1))
                continue
            if r.status_code >= 400:
                raise RuntimeError(f"OANDA {method} {path} -> {r.status_code}: {r.text[:300]}")
            return r.json()
        raise RuntimeError(f"OANDA {method} {path} failed: {last}")

    @property
    def account_id(self) -> str:
        if not self._acct:
            accts = self._req("GET", "/accounts").get("accounts") or []
            if not accts:
                raise RuntimeError("This OANDA token has no accounts")
            self._acct = accts[0]["id"]
        return self._acct

    def summary(self) -> dict:
        a = self._req("GET", f"/accounts/{self.account_id}/summary")["account"]
        return {"nav": float(a["NAV"]), "balance": float(a["balance"]), "upl": float(a["unrealizedPL"]),
                "margin_used": float(a["marginUsed"]), "currency": a["currency"],
                "open_trades": int(a["openTradeCount"])}

    def candles(self, pair, start: datetime, gran="H1") -> pd.DataFrame:
        """Completed mid-price candles from `start` until now."""
        rows, t = [], start
        while True:
            data = self._req("GET", f"/instruments/{pair}/candles",
                             params={"granularity": gran, "price": "M", "count": 5000,
                                     "from": t.strftime("%Y-%m-%dT%H:%M:%SZ")}).get("candles", [])
            for c in data:
                if c.get("complete"):
                    m = c["mid"]
                    rows.append((pd.Timestamp(c["time"]), float(m["o"]), float(m["h"]), float(m["l"]), float(m["c"]),
                                 float(c.get("volume", 0))))
            if len(data) < 5000:
                break
            t = pd.Timestamp(data[-1]["time"]).to_pydatetime() + timedelta(seconds=1)
        return pd.DataFrame(rows, columns=["ts", "o", "h", "l", "c", "v"])

    def prices(self, pairs) -> dict:
        r = self._req("GET", f"/accounts/{self.account_id}/pricing", params={"instruments": ",".join(pairs)})
        out = {}
        for p in r.get("prices", []):
            bid, ask = float(p["bids"][0]["price"]), float(p["asks"][0]["price"])
            out[p["instrument"]] = {"bid": bid, "ask": ask, "mid": (bid + ask) / 2, "tradeable": p.get("tradeable", True)}
        return out

    def market_order(self, pair, units: int, sl: float, tp: float, comment="") -> dict:
        d = _digits(pair)
        body = {"order": {"type": "MARKET", "instrument": pair, "units": str(int(units)), "timeInForce": "FOK",
                          "positionFill": "DEFAULT",
                          "stopLossOnFill": {"price": f"{sl:.{d}f}", "timeInForce": "GTC"},
                          "takeProfitOnFill": {"price": f"{tp:.{d}f}", "timeInForce": "GTC"},
                          "clientExtensions": {"tag": "stocksbot", "comment": comment[:120]}}}
        r = self._req("POST", f"/accounts/{self.account_id}/orders", json=body)
        fill = r.get("orderFillTransaction") or {}
        opened = fill.get("tradeOpened")
        if not opened:
            why = (r.get("orderCancelTransaction") or {}).get("reason") or json.dumps(r)[:200]
            raise RuntimeError(f"OANDA did not open the trade: {why}")
        return {"trade_id": opened["tradeID"], "price": float(fill["price"]), "units": float(opened["units"])}

    def close_trade(self, trade_id) -> dict:
        r = self._req("PUT", f"/accounts/{self.account_id}/trades/{trade_id}/close")
        fill = r.get("orderFillTransaction") or {}
        return {"price": float(fill.get("price") or 0), "pl": float(fill.get("pl") or 0), "time": fill.get("time")}

    def trade(self, trade_id) -> dict:
        return self._req("GET", f"/accounts/{self.account_id}/trades/{trade_id}")["trade"]


# ---------------------------------------------------------------- price history
def update_forex(client: Oanda, pairs=None) -> int:
    """Hourly candles for each pair, stored twice: as the pair and as its flipped "_SHORT" twin."""
    pairs = pairs or config.FOREX_PAIRS
    desk = config.DESKS["forex"]
    last = db.last_bar_ts("1Hour")
    now = datetime.now(timezone.utc)
    n = 0
    for pair in pairs:
        start = (datetime.fromisoformat(last[pair]) - timedelta(days=3)) if pair in last else now - timedelta(days=desk.history_days)
        if pair not in last:
            L.info("Downloading hourly %s history since %s", pair, start.date())
        try:
            df = client.candles(pair, start)
        except Exception as e:
            L.warning("forex candles for %s failed: %s", pair, e)
            continue
        if df.empty:
            continue
        inv = pd.DataFrame({"ts": df["ts"], "o": 1 / df["o"], "h": 1 / df["l"], "l": 1 / df["h"], "c": 1 / df["c"],
                            "v": df["v"]})
        db.save_bars(df.assign(symbol=pair)[["symbol", "ts", "o", "h", "l", "c", "v"]], "1Hour")
        db.save_bars(inv.assign(symbol=pair + "_SHORT")[["symbol", "ts", "o", "h", "l", "c", "v"]], "1Hour")
        n += len(df)
    return n


# ---------------------------------------------------------------- the desk's broker
class ForexBroker:
    """Same job as broker.Broker, for the forex desk: orders, the shared position ledger, and syncing fills."""

    def __init__(self):
        self.api = Oanda()

    @staticmethod
    def ledger(statuses=("pending", "open", "closing")) -> list:
        q = f"SELECT * FROM positions WHERE desk='forex' AND status IN ({','.join('?' * len(statuses))})"
        return db.query(q, list(statuses))

    def account(self) -> dict:
        s = self.api.summary()
        budget = min(s["nav"], config.FOREX_BUDGET)
        return {"equity": budget, "nav": s["nav"], "upl": s["upl"], "currency": s["currency"]}

    def bot_prices(self, syms=None) -> dict:
        """Current mid prices in the bot's terms (flipped for _SHORT instruments)."""
        syms = syms or config.FOREX_UNIVERSE
        px = self.api.prices(sorted({pair_of(s) for s in syms}))
        out = {}
        for s in syms:
            p = px.get(pair_of(s))
            if p and p["mid"] > 0:
                out[s] = 1 / p["mid"] if is_short(s) else p["mid"]
        return out

    def exposure(self) -> float:
        return sum(usd_notional(p["symbol"], p["qty"], p["entry_price"]) for p in self.ledger())

    def held_pairs(self) -> set:
        return {pair_of(p["symbol"]) for p in self.ledger()}

    def buy(self, sym, notional_usd, stop_pct, target_pct, meta: dict) -> dict:
        if config.KILL_SWITCH.exists():
            raise RuntimeError("Kill switch file STOP_TRADING present - no new trades")
        pair, short = pair_of(sym), is_short(sym)
        if "USD" not in pair:
            raise RuntimeError(f"{pair}: only USD pairs are supported")
        with _lock:
            if pair in self.held_pairs():
                raise RuntimeError(f"{pair} is already held (US accounts can't be long and short the same pair)")
            mid = self.api.prices([pair])[pair]["mid"]
            units = int(notional_usd if pair.startswith("USD_") else notional_usd / mid)
            if units < 1:
                raise RuntimeError("position too small")
            entry = 1 / mid if short else mid                       # in the bot's (flipped if short) terms
            stop_b, target_b = entry * (1 - stop_pct), entry * (1 + target_pct)
            sl, tp = (1 / stop_b, 1 / target_b) if short else (stop_b, target_b)
            res = self.api.market_order(pair, -units if short else units, sl, tp, comment=f"{sym} {meta.get('genome_id', '')}")
            fill = 1 / res["price"] if short else res["price"]
            db.execute(
                "INSERT INTO positions (desk, symbol, status, qty, entry_price, entry_time, stop_price, target_price, "
                "stop_pct, exit_deadline, entry_order_id, leg_ids, genome_id, score, llm_json, features) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("forex", sym, "open", abs(res["units"]), fill, db.now_iso(), stop_b, target_b, stop_pct,
                 meta.get("deadline"), res["trade_id"], "[]", meta.get("genome_id"), meta.get("score"),
                 json.dumps(meta.get("llm")), json.dumps(meta.get("features"))))
        L.info("[forex] %s %s x%d @ %.5f  stop %.5f  target %.5f", "SELL" if short else "BUY", pair, units,
               res["price"], sl, tp)
        return {"trade_id": res["trade_id"], "units": units, "price": res["price"]}

    def close_position(self, pos: dict, reason: str):
        try:
            r = self.api.close_trade(pos["entry_order_id"])
        except Exception as e:
            L.warning("[forex] closing %s failed (%s) - checking whether it already closed", pos["symbol"], e)
            self.sync()
            return None
        if r["price"]:
            exit_b = 1 / r["price"] if is_short(pos["symbol"]) else r["price"]
            self._finalize(pos, reason, exit_b, r["pl"], r["time"])
        return pos["id"]

    def flatten_all(self, reason):
        for p in self.ledger(("open",)):
            self.close_position(p, reason)

    def sync(self) -> list:
        """Catch stops / targets OANDA filled on its own. Returns newly closed position ids."""
        closed = []
        for pos in self.ledger(("open", "closing")):
            try:
                t = self.api.trade(pos["entry_order_id"])
            except Exception as e:
                L.warning("[forex] sync issue for %s: %s", pos["symbol"], e)
                continue
            if t.get("state") != "CLOSED":
                continue
            kind = ("target" if (t.get("takeProfitOrder") or {}).get("state") == "FILLED" else
                    "stop" if (t.get("stopLossOrder") or {}).get("state") == "FILLED" else pos["exit_reason"] or "closed")
            px = float(t.get("averageClosePrice") or 0)
            exit_b = (1 / px if is_short(pos["symbol"]) else px) if px else pos["entry_price"]
            self._finalize(pos, kind, exit_b, float(t.get("realizedPL") or 0), t.get("closeTime"))
            closed.append(pos["id"])
        return closed

    @staticmethod
    def _finalize(pos, reason, exit_b, pl_usd, when):
        pnl_pct = exit_b / pos["entry_price"] - 1 if pos["entry_price"] else 0
        when = pd.Timestamp(when).to_pydatetime().isoformat() if when else db.now_iso()
        db.execute("UPDATE positions SET status='closed', exit_price=?, exit_time=?, exit_reason=?, pnl=?, pnl_pct=? "
                   "WHERE id=? AND status != 'closed'", (exit_b, when, reason, pl_usd, pnl_pct, pos["id"]))
        L.info("[forex] CLOSED %s %s  %+.2f%%  ($%+.2f)", pos["symbol"], reason, pnl_pct * 100, pl_usd)
