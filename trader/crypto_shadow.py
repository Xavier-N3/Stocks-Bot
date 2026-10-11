"""Clearance for the strict desks (crypto and forex): the path a brain must walk before it may trade.

  1. training    - the lab keeps evolving brains until one passes the strict backtest bar: validation
                   (used to pick champions) AND a final exam on the newest data (never used for picking)
  2. shadow test - that brain "trades" on paper inside the bot, on LIVE prices that didn't exist when it was
                   chosen (so it can't have been overfit to them). Needs >= 15 trades over >= 7 days, net
                   profitable, win rate >= 50%.
  3. cleared     - real (paper-account) trades start at HALF size for the first 10 trades
  4. revoked     - if real trades go wrong (net loss after 5+ trades, or 3 losses in a row), clearance is
                   withdrawn and the brain goes back to shadow testing.
A new champion brain always starts again from step 2."""
from datetime import datetime, timedelta, timezone

from . import config, db

SHADOW_MIN_TRADES = config._i("CRYPTO_SHADOW_MIN_TRADES", 15)
SHADOW_MIN_DAYS = config._f("CRYPTO_SHADOW_MIN_DAYS", 7)
SHADOW_MIN_WINRATE = config._f("CRYPTO_SHADOW_MIN_WINRATE", 0.5)
HALF_SIZE_TRADES = 10


def val_ok(champ) -> bool:
    """Bar 1 (validation): a clear win on unseen data AND in training. The lab may pick brains by this."""
    if not champ:
        return False
    h, t = champ.get("holdout", {}), champ.get("train", {})
    return (h.get("trades", 0) >= 30 and h.get("sharpe", 0) >= 1.5 and h.get("winrate", 0) >= 0.55
            and h.get("total", 0) > 0 and h.get("maxdd", 1) <= 0.15 and t.get("sharpe", 0) >= 1.0)


def test_ok(champ) -> bool:
    """Bar 2 (final exam): the newest slice of history, which played no part in choosing the brain."""
    x = (champ or {}).get("test") or {}
    return (x.get("trades", 0) >= 20 and x.get("sharpe", 0) >= 1.0 and x.get("total", 0) > 0
            and x.get("maxdd", 1) <= 0.15)


def backtest_ok(champ) -> bool:
    """The strict backtest bar: both of the above."""
    return val_ok(champ) and test_ok(champ)


def _desk(champ_or_gid) -> str:
    gid = champ_or_gid if isinstance(champ_or_gid, str) else champ_or_gid["id"]
    return gid.split("-")[0]           # genome ids look like "crypto-202610110314-516b"


def _since(gid):
    rev = db.kv_get(f"{_desk(gid)}_revoked") or {}
    return rev.get("at", "") if rev.get("genome_id") == gid else ""


def shadow_stats(gid) -> dict:
    rows = db.query("SELECT ret, entry_ts FROM shadow_trades WHERE genome_id=? AND status='closed' AND entry_ts >= ? "
                    "ORDER BY entry_ts", (gid, _since(gid)))
    first = db.one("SELECT MIN(entry_ts) t FROM shadow_trades WHERE genome_id=? AND entry_ts >= ?", (gid, _since(gid)))
    days = 0.0
    if first and first["t"]:
        days = (datetime.now(timezone.utc) - datetime.fromisoformat(first["t"])).total_seconds() / 86400
    n = len(rows)
    wins = sum(1 for r in rows if r["ret"] > 0)
    return {"trades": n, "wins": wins, "winrate": wins / n if n else 0.0,
            "total": sum(r["ret"] for r in rows), "days": round(days, 1),
            "open": db.one("SELECT COUNT(*) n FROM shadow_trades WHERE genome_id=? AND status='open'", (gid,))["n"]}


def cleared(champ) -> bool:
    if not champ:
        return False
    c = db.kv_get(f"{_desk(champ)}_clearance") or {}
    return c.get("genome_id") == champ["id"]


def stage(champ) -> dict:
    if not champ or not backtest_ok(champ):
        why = ("the best brain passed validation but failed the final exam on the newest data" if val_ok(champ)
               else "no brain has passed the strict backtest bar yet")
        return {"stage": "training", "label": f"Training - {why}", "genome_id": champ["id"] if champ else None}
    s = shadow_stats(champ["id"])
    if cleared(champ):
        real = db.one("SELECT COUNT(*) n FROM positions WHERE desk=? AND genome_id=? AND status='closed'",
                      (_desk(champ), champ["id"]))["n"]
        half = real < HALF_SIZE_TRADES
        return {"stage": "cleared", "genome_id": champ["id"], "shadow": s, "real_trades": real, "half_size": half,
                "label": f"Cleared to trade{' (half size for its first ' + str(HALF_SIZE_TRADES) + ' trades)' if half else ''}"}
    return {"stage": "shadow", "genome_id": champ["id"], "shadow": s,
            "label": f"Shadow testing: {s['trades']}/{SHADOW_MIN_TRADES} trades over {s['days']:.0f}/"
                     f"{SHADOW_MIN_DAYS:.0f} days, {s['total'] * 100:+.1f}% so far (no real money used)"}


