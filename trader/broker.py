"""Alpaca orders + the shared position ledger. Every entry is a BRACKET order
(entry + stop-loss + take-profit held on Alpaca's servers), so protection
stays in place even if this PC crashes. Each position belongs to exactly one desk."""
import json
import threading
import time
import uuid
from datetime import datetime, timezone

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderClass, OrderSide, QueryOrderStatus, TimeInForce
from alpaca.trading.requests import (GetOrderByIdRequest, GetOrdersRequest, MarketOrderRequest, StopLimitOrderRequest,
                                     StopLossRequest, TakeProfitRequest)

from . import config, db, log

L = log.get("broker")
_lock = threading.RLock()


def _f(x, d=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


class Broker:
    def __init__(self):
        config.validate()
        self.client = TradingClient(config.ALPACA_API_KEY, config.ALPACA_SECRET_KEY, paper=config.ALPACA_PAPER)
        self._clock = (0, None)

    # ------------------------------------------------------------ account
    def account(self) -> dict:
        a = self.client.get_account()
        return {"equity": _f(a.equity), "cash": _f(a.cash), "last_equity": _f(a.last_equity),
                "buying_power": _f(a.buying_power), "daytrade_count": int(a.daytrade_count or 0),
                "blocked": bool(a.trading_blocked)}

    def clock(self) -> dict:
        if time.time() - self._clock[0] < 30 and self._clock[1]:
            return self._clock[1]
        c = self.client.get_clock()
        out = {"is_open": bool(c.is_open), "next_open": c.next_open, "next_close": c.next_close}
        self._clock = (time.time(), out)
        return out

    def alpaca_positions(self) -> dict:
        def sym(p):   # Alpaca reports crypto positions as BTCUSD; everywhere else we use BTC/USD
            s = p.symbol
            if "crypto" in str(p.asset_class).lower() and "/" not in s and s.endswith("USD"):
                s = s[:-3] + "/USD"
            return s
        return {sym(p): {"qty": _f(p.qty), "avg": _f(p.avg_entry_price), "price": _f(p.current_price),
                         "value": _f(p.market_value), "upl": _f(p.unrealized_pl),
                         "uplpc": _f(p.unrealized_plpc)}
                for p in self.client.get_all_positions()}

    def open_orders(self) -> list:
        return self.client.get_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN, nested=True, limit=500))

    def order(self, order_id):
        return self.client.get_order_by_id(order_id, GetOrderByIdRequest(nested=True))

    # ------------------------------------------------------------ ledger
    @staticmethod
    def ledger(desk=None, statuses=("pending", "open", "closing")) -> list:
        q = f"SELECT * FROM positions WHERE status IN ({','.join('?' * len(statuses))})"
        p = list(statuses)
        if desk:
            q += " AND desk=?"
            p.append(desk)
        return db.query(q, p)

    @staticmethod
    def owner(symbol):
        r = db.one("SELECT desk FROM positions WHERE symbol=? AND status IN ('pending','open','closing')", (symbol,))
        return r["desk"] if r else None

    # ------------------------------------------------------------ orders
    def buy_bracket(self, desk, symbol, qty, price, stop_pct, target_pct, meta: dict) -> dict:
        if config.KILL_SWITCH.exists():
            raise RuntimeError("Kill switch file STOP_TRADING present - no new buys")
        stop_price = round(price * (1 - stop_pct), 2)
        target_price = round(price * (1 + target_pct), 2)
        if target_price < price + 0.02:
            target_price = round(price + 0.02, 2)
        if stop_price > price - 0.02:
            stop_price = round(price - 0.02, 2)
        tif = TimeInForce.DAY if desk == "day" else TimeInForce.GTC
        coid = f"{desk}-{symbol}-{uuid.uuid4().hex[:10]}"[:48]
        req = MarketOrderRequest(symbol=symbol, qty=int(qty), side=OrderSide.BUY, time_in_force=tif,
                                 order_class=OrderClass.BRACKET, client_order_id=coid,
                                 take_profit=TakeProfitRequest(limit_price=target_price),
                                 stop_loss=StopLossRequest(stop_price=stop_price))
        with _lock:
            if self.owner(symbol):
                raise RuntimeError(f"{symbol} already held by the {self.owner(symbol)} desk")
            o = self.client.submit_order(req)
            cur = db.execute(
                "INSERT INTO positions (desk, symbol, status, qty, entry_price, entry_time, stop_price, "
                "target_price, stop_pct, exit_deadline, entry_order_id, leg_ids, genome_id, score, llm_json, features) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (desk, symbol, "pending", int(qty), price, db.now_iso(), stop_price, target_price, stop_pct,
                 meta.get("deadline"), str(o.id), "[]", meta.get("genome_id"), meta.get("score"),
                 json.dumps(meta.get("llm")), json.dumps(meta.get("features"))))
        L.info("[%s] BUY %s x%d @~%.2f  stop %.2f  target %.2f", desk, symbol, qty, price, stop_price, target_price)
        return {"id": cur.lastrowid, "order_id": str(o.id)}

    def buy_crypto(self, symbol, notional, price, stop_pct, target_pct, meta: dict) -> dict:
        """Crypto can't use bracket orders on Alpaca: buy at market now; once filled, sync() places a protective
        stop order at Alpaca, and the crypto trader sells at the target / time limit itself (it runs 24/7)."""
        if config.KILL_SWITCH.exists():
            raise RuntimeError("Kill switch file STOP_TRADING present - no new buys")
        qty = round(notional / price, 6)
        coid = f"crypto-{symbol.replace('/', '')}-{uuid.uuid4().hex[:10]}"
        req = MarketOrderRequest(symbol=symbol, qty=qty, side=OrderSide.BUY, time_in_force=TimeInForce.GTC,
                                 client_order_id=coid)
        with _lock:
            if self.owner(symbol):
                raise RuntimeError(f"{symbol} already held by the {self.owner(symbol)} desk")
            o = self.client.submit_order(req)
            cur = db.execute(
                "INSERT INTO positions (desk, symbol, status, qty, entry_price, entry_time, stop_price, "
                "target_price, stop_pct, exit_deadline, entry_order_id, leg_ids, genome_id, score, llm_json, features) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("crypto", symbol, "pending", qty, price, db.now_iso(), round(price * (1 - stop_pct), 6),
                 round(price * (1 + target_pct), 6), stop_pct, meta.get("deadline"), str(o.id), "[]",
                 meta.get("genome_id"), meta.get("score"), json.dumps(meta.get("llm")), json.dumps(meta.get("features"))))
        L.info("[crypto] BUY %s x%s @~%.4f  stop -%.1f%%  target +%.1f%%", symbol, qty, price, stop_pct * 100,
               target_pct * 100)
        return {"id": cur.lastrowid, "order_id": str(o.id)}

    def _crypto_protect(self, pos, filled_qty, fill_price):
        """After a crypto buy fills: record the real quantity held (fees come out of the coins) and park a
        stop-limit sell at Alpaca so the downside is protected even if this PC is off."""
        held = self.alpaca_positions().get(pos["symbol"], {}).get("qty") or filled_qty
        stop_price = round(fill_price * (1 - pos["stop_pct"]), 6 if fill_price < 10 else 2)
        target_price = round(fill_price * (1 + (pos["target_price"] / pos["entry_price"] - 1)), 6 if fill_price < 10 else 2)
        legs = []
        try:
            so = self.client.submit_order(StopLimitOrderRequest(
                symbol=pos["symbol"], qty=held, side=OrderSide.SELL, time_in_force=TimeInForce.GTC,
                stop_price=stop_price, limit_price=round(stop_price * 0.99, 6 if fill_price < 10 else 2)))
            legs = [str(so.id)]
        except Exception as e:
            L.warning("[crypto] could not place protective stop for %s: %s (the bot will watch it instead)",
                      pos["symbol"], e)
        db.execute("UPDATE positions SET status='open', qty=?, entry_price=?, entry_time=?, stop_price=?, "
                   "target_price=?, leg_ids=? WHERE id=?",
                   (held, fill_price, db.now_iso(), stop_price, target_price, json.dumps(legs), pos["id"]))

    def close_position(self, pos: dict, reason: str):
        """Cancel the bracket's protective legs, then sell at market."""
        with _lock:
            for lid in json.loads(pos.get("leg_ids") or "[]"):
                try:
                    self.client.cancel_order_by_id(lid)
                except Exception:
                    pass
            # cancelling legs can take a moment to release the shares
            for _ in range(10):
                held = [o for o in self.open_orders() if o.symbol == pos["symbol"] and o.side == OrderSide.SELL]
                if not held:
                    break
                for o in held:          # one desk per symbol, so any open sell here is ours
                    try:
                        self.client.cancel_order_by_id(o.id)
                    except Exception:
                        pass
                time.sleep(1)
            alp = self.alpaca_positions().get(pos["symbol"])
            qty = min(pos["qty"], alp["qty"]) if alp else 0
            if qty <= 0:
                db.execute("UPDATE positions SET status='open' WHERE id=?", (pos["id"],))
                return None
            crypto = "/" in pos["symbol"]
            req = MarketOrderRequest(symbol=pos["symbol"], qty=qty, side=OrderSide.SELL,
                                     # stocks: DAY is required for fractional shares; crypto only allows GTC/IOC
                                     time_in_force=TimeInForce.GTC if crypto else TimeInForce.DAY,
                                     client_order_id=f"{pos['desk']}-x-{pos['symbol'].replace('/', '')}-{uuid.uuid4().hex[:8]}"[:48])
            o = self.client.submit_order(req)
            db.execute("UPDATE positions SET status='closing', exit_order_id=?, exit_reason=? WHERE id=?",
                       (str(o.id), reason, pos["id"]))
        L.info("[%s] SELL %s x%s (%s)", pos["desk"], pos["symbol"], qty, reason)
        return str(o.id)

    # ------------------------------------------------------------ sync
    def sync(self, desk):
        """Bring the ledger up to date with what actually happened at Alpaca:
        fills, stop-outs, targets hit. Returns newly closed position ids."""
        closed = []
        alp = None
        for pos in self.ledger(desk):
            try:
                if pos["status"] == "pending":
                    o = self.order(pos["entry_order_id"])
                    st = str(o.status.value if hasattr(o.status, "value") else o.status)
                    if pos["desk"] == "crypto" and (st == "filled" or (_f(o.filled_qty) > 0 and st in ("canceled", "expired"))):
                        self._crypto_protect(pos, _f(o.filled_qty), _f(o.filled_avg_price))
                    elif st == "filled" or (_f(o.filled_qty) > 0 and st in ("canceled", "expired")):
                        legs = [str(l.id) for l in (o.legs or [])]
                        db.execute("UPDATE positions SET status='open', qty=?, entry_price=?, entry_time=?, "
                                   "leg_ids=? WHERE id=?",
                                   (_f(o.filled_qty), _f(o.filled_avg_price), (o.filled_at or datetime.now(timezone.utc)).isoformat(),
                                    json.dumps(legs), pos["id"]))
                    elif st in ("canceled", "expired", "rejected"):
                        db.execute("UPDATE positions SET status='cancelled', exit_reason=? WHERE id=?", (st, pos["id"]))
                elif pos["status"] == "open":
                    if alp is None:
                        alp = self.alpaca_positions()
                    held = alp.get(pos["symbol"])
                    if held and held["qty"] >= pos["qty"] * 0.999:
                        continue            # still fully held - nothing to check
                    hit = None
                    for lid in json.loads(pos["leg_ids"] or "[]"):
                        lo = self.order(lid)
                        if str(lo.status.value if hasattr(lo.status, "value") else lo.status) == "filled":
                            kind = "target" if lo.limit_price and not lo.stop_price else "stop"
                            hit = (kind, _f(lo.filled_avg_price), lo.filled_at)
                            break
                    if hit is None and not held:
                        hit = ("gone", None, None)
                    if hit:
                        self._finalize(pos, hit[0], hit[1], hit[2])
                        closed.append(pos["id"])
                elif pos["status"] == "closing":
                    o = self.order(pos["exit_order_id"])
                    st = str(o.status.value if hasattr(o.status, "value") else o.status)
                    if st == "filled":
                        self._finalize(pos, pos["exit_reason"] or "exit", _f(o.filled_avg_price), o.filled_at)
                        closed.append(pos["id"])
                    elif st in ("canceled", "expired", "rejected"):
                        db.execute("UPDATE positions SET status='open' WHERE id=?", (pos["id"],))
            except Exception as e:
                L.warning("[%s] sync issue for %s: %s", desk, pos["symbol"], e)
        return closed

    def _finalize(self, pos, reason, price, when):
        if not price:
            # Closed outside the bot (or manually in the Alpaca app): use the last fill we can find.
            try:
                orders = self.client.get_orders(GetOrdersRequest(status=QueryOrderStatus.CLOSED,
                                                                 symbols=[pos["symbol"]], limit=20))
                sells = [o for o in orders if o.side == OrderSide.SELL and o.filled_avg_price]
                price = _f(sells[0].filled_avg_price) if sells else pos["entry_price"]
                when = sells[0].filled_at if sells else None
            except Exception:
                price = pos["entry_price"]
        pnl = (price - pos["entry_price"]) * pos["qty"]
        pnl_pct = price / pos["entry_price"] - 1 if pos["entry_price"] else 0
        db.execute("UPDATE positions SET status='closed', exit_price=?, exit_time=?, exit_reason=?, pnl=?, "
                   "pnl_pct=? WHERE id=?",
                   (price, (when or datetime.now(timezone.utc)).isoformat(), reason, pnl, pnl_pct, pos["id"]))
        L.info("[%s] CLOSED %s %s  %+.2f%%  ($%+.2f)", pos["desk"], pos["symbol"], reason, pnl_pct * 100, pnl)
        db.event(f"{pos['desk']}_trader", f"CLOSED {pos['symbol']} ({pos['desk']} desk, {reason}): "
                                          f"{pnl_pct * 100:+.2f}%  ${pnl:+,.2f}")