def run(champ, scored, prices: dict, max_open: int):
    """Called by the desk's trader every cycle: manage paper exits, open paper entries, maybe clear the brain.
    scored=None means "no new entries right now" (e.g. forex on a Friday afternoon)."""
    now = datetime.now(timezone.utc)
    desk = _desk(champ)
    cost = 2 * config.DESKS[desk].cost_per_side
    # a brain of this desk that's no longer champion: its open paper trades are void
    db.execute("UPDATE shadow_trades SET status='void' WHERE status='open' AND genome_id LIKE ? AND genome_id != ?",
               (f"{desk}-%", champ["id"]))
    for t in db.query("SELECT * FROM shadow_trades WHERE status='open' AND genome_id=?", (champ["id"],)):
        px = prices.get(t["symbol"])
        if not px:
            continue
        r = px / t["entry_price"] - 1
        reason = ("target" if r >= t["target_pct"] else "stop" if r <= -t["stop_pct"]
                  else "max holding time" if t["deadline"] <= now.isoformat() else None)
        if reason:
            db.execute("UPDATE shadow_trades SET status='closed', exit_ts=?, exit_price=?, ret=?, exit_reason=? WHERE id=?",
                       (now.isoformat(), px, r - cost, reason, t["id"]))
    if scored is not None and not scored.empty:
        mkt = lambda s: s.replace("_SHORT", "")      # a forex pair and its short twin are one market
        open_syms = {mkt(r["symbol"]) for r in db.query("SELECT symbol FROM shadow_trades WHERE status='open' AND genome_id=?",
                                                        (champ["id"],))}
        slots = max_open - len(open_syms)
        for sym, r in scored[scored["score"] > scored["thr"]].iterrows():
            if slots <= 0:
                break
            if mkt(sym) in open_syms or not prices.get(sym):
                continue
            open_syms.add(mkt(sym))
            db.execute("INSERT INTO shadow_trades (genome_id, symbol, entry_ts, entry_price, stop_pct, target_pct, "
                       "deadline, status) VALUES (?,?,?,?,?,?,?, 'open')",
                       (champ["id"], sym, now.isoformat(), prices[sym], float(r["stop_pct"]), float(r["target_pct"]),
                        (now + timedelta(hours=int(r["hold"]))).isoformat()))
            slots -= 1
    # graduation
    if not cleared(champ):
        s = shadow_stats(champ["id"])
        if (s["trades"] >= SHADOW_MIN_TRADES and s["days"] >= SHADOW_MIN_DAYS and s["total"] > 0
                and s["winrate"] >= SHADOW_MIN_WINRATE):
            db.kv_set(f"{desk}_clearance", {"genome_id": champ["id"], "at": now.isoformat(), "shadow": s})
            db.event(f"{desk}_trader", f"{desk.title()} brain {champ['id']} passed shadow testing ({s['trades']} paper "
                                       f"trades, {s['total'] * 100:+.1f}%, {s['winrate']:.0%} wins) - cleared to trade at half size")


def check_revoke(champ):
    """Withdraw clearance if real trades go wrong."""
    if not cleared(champ):
        return
    desk = _desk(champ)
    rows = db.query("SELECT pnl_pct FROM positions WHERE desk=? AND genome_id=? AND status='closed' "
                    "ORDER BY exit_time DESC", (desk, champ["id"]))
    rets = [r["pnl_pct"] or 0 for r in rows]
    why = ""
    if len(rets) >= 3 and all(r < 0 for r in rets[:3]):
        why = "3 losing trades in a row"
    elif len(rets) >= 5 and sum(rets) < 0:
        why = f"net loss after {len(rets)} trades"
    if why:
        db.execute("DELETE FROM kv WHERE k=?", (f"{desk}_clearance",))
        db.kv_set(f"{desk}_revoked", {"genome_id": champ["id"], "at": datetime.now(timezone.utc).isoformat(), "why": why})
        db.event(f"{desk}_trader", f"{desk.title()} clearance withdrawn ({why}) - back to shadow testing, no real trades",
                 "warning")
